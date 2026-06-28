import json
import psycopg2
import sys; sys.path.insert(0, r'D:\code\Python\ai_quant_python')
from config.settings import settings

conn = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=settings.DB_NAME)
cur = conn.cursor()

with open(r'D:\code\Python\ai_quant_python\visualization\grafana\dashboards\bond_basic.json', encoding='utf-8') as f:
    d = json.load(f)

ok = 0
fail = 0
for p in d['panels']:
    if p.get('type') == 'row':
        continue
    for t in p.get('targets', []):
        sql = t.get('rawSql', '')
        if not sql:
            continue
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            ok += 1
            print(f"OK  Panel {p['id']:2d} ({p['type']:10s}): {len(rows):5d} rows")
        except Exception as e:
            fail += 1
            print(f"ERR Panel {p['id']:2d} ({p['type']:10s}): {e}")

print(f"\nTotal: {ok} OK, {fail} FAIL")
cur.close()
conn.close()
