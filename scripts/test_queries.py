import sys; sys.path.insert(0, r'D:\code\Python\ai_quant_python')
import psycopg2
from config.settings import settings
conn = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=settings.DB_NAME)
cur = conn.cursor()

queries = [
    ("7. New Bonds by Date", "SELECT list_date::date AS time, COUNT(*) AS cnt FROM bond_basic WHERE list_date IS NOT NULL GROUP BY list_date ORDER BY list_date LIMIT 3"),
    ("15. Issuance Trend", "SELECT TO_DATE(SUBSTRING(ann_date, 1, 6), 'YYYYMM') AS time, COUNT(*) AS issues FROM cb_issue WHERE ann_date IS NOT NULL GROUP BY time ORDER BY time LIMIT 3"),
    ("16. Avg Issue Price Trend", "SELECT TO_DATE(SUBSTRING(ann_date, 1, 4), 'YYYY') AS time, ROUND(AVG(issue_price)::numeric, 2) FROM cb_issue WHERE ann_date IS NOT NULL AND issue_price > 0 GROUP BY time ORDER BY time LIMIT 3"),
    ("19. Monthly Conversion", "SELECT TO_DATE(SUBSTRING(end_date, 1, 6), 'YYYYMM') AS time, SUM(convert_val) FROM cb_share WHERE convert_val IS NOT NULL GROUP BY time ORDER BY time LIMIT 3"),
    ("22. Bond Price 90D", "SELECT trade_date::date AS time, ts_code, close FROM cb_daily WHERE ts_code = '110075.SH' AND trade_date::date >= (SELECT MAX(trade_date) FROM cb_daily)::date - INTERVAL '90 days' ORDER BY trade_date LIMIT 3"),
    ("23. Bond Volume 90D", "SELECT trade_date::date AS time, ts_code, vol FROM cb_daily WHERE ts_code = '110075.SH' AND trade_date::date >= (SELECT MAX(trade_date) FROM cb_daily)::date - INTERVAL '90 days' ORDER BY trade_date LIMIT 3"),
]

for name, q in queries:
    try:
        cur.execute(q)
        rows = cur.fetchall()
        print(f"OK  {name}: {len(rows)} rows -> {rows[:2]}")
    except Exception as e:
        print(f"ERR {name}: {e}")

cur.close()
conn.close()
