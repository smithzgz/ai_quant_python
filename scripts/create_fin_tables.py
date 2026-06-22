# -*- coding: utf-8 -*-
"""Create tables for 6 new financial sync tasks"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.database.connection import engine
from sqlalchemy import text
from utils.logger import get_logger

logger = get_logger("create_fin_tables")

TABLES_SQL = {
    "moneyflow": """
        CREATE TABLE IF NOT EXISTS moneyflow (
            ts_code VARCHAR(20) NOT NULL,
            trade_date DATE NOT NULL,
            buy_sm_vol DOUBLE PRECISION,
            buy_sm_amount DOUBLE PRECISION,
            sell_sm_vol DOUBLE PRECISION,
            sell_sm_amount DOUBLE PRECISION,
            buy_md_vol DOUBLE PRECISION,
            buy_md_amount DOUBLE PRECISION,
            sell_md_vol DOUBLE PRECISION,
            sell_md_amount DOUBLE PRECISION,
            buy_lg_vol DOUBLE PRECISION,
            buy_lg_amount DOUBLE PRECISION,
            sell_lg_vol DOUBLE PRECISION,
            sell_lg_amount DOUBLE PRECISION,
            buy_elg_vol DOUBLE PRECISION,
            buy_elg_amount DOUBLE PRECISION,
            sell_elg_vol DOUBLE PRECISION,
            sell_elg_amount DOUBLE PRECISION,
            net_mf_vol DOUBLE PRECISION,
            net_mf_amount DOUBLE PRECISION,
            PRIMARY KEY (ts_code, trade_date)
        );
    """,
    "income": """
        CREATE TABLE IF NOT EXISTS income (
            ts_code VARCHAR(20) NOT NULL,
            ann_date DATE,
            f_ann_date DATE,
            end_date DATE NOT NULL,
            report_type VARCHAR(10),
            comp_type VARCHAR(10),
            basic_eps DOUBLE PRECISION,
            diluted_eps DOUBLE PRECISION,
            total_revenue DOUBLE PRECISION,
            revenue DOUBLE PRECISION,
            n_income DOUBLE PRECISION,
            n_income_attr_p DOUBLE PRECISION,
            compr_inc_attr_p DOUBLE PRECISION,
            minority_gain DOUBLE PRECISION,
            total_profit DOUBLE PRECISION,
            income_tax DOUBLE PRECISION,
            operate_profit DOUBLE PRECISION,
            n_commis_income DOUBLE PRECISION,
            oth_b_income DOUBLE PRECISION,
            fv_value_chg_gain DOUBLE PRECISION,
            invest_income DOUBLE PRECISION,
            total_cogs DOUBLE PRECISION,
            oper_cost DOUBLE PRECISION,
            biz_tax_surchg DOUBLE PRECISION,
            sell_exp DOUBLE PRECISION,
            admin_exp DOUBLE PRECISION,
            fin_exp DOUBLE PRECISION,
            assets_impair_loss DOUBLE PRECISION,
            update_flag VARCHAR(10),
            PRIMARY KEY (ts_code, end_date, update_flag)
        );
    """,
    "balancesheet": """
        CREATE TABLE IF NOT EXISTS balancesheet (
            ts_code VARCHAR(20) NOT NULL,
            ann_date DATE,
            f_ann_date DATE,
            end_date DATE NOT NULL,
            report_type VARCHAR(10),
            total_assets DOUBLE PRECISION,
            total_liab DOUBLE PRECISION,
            total_hldr_eqy_exc_min_int DOUBLE PRECISION,
            total_hldr_eqy_inc_min_int DOUBLE PRECISION,
            money_cap DOUBLE PRECISION,
            accounts_receiv DOUBLE PRECISION,
            inventories DOUBLE PRECISION,
            fix_assets DOUBLE PRECISION,
            intan_assets DOUBLE PRECISION,
            goodwill DOUBLE PRECISION,
            lt_borr DOUBLE PRECISION,
            st_borr DOUBLE PRECISION,
            total_cur_assets DOUBLE PRECISION,
            total_nca DOUBLE PRECISION,
            total_cur_liab DOUBLE PRECISION,
            total_ncl DOUBLE PRECISION,
            update_flag VARCHAR(10),
            PRIMARY KEY (ts_code, end_date, update_flag)
        );
    """,
    "cashflow": """
        CREATE TABLE IF NOT EXISTS cashflow (
            ts_code VARCHAR(20) NOT NULL,
            ann_date DATE,
            f_ann_date DATE,
            end_date DATE NOT NULL,
            report_type VARCHAR(10),
            n_cashflow_act DOUBLE PRECISION,
            n_cashflow_inv_act DOUBLE PRECISION,
            n_cash_flows_fnc_act DOUBLE PRECISION,
            free_cashflow DOUBLE PRECISION,
            c_cash_equ_end_period DOUBLE PRECISION,
            n_incr_cash_cash_equ DOUBLE PRECISION,
            c_fr_sale_sg DOUBLE PRECISION,
            c_paid_goods_s DOUBLE PRECISION,
            c_paid_to_for_empl DOUBLE PRECISION,
            c_paid_for_taxes DOUBLE PRECISION,
            c_recp_borrow DOUBLE PRECISION,
            c_prepay_amt_borr DOUBLE PRECISION,
            c_pay_dist_dpcp_int_exp DOUBLE PRECISION,
            update_flag VARCHAR(10),
            PRIMARY KEY (ts_code, end_date, update_flag)
        );
    """,
    "fina_indicator": """
        CREATE TABLE IF NOT EXISTS fina_indicator (
            ts_code VARCHAR(20) NOT NULL,
            ann_date DATE,
            end_date DATE NOT NULL,
            eps DOUBLE PRECISION,
            dt_eps DOUBLE PRECISION,
            bps DOUBLE PRECISION,
            roe DOUBLE PRECISION,
            roe_waa DOUBLE PRECISION,
            roe_dt DOUBLE PRECISION,
            roa DOUBLE PRECISION,
            grossprofit_margin DOUBLE PRECISION,
            netprofit_margin DOUBLE PRECISION,
            current_ratio DOUBLE PRECISION,
            quick_ratio DOUBLE PRECISION,
            debt_to_assets DOUBLE PRECISION,
            assets_turn DOUBLE PRECISION,
            op_yoy DOUBLE PRECISION,
            netprofit_yoy DOUBLE PRECISION,
            tr_yoy DOUBLE PRECISION,
            or_yoy DOUBLE PRECISION,
            dt_netprofit_yoy DOUBLE PRECISION,
            ocf_yoy DOUBLE PRECISION,
            roe_yoy DOUBLE PRECISION,
            rd_exp DOUBLE PRECISION,
            update_flag VARCHAR(10),
            PRIMARY KEY (ts_code, end_date, update_flag)
        );
    """,
    "fina_audit": """
        CREATE TABLE IF NOT EXISTS fina_audit (
            ts_code VARCHAR(20) NOT NULL,
            ann_date DATE,
            end_date DATE NOT NULL,
            audit_result VARCHAR(50),
            audit_fees DOUBLE PRECISION,
            audit_agency VARCHAR(100),
            audit_sign VARCHAR(100),
            PRIMARY KEY (ts_code, end_date)
        );
    """,
}


def main():
    with engine.connect() as conn:
        for table_name, sql in TABLES_SQL.items():
            try:
                conn.execute(text(sql))
                conn.commit()
                logger.info(f"Created table: {table_name}")
            except Exception as e:
                logger.error(f"Failed to create {table_name}: {e}")

    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ))
        tables = [r[0] for r in result.fetchall()]
        logger.info(f"All tables ({len(tables)}): {', '.join(tables)}")


if __name__ == "__main__":
    main()
