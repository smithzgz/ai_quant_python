import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

# Check trade_cal coverage before 2003
cur.execute("SELECT cal_date, is_open FROM trade_cal WHERE cal_date < '2003-01-01' ORDER BY cal_date LIMIT 20")
rows = cur.fetchall()
print('=== trade_cal before 2003 (first 20) ===')
for r in rows:
    print(f'  {r[0]} is_open={r[1]}')

cur.execute("SELECT COUNT(*) FROM trade_cal WHERE is_open = 1 AND cal_date < '2003-01-01'")
print(f'Open dates before 2003: {cur.fetchone()[0]}')

# Check min/max of daily
cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date) FROM daily")
r = cur.fetchone()
print(f'\nDaily: min={r[0]} max={r[1]} total={r[2]} distinct_dates={r[3]}')

# Check if there's data from 1991-2003 range
cur.execute("SELECT COUNT(*) FROM daily WHERE trade_date < '2003-01-01'")
print(f'Daily rows before 2003: {cur.fetchone()[0]}')

# Sample some early daily data
cur.execute("SELECT trade_date, ts_code FROM daily ORDER BY trade_date LIMIT 5")
rows = cur.fetchall()
print(f'\nEarliest daily rows:')
for r in rows:
    print(f'  {r[0]} {r[1]}')

conn.close()
