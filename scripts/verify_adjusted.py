import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quant_db', user='postgres', password='postgres')
cur = conn.cursor()

for tbl in ['daily', 'daily_qfq', 'daily_hfq']:
    cur.execute(f'SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {tbl}')
    r = cur.fetchone()
    print(f'{tbl:12s}: {r[0]:>12,} rows, {r[1]} ~ {r[2]}')

print()
for tbl in ['daily', 'daily_qfq', 'daily_hfq']:
    cur.execute("SELECT open, close FROM {} WHERE ts_code='000001.SZ' AND trade_date='20260618'".format(tbl))
    r = cur.fetchone()
    if r: print(f'{tbl:12s}: 000001.SZ 20260618 O={r[0]:.4f} C={r[1]:.4f}')

for tbl in ['daily_qfq', 'daily_hfq']:
    try:
        cur.execute("SELECT create_hypertable('{}', 'trade_date')".format(tbl))
        conn.commit()
        print(f'{tbl}: hypertable OK')
    except Exception as e:
        conn.rollback()
        print(f'{tbl}: hypertable skip')

conn.close()
