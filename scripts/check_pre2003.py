import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM daily WHERE trade_date < '1991-01-01'::date")
print(f'Rows before 1991: {cur.fetchone()[0]}')

cur.execute("SELECT trade_date, COUNT(*) FROM daily WHERE trade_date < '2004-01-01'::date GROUP BY trade_date ORDER BY trade_date LIMIT 15")
rows = cur.fetchall()
print(f'Earliest dates in daily:')
for r in rows:
    print(f'  {r[0]}: {r[1]} rows')

# Check what happened - maybe daily was loaded in two passes?
# Check checkpoint more carefully
cur.execute("SELECT * FROM sync_checkpoint WHERE table_name = 'daily'")
r = cur.fetchone()
print(f'\nDaily checkpoint: {r}')

# Check max trade_date
cur.execute("SELECT MAX(trade_date) FROM daily")
print(f'Max trade_date: {cur.fetchone()[0]}')

# Check how many distinct dates total
cur.execute("SELECT COUNT(DISTINCT trade_date) FROM daily")
print(f'Total distinct trade dates: {cur.fetchone()[0]}')

# Check trade_cal open dates count
cur.execute("SELECT COUNT(*) FROM trade_cal WHERE is_open = 1 AND cal_date > '1991-01-01'::date")
print(f'Trade cal open dates since 1991: {cur.fetchone()[0]}')

conn.close()
