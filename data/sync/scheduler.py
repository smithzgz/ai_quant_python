# -*- coding: utf-8 -*-
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from data.sync.engine import SyncEngine
from config.data_sync_config import DATA_SYNC_TASKS
from utils.logger import get_logger

logger = get_logger("scheduler")


def create_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler()
    engine = SyncEngine()

    for table_name, cfg in DATA_SYNC_TASKS.items():
        if not cfg.get("enabled", True):
            continue

        schedule = cfg.get("schedule")
        if not schedule:
            continue

        parts = schedule.split()
        if len(parts) == 5:
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )
            scheduler.add_job(
                engine.sync,
                trigger=trigger,
                kwargs={"table_name": table_name},
                id=f"sync_{table_name}",
                name=f"同步 {cfg.get('name', table_name)}",
                misfire_grace_time=3600,
            )
            logger.info(f"Scheduled: sync_{table_name} ({schedule})")

    return scheduler


if __name__ == "__main__":
    sched = create_scheduler()
    logger.info("Scheduler started. Press Ctrl+C to exit.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
