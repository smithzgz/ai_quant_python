import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

# Check daily raw data sample
cur.execute("SELECT ts_code, trade_date, open, high, low, close, pre_close FROM daily WHERE ts_code='000001.SZ' AND trade_date='20260618'::date")
r = cur.fetchone()
print(f'daily (raw): {r}')

# Check adj_factor sample
cur.execute("SELECT ts_code, trade_date, adj_factor FROM adj_factor WHERE ts_code='000001.SZ' AND trade_date='20260618'::date")
r = cur.fetchone()
print(f'adj_factor:  {r}')

# Check adj_factor range for a stock
cur.execute("SELECT MIN(adj_factor), MAX(adj_factor), AVG(adj_factor) FROM adj_factor WHERE ts_code='000001.SZ'")
r = cur.fetchone()
print(f'adj_factor range for 000001.SZ: min={r[0]}, max={r[1]}, avg={r[2]:.4f}')

# Latest adj_factor
cur.execute("SELECT trade_date, adj_factor FROM adj_factor WHERE ts_code='000001.SZ' ORDER BY trade_date DESC LIMIT 5")
print('\nLatest adj_factor for 000001.SZ:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')

# Earliest adj_factor
cur.execute("SELECT trade_date, adj_factor FROM adj_factor WHERE ts_code='000001.SZ' ORDER BY trade_date ASC LIMIT 5")
print('\nEarliest adj_factor for 000001.SZ:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')

# Total rows
cur.execute("SELECT COUNT(*) FROM adj_factor")
print(f'\nadj_factor total rows: {cur.fetchone()[0]:,}')

cur.execute("SELECT COUNT(DISTINCT ts_code) FROM adj_factor")
print(f'adj_factor distinct stocks: {cur.fetchone()[0]:,}')

conn.close()
