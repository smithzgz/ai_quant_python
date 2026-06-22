"""全量同步所有表 1991-至今"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date as _date, datetime
from data.database.connection import engine, SessionLocal
from data.database.models import SyncCheckpoint
from config.data_sync_config import DATA_SYNC_TASKS

# Step 1: Set all checkpoints to oldest_date
from sqlalchemy import text
with engine.connect() as conn:
    for name, cfg in DATA_SYNC_TASKS.items():
        oldest = cfg.get('oldest_date', '19910101')
        d = datetime.strptime(oldest, '%Y%m%d').date()
        conn.execute(text(
            "INSERT INTO sync_checkpoint (table_name, last_sync_date) VALUES (:t, :d) "
            "ON CONFLICT (table_name) DO UPDATE SET last_sync_date = :d"
        ), {"t": name, "d": d})
    conn.commit()
print('All checkpoints reset')
print('All checkpoints reset to oldest_date')

# Step 2: Run sync for each table
from data.sync.engine import SyncEngine
import time

engine = SyncEngine()
sorted_tasks = sorted(DATA_SYNC_TASKS.items(), key=lambda x: x[1].get('priority', 99))

for name, cfg in sorted_tasks:
    if not cfg.get('enabled', True):
        print(f'SKIP: {name} (disabled)')
        continue
    print(f'\n===== Syncing {name} (mode=full) =====')
    t0 = time.time()
    try:
        rows, start, end = engine._sync_full(name, cfg)
        elapsed = time.time() - t0
        print(f'OK: {name} -> {rows} rows, {start}~{end}, {elapsed:.0f}s')
    except Exception as e:
        elapsed = time.time() - t0
        print(f'FAIL: {name} -> {e} ({elapsed:.0f}s)')

print('\n===== All syncs complete =====')
