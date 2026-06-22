import psycopg2

conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

cur.execute("SELECT table_name, status, start_time, end_time FROM sync_log ORDER BY id DESC LIMIT 10")
rows = cur.fetchall()
print('=== Recent Sync Logs ===')
for r in rows:
    print(f'{r[0]:25s} {r[1]:12s} {r[2]} -> {r[3]}')

cur.execute("""SELECT column_name FROM information_schema.columns 
               WHERE table_name='sync_checkpoint' ORDER BY ordinal_position""")
cols = [r[0] for r in cur.fetchall()]
print(f'\nCheckpoint columns: {cols}')

cur.execute("SELECT * FROM sync_checkpoint ORDER BY table_name")
rows = cur.fetchall()
print()
print('=== Checkpoints ===')
for r in rows:
    d = dict(zip(cols, r))
    print(f'{d.get("table_name","?"):25s} | {d}')

tables = ['trade_cal', 'stock_basic', 'daily', 'daily_basic', 'adj_factor', 'fund_basic', 'fund_daily', 'moneyflow',
          'income', 'balancesheet', 'cashflow', 'fina_indicator', 'fina_audit']
print()
print('=== Table Record Counts ===')
for t in tables:
    try:
        cur.execute(f'SELECT COUNT(*) FROM {t}')
        cnt = cur.fetchone()[0]
        print(f'{t:25s}: {cnt:>10,} rows')
    except Exception as e:
        print(f'{t:25s}: ERROR - {e}')

conn.close()
