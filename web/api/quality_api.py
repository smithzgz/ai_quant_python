# -*- coding: utf-8 -*-
from fastapi import APIRouter
from datetime import date
from data.quality.checker import QualityChecker
from data.quality.reporter import QualityReporter

router = APIRouter()


@router.get("/check/{table_name}")
def check_quality(table_name: str, check_date: str = None):
    checker = QualityChecker()
    cd = date.fromisoformat(check_date) if check_date else None
    results = checker.check_table(table_name, check_date=cd)
    return results


@router.get("/log")
def quality_log(table_name: str = None, limit: int = 50):
    reporter = QualityReporter()
    return reporter.get_latest_report(table_name=table_name, limit=limit)


@router.get("/summary")
def quality_summary():
    reporter = QualityReporter()
    return reporter.get_summary()
