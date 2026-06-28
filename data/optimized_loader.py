# -*- coding: utf-8 -*-
"""
优化版数据加载：用于回归分析
- 只读必要字段
- 使用高效数据类型
- 自动转换为宽表格式（日期为索引，股票为列）
"""
import sys
import time
import pandas as pd
import numpy as np

sys.path.insert(0, r'D:\code\Python\ai_quant_python')

from config.settings import settings
from sqlalchemy import create_engine, text


def get_engine():
    """创建 SQLAlchemy 引擎"""
    return create_engine(
        f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )


def load_close_prices(start_date='19910101', end_date='20260630'):
    """
    快速加载收盘价（宽表格式）
    
    返回: DataFrame, index=日期, columns=股票代码, values=收盘价
    """
    engine = get_engine()
    
    start_time = time.time()
    
    sql = f"""
        SELECT trade_date, ts_code, close
        FROM daily
        WHERE trade_date >= '{start_date}' AND trade_date <= '{end_date}'
        ORDER BY trade_date, ts_code
    """
    
    # 使用 chunked reading 减少内存峰值
    chunks = pd.read_sql(sql, engine, chunksize=500_000)
    
    dfs = []
    for chunk in chunks:
        # 优化数据类型
        chunk['trade_date'] = pd.to_datetime(chunk['trade_date'], format='%Y%m%d')
        chunk['ts_code'] = chunk['ts_code'].astype('category')
        chunk['close'] = chunk['close'].astype('float32')
        dfs.append(chunk)
    
    df = pd.concat(dfs, ignore_index=True)
    
    # 透视为宽表
    close_df = df.pivot_table(
        index='trade_date',
        columns='ts_code',
        values='close'
    )
    close_df = close_df.sort_index()
    
    elapsed = time.time() - start_time
    mem_mb = close_df.memory_usage(deep=True).sum() / 1024 / 1024
    
    print(f"加载完成: {close_df.shape[0]} 天 x {close_df.shape[1]} 只股票")
    print(f"耗时: {elapsed:.2f}秒, 内存: {mem_mb:.1f} MB")
    
    return close_df


def load_multi_field(start_date='19910101', end_date='20260630'):
    """
    加载多字段（OHLCV），用于需要开盘价/最高价/最低价的策略
    """
    engine = get_engine()
    
    start_time = time.time()
    
    sql = f"""
        SELECT trade_date, ts_code, open, high, low, close, vol
        FROM daily
        WHERE trade_date >= '{start_date}' AND trade_date <= '{end_date}'
        ORDER BY trade_date, ts_code
    """
    
    chunks = pd.read_sql(sql, engine, chunksize=500_000)
    
    dfs = []
    for chunk in chunks:
        chunk['trade_date'] = pd.to_datetime(chunk['trade_date'], format='%Y%m%d')
        chunk['ts_code'] = chunk['ts_code'].astype('category')
        for col in ['open', 'high', 'low', 'close']:
            chunk[col] = chunk[col].astype('float32')
        chunk['vol'] = chunk['vol'].astype('float32')
        dfs.append(chunk)
    
    df = pd.concat(dfs, ignore_index=True)
    
    elapsed = time.time() - start_time
    mem_mb = df.memory_usage(deep=True).sum() / 1024 / 1024
    
    print(f"加载完成: {len(df):,} 条记录, {df['ts_code'].nunique()} 只股票")
    print(f"耗时: {elapsed:.2f}秒, 内存: {mem_mb:.1f} MB")
    
    return df


if __name__ == '__main__':
    print("=" * 60)
    print("优化版数据加载测试")
    print("=" * 60)
    
    # 测试 1: 只读收盘价
    print("\n[1] 只读收盘价（回归用）:")
    t1 = time.time()
    close_df = load_close_prices()
    print(f"总耗时: {time.time()-t1:.2f}秒\n")
    
    # 测试 2: 读取 OHLCV
    print("[2] 读取 OHLCV（完整K线）:")
    t2 = time.time()
    ohlcv_df = load_multi_field()
    print(f"总耗时: {time.time()-t2:.2f}秒\n")
    
    # 显示数据样例
    print("数据样例:")
    print(close_df.head())
