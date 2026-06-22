import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

# Check daily table schema
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='daily' ORDER BY ordinal_position")
print('=== daily table columns ===')
for row in cur.fetchall():
    print(f'  {row[0]:20s} {row[1]}')

# Check min/max of daily - is it still 2003? (sync is still running)
cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date) FROM daily")
r = cur.fetchone()
print(f'\nDaily: min={r[0]} max={r[1]} total={r[2]} distinct_dates={r[3]}')

conn.close()
