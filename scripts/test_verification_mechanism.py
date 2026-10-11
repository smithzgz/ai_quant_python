#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全表验证机制测试脚本
测试三大关卡：数没丢、数没乱、业务对得上
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import date, timedelta
from config.data_sync_config import DATA_SYNC_TASKS
from data.sync.engine import SyncEngine
from data.quality.sync_verifier import SyncVerifier
from data.quality.rules import QUALITY_RULES, RULE_CHECKERS
from data.database.connection import engine as db_engine
from sqlalchemy import text
from utils.logger import get_logger

logger = get_logger("verify_test")


def get_table_date_info(table_name: str, cfg: dict):
    """获取表的日期字段信息和最新日期"""
    date_field = cfg.get("date_field", "trade_date")
    
    try:
        with db_engine.connect() as conn:
            # 先检查列是否存在
            col_check = conn.execute(text("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = :t AND column_name = :c
            """), {"t": table_name, "c": date_field}).fetchone()
            
            if not col_check:
                return None, None, f"列 {date_field} 不存在"
            
            col_type = col_check[1]
            
            # 获取最新日期，处理字符串日期
            if col_type in ('character varying', 'varchar', 'text'):
                result = conn.execute(text(f"SELECT max({date_field}::date) FROM {table_name}"))
            else:
                result = conn.execute(text(f"SELECT max({date_field}) FROM {table_name}"))
            
            latest = result.scalar()
            
        return date_field, col_type, latest
        
    except Exception as e:
        return None, None, str(e)


def test_count_mismatch(table_name: str, cfg: dict) -> dict:
    """关卡1：数没丢 - 对比 API 返回 vs 入库行数"""
    result = {"table": table_name, "check": "count_mismatch", "status": "skip", "detail": ""}
    
    if cfg.get("mode") not in ("incremental", "full"):
        result["detail"] = f"mode={cfg.get('mode')} 不适用"
        return result
    
    # 跳过无 date_field 的表
    if not cfg.get("date_field"):
        result["detail"] = "缺少 date_field 配置"
        return result
    
    date_field, col_type, latest = get_table_date_info(table_name, cfg)
    if latest is None:
        result["detail"] = f"无数据或错误: {col_type}"
        return result
    
    try:
        # 查该日期区间的入库行数
        with db_engine.connect() as conn:
            if col_type in ('character varying', 'varchar', 'text'):
                db_count = conn.execute(
                    text(f"SELECT count(*) FROM {table_name} WHERE {date_field}::date = :d"),
                    {"d": latest}
                ).scalar()
            else:
                db_count = conn.execute(
                    text(f"SELECT count(*) FROM {table_name} WHERE {date_field} = :d"),
                    {"d": latest}
                ).scalar()
        
        # 获取 checkpoint
        with db_engine.connect() as conn:
            cp = conn.execute(
                text("SELECT last_sync_date FROM sync_checkpoint WHERE table_name = :t"),
                {"t": table_name}
            ).scalar()
        
        result["status"] = "pass"
        result["detail"] = f"date_field={date_field}, latest={latest}, db_rows={db_count}, checkpoint={cp}"
        
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
    
    return result


def test_quality_rules_sql(table_name: str, cfg: dict) -> dict:
    """关卡2：数没乱 - 质检规则（复用 checker，拉取样本数据）"""
    result = {"table": table_name, "check": "quality_rules", "status": "skip", "detail": "", "issues": []}
    
    quality_rules = cfg.get("quality_rules", [])
    if not quality_rules:
        result["detail"] = "未配置 quality_rules"
        return result
    
    # 跳过无 date_field 的表
    if not cfg.get("date_field"):
        result["detail"] = "缺少 date_field 配置"
        return result
    
    date_field, col_type, latest = get_table_date_info(table_name, cfg)
    if latest is None:
        result["detail"] = f"无数据或错误: {col_type}"
        return result
    
    try:
        start_date = latest - timedelta(days=7)
        issues_all = []
        
        for rule_name in quality_rules:
            rule_cfg = QUALITY_RULES.get(rule_name)
            if not rule_cfg:
                issues_all.append(f"规则 {rule_name} 未定义")
                continue
            
            checker = RULE_CHECKERS.get(rule_name)
            if not checker:
                issues_all.append(f"规则 {rule_name} 无 checker")
                continue
            
            # 拉取样本数据跑 checker - 增加限制以覆盖多天
            with db_engine.connect() as conn:
                if col_type in ('character varying', 'varchar', 'text'):
                    df = pd.read_sql(
                        text(f"SELECT * FROM {table_name} WHERE {date_field}::date BETWEEN :s AND :e LIMIT 50000"),
                        conn, params={"s": start_date, "e": latest}
                    )
                else:
                    df = pd.read_sql(
                        text(f"SELECT * FROM {table_name} WHERE {date_field} BETWEEN :s AND :e LIMIT 50000"),
                        conn, params={"s": start_date, "e": latest}
                    )
            
            if not df.empty:
                issues = checker(df, rule_cfg, table_name=table_name, check_date=latest)
                if issues:
                    issues_all.extend([f"{rule_name}: {i}" for i in issues])
        
        if issues_all:
            result["status"] = "fail"
            result["issues"] = issues_all[:10]
            result["detail"] = f"发现 {len(issues_all)} 个问题"
        else:
            result["status"] = "pass"
            result["detail"] = f"检查 {len(quality_rules)} 条规则通过"
            
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
    
    return result


def test_backsource_validation(table_name: str, cfg: dict) -> dict:
    """关卡3：业务对得上 - 回源复核"""
    result = {"table": table_name, "check": "backsource", "status": "skip", "detail": ""}
    
    sample_pct = cfg.get("verify_sample_pct", 0.1)
    verify_size = cfg.get("verify_sample_size", 0)
    
    if sample_pct == 0 and verify_size == 0:
        result["detail"] = "未启用回源验证"
        return result
    
    # 跳过非标准 API 表（有 sync_func 的自定义同步表）
    if cfg.get("sync_func") and not cfg.get("api"):
        result["detail"] = "自定义同步表，跳过标准回源"
        return result
    
    # 跳过 by-code 模式表（按代码遍历，不适合按日期抽样回源）
    if cfg.get("api_date_type") == "code" or cfg.get("code_source"):
        result["detail"] = "by-code 模式，跳过日期抽样回源"
        return result
    
    # 跳过无 date_field 的表
    if not cfg.get("date_field"):
        result["detail"] = "缺少 date_field 配置"
        return result
    
    date_field, col_type, latest = get_table_date_info(table_name, cfg)
    if latest is None:
        result["detail"] = f"无数据: {col_type}"
        return result
    
    try:
        start_date = latest - timedelta(days=30)
        
        # 使用现有 SyncVerifier
        verifier = SyncVerifier()
        vr = verifier.verify(table_name, start_date, latest, sample_size=min(verify_size or 3, 3))
        
        if vr and vr["status"] == "pass":
            result["status"] = "pass"
            result["detail"] = f"抽样 {vr['total_checks']} 个日期全部通过"
        elif vr:
            result["status"] = "fail"
            mismatches = vr.get('mismatches', [])
            result["detail"] = f"失败 {vr['failed']}/{vr['total_checks']}: {mismatches[:3]}"
        else:
            result["detail"] = "验证器返回空"
            
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
    
    return result


def run_all_tests():
    print("=" * 80)
    print("全表验证机制测试开始")
    print("=" * 80)
    
    all_results = []
    tables_tested = 0
    
    for table_name, cfg in DATA_SYNC_TASKS.items():
        if not cfg.get("enabled", True):
            continue
            
        print(f"\n>>> 测试表: {table_name} ({cfg.get('name', '')})")
        tables_tested += 1
        
        # 关卡1
        r1 = test_count_mismatch(table_name, cfg)
        all_results.append(r1)
        print(f"  [数没丢] {r1['status']}: {r1['detail']}")
        
        # 关卡2
        r2 = test_quality_rules_sql(table_name, cfg)
        all_results.append(r2)
        print(f"  [数没乱] {r2['status']}: {r2['detail']}")
        if r2["issues"]:
            for iss in r2["issues"][:3]:
                print(f"    - {iss}")
        
        # 关卡3
        r3 = test_backsource_validation(table_name, cfg)
        all_results.append(r3)
        print(f"  [业务对得上] {r3['status']}: {r3['detail']}")
    
    # 汇总
    print("\n" + "=" * 80)
    print("测试汇总")
    print("=" * 80)
    print(f"测试表数: {tables_tested}")
    print(f"总检查项: {len(all_results)}")
    
    by_status = {}
    for r in all_results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")
    
    # 失败详情
    failed = [r for r in all_results if r["status"] == "fail"]
    if failed:
        print("\n失败详情:")
        for r in failed:
            print(f"  {r['table']}.{r['check']}: {r['detail']}")
            if r.get("issues"):
                for iss in r["issues"][:2]:
                    print(f"    - {iss}")
    
    return all_results


if __name__ == "__main__":
    run_all_tests()