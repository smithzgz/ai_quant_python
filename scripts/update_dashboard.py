# -*- coding: utf-8 -*-
"""Add financial panels to Stock K-Line dashboard via Grafana API"""
import json
import requests
import base64

GRAFANA_URL = "http://localhost:8080"
AUTH = base64.b64encode(b"admin:admin").decode()
HEADERS = {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"}

DS_UID = "bfpbii1tm9ou8c"
DASHBOARD_UID = "dfotwm9b9v1tsc"


def make_panel(title, sql, panel_type="timeseries", grid_pos=None):
    return {
        "datasource": {"type": "postgres", "uid": DS_UID},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "axisBorderShow": False,
                    "axisCenteredZero": False,
                    "axisColorMode": "text",
                    "axisLabel": "",
                    "axisPlacement": "auto",
                    "barAlignment": 0,
                    "drawStyle": "line",
                    "fillOpacity": 10,
                    "gradientMode": "none",
                    "lineInterpolation": "smooth",
                    "lineWidth": 2,
                    "pointSize": 5,
                    "scaleDistribution": {"type": "linear"},
                    "showPoints": "never",
                    "spanNulls": True,
                    "stacking": {"group": "A", "mode": "none"},
                    "thresholdsStyle": {"mode": "off"},
                },
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "green", "value": None}, {"color": "red", "value": 80}],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "gridPos": grid_pos or {"h": 8, "w": 12, "x": 0, "y": 10},
        "id": None,
        "options": {
            "legend": {"calcs": [], "displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "title": title,
        "type": panel_type,
        "targets": [
            {
                "datasource": {"type": "postgres", "uid": DS_UID},
                "editorMode": "code",
                "rawSql": sql,
                "format": "time_series",
                "rawQuery": True,
            }
        ],
    }


def make_row_panel(title, y):
    return {
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "id": None,
        "title": title,
        "type": "row",
    }


def main():
    # Get current dashboard
    r = requests.get(f"{GRAFANA_URL}/api/dashboards/uid/{DASHBOARD_UID}", headers=HEADERS)
    dash = r.json()["dashboard"]
    version = dash["version"]

    # Find max existing id
    max_id = 0
    for p in dash["panels"]:
        if p.get("id") and p["id"] > max_id:
            max_id = p["id"]

    next_id = max_id + 1

    # Current panels end at y ~= 10 (6h panels)
    y_start = 20

    new_panels = []

    # Row: Financial Key Indicators
    new_panels.append(make_row_panel("Financial Key Indicators", y_start))
    y_start += 1

    # Income Statement
    income_sql = """SELECT CAST(end_date AS DATE) as time,
        total_revenue/100000000 as "Revenue (100M)",
        n_income/100000000 as "Net Income (100M)",
        compr_inc_attr_p/100000000 as "Parent Net Income (100M)"
    FROM income
    WHERE ts_code = '$ts_code'
    ORDER BY end_date"""
    p = make_panel("Income Statement", income_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 0, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)

    # Balance Sheet
    bs_sql = """SELECT CAST(end_date AS DATE) as time,
        total_assets/100000000 as "Total Assets (100M)",
        total_liab/100000000 as "Total Liabilities (100M)",
        total_hldr_eqy_exc_min_int/100000000 as "Equity (100M)"
    FROM balancesheet
    WHERE ts_code = '$ts_code'
    ORDER BY end_date"""
    p = make_panel("Balance Sheet", bs_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 12, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)
    y_start += 8

    # Cash Flow
    cf_sql = """SELECT CAST(end_date AS DATE) as time,
        n_cashflow_act/100000000 as "Operating CF (100M)",
        n_cashflow_inv_act/100000000 as "Investing CF (100M)",
        n_cash_flows_fnc_act/100000000 as "Financing CF (100M)",
        free_cashflow/100000000 as "Free CF (100M)"
    FROM cashflow
    WHERE ts_code = '$ts_code'
    ORDER BY end_date"""
    p = make_panel("Cash Flow Statement", cf_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 0, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)

    # Financial Indicators
    fi_sql = """SELECT CAST(end_date AS DATE) as time,
        roe as "ROE (%)",
        roe_waa as "ROE (WAA) (%)",
        grossprofit_margin as "Gross Margin (%)",
        netprofit_margin as "Net Margin (%)"
    FROM fina_indicator
    WHERE ts_code = '$ts_code'
    ORDER BY end_date"""
    p = make_panel("Profitability Indicators", fi_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 12, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)
    y_start += 8

    # Growth Indicators
    growth_sql = """SELECT CAST(end_date AS DATE) as time,
        op_yoy as "Operating Profit YoY (%)",
        netprofit_yoy as "Net Profit YoY (%)",
        tr_yoy as "Revenue YoY (%)"
    FROM fina_indicator
    WHERE ts_code = '$ts_code'
    ORDER BY end_date"""
    p = make_panel("Growth Indicators (YoY %)", growth_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 0, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)

    # Leverage Indicators
    lev_sql = """SELECT CAST(end_date AS DATE) as time,
        debt_to_assets as "Debt-to-Asset (%)",
        current_ratio as "Current Ratio",
        quick_ratio as "Quick Ratio"
    FROM fina_indicator
    WHERE ts_code = '$ts_code'
    ORDER BY end_date"""
    p = make_panel("Leverage & Liquidity", lev_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 12, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)
    y_start += 8

    # Money Flow
    mf_sql = """SELECT trade_date as time,
        (buy_sm_vol-sell_sm_vol)/10000 as "Small Net (10K)",
        (buy_md_vol-sell_md_vol)/10000 as "Medium Net (10K)",
        (buy_lg_vol-sell_lg_vol)/10000 as "Large Net (10K)",
        (buy_elg_vol-sell_elg_vol)/10000 as "XLarge Net (10K)"
    FROM moneyflow
    WHERE ts_code = '$ts_code'
    ORDER BY trade_date"""
    p = make_panel("Money Flow (Net Volume)", mf_sql)
    p["gridPos"] = {"h": 8, "w": 12, "x": 0, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)

    # Audit Opinions
    audit_sql = """SELECT end_date as time,
        audit_result as "Audit Result",
        audit_fees as "Audit Fees",
        audit_agency as "Audit Agency"
    FROM fina_audit
    WHERE ts_code = '$ts_code'
    ORDER BY end_date DESC"""
    p = make_panel("Audit Opinions", audit_sql, panel_type="table")
    p["gridPos"] = {"h": 8, "w": 12, "x": 12, "y": y_start}
    p["id"] = next_id; next_id += 1
    new_panels.append(p)
    y_start += 8

    # Add new panels to dashboard
    dash["panels"].extend(new_panels)

    # Update dashboard
    payload = {
        "dashboard": dash,
        "overwrite": True,
        "message": "Added financial indicator panels",
    }
    r = requests.post(f"{GRAFANA_URL}/api/dashboards/db", headers=HEADERS, json=payload)
    result = r.json()
    if result.get("status") == "success":
        print(f"Dashboard updated successfully! URL: {result.get('url')}")
        print(f"Added {len(new_panels)} panels")
    else:
        print(f"Failed: {result}")


if __name__ == "__main__":
    main()
