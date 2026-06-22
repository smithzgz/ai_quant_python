# -*- coding: utf-8 -*-
from data.database.connection import engine, SessionLocal
from data.database.models import DataQualityLog
from utils.logger import get_logger

logger = get_logger("reporter")


class QualityReporter:
    def get_latest_report(self, table_name: str = None, limit: int = 50) -> list:
        session = SessionLocal()
        try:
            q = session.query(DataQualityLog)
            if table_name:
                q = q.filter_by(table_name=table_name)
            q = q.order_by(DataQualityLog.checked_at.desc()).limit(limit)
            return [
                {
                    "table_name": r.table_name,
                    "check_date": str(r.check_date),
                    "rule_name": r.rule_name,
                    "status": r.status,
                    "total_rows": r.total_rows,
                    "issue_count": r.issue_count,
                    "checked_at": str(r.checked_at),
                }
                for r in q.all()
            ]
        finally:
            session.close()

    def get_summary(self) -> dict:
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text(
                "SELECT table_name, status, COUNT(*) as cnt "
                "FROM data_quality_log "
                "WHERE checked_at > NOW() - INTERVAL '7 days' "
                "GROUP BY table_name, status "
                "ORDER BY table_name, status"
            ))
            summary = {}
            for row in result.fetchall():
                tn, st, cnt = row
                if tn not in summary:
                    summary[tn] = {}
                summary[tn][st] = cnt
            return summary
