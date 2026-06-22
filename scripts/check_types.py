import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

# Check actual column types
cur.execute("SELECT data_type FROM information_schema.columns WHERE table_name='trade_cal' AND column_name='cal_date'")
print(f'trade_cal.cal_date type: {cur.fetchone()[0]}')

cur.execute("SELECT data_type FROM information_schema.columns WHERE table_name='daily' AND column_name='trade_date'")
print(f'daily.trade_date type: {cur.fetchone()[0]}')

# Check daily min/max
cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily")
r = cur.fetchone()
print(f'Daily overall: min={r[0]} max={r[1]} total={r[2]}')

# Check early dates with date cast
cur.execute("SELECT trade_date, COUNT(*) FROM daily GROUP BY trade_date ORDER BY trade_date LIMIT 10")
for row in cur.fetchall():
    print(f'  Earliest: {row[0]} ({type(row[0]).__name__}): {row[1]} rows')

# Check how many dates before 2003
cur.execute("SELECT COUNT(DISTINCT trade_date) FROM daily WHERE trade_date::text < '20030101'")
print(f'Distinct dates before 2003: {cur.fetchone()[0]}')

# Check how many dates before 2000
cur.execute("SELECT COUNT(DISTINCT trade_date) FROM daily WHERE trade_date::text < '20000101'")
print(f'Distinct dates before 2000: {cur.fetchone()[0]}')

# Sample trade_cal to understand format
cur.execute("SELECT cal_date, is_open FROM trade_cal WHERE is_open = 1 ORDER BY cal_date LIMIT 5")
for row in cur.fetchall():
    print(f'  trade_cal: {row[0]} ({type(row[0]).__name__}): is_open={row[1]}')

conn.close()
