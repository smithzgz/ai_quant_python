# -*- coding: utf-8 -*-
"""
东方财富研报数据同步
数据来源: https://reportapi.eastmoney.com/report/list
独立表 eastmoney_report，全量同步 2017-2026
"""
import time
import logging
import requests
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

EASTMONEY_API_URL = 'https://reportapi.eastmoney.com/report/list'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://data.eastmoney.com/',
}


def fetch_page(year: int, page_no: int, page_size: int = 50,
               session: requests.Session = None) -> Optional[dict]:
    params = {
        'industryCode': '*',
        'pageSize': page_size,
        'industry': '*',
        'rating': '*',
        'ratingChange': '*',
        'beginTime': f'{year}-01-01',
        'endTime': f'{year}-12-31',
        'pageNo': page_no,
        'fields': '',
        'qType': 0,
        'orgCode': '',
        'code': '',
        'rcode': '',
        'p': page_no,
    }
    sess = session or requests.Session()
    for attempt in range(3):
        try:
            resp = sess.get(EASTMONEY_API_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f'Eastmoney page {page_no} year {year} error (attempt {attempt+1}): {e}')
            time.sleep(1)
    return None


def _safe_float(val) -> Optional[float]:
    if val is None or val == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_record(rec: dict) -> Optional[Dict]:
    stock_code = rec.get('stockCode', '')
    if not stock_code or len(stock_code) != 6:
        return None

    if stock_code.startswith('6'):
        ts_code = f'{stock_code}.SH'
    elif stock_code.startswith(('0', '3')):
        ts_code = f'{stock_code}.SZ'
    elif stock_code.startswith(('4', '8')):
        ts_code = f'{stock_code}.BJ'
    else:
        return None

    publish_date = (rec.get('publishDate') or '')[:10]
    if not publish_date:
        return None

    authors = rec.get('author', [])
    if isinstance(authors, list):
        analyst_str = '|'.join([a.split('.')[-1] if '.' in a else a for a in authors])
    else:
        analyst_str = str(authors) if authors else ''

    info_code = rec.get('infoCode', '')

    return {
        'ts_code': ts_code,
        'stock_name': rec.get('stockName', ''),
        'industry': rec.get('indvInduName', ''),
        'analyst': analyst_str,
        'broker': rec.get('orgSName', ''),
        'publish_date': publish_date,
        'title': rec.get('title', ''),
        's_rating': rec.get('sRatingName', ''),
        's_rating_code': rec.get('sRatingCode', ''),
        'em_rating': rec.get('emRatingName', ''),
        'rating_change': rec.get('ratingChange') if rec.get('ratingChange') not in (None, '', ' ') else None,
        'target_priceHigh': _safe_float(rec.get('indvAimPriceT')),
        'target_priceLow': _safe_float(rec.get('indvAimPriceL')),
        'predict_this_year_eps': _safe_float(rec.get('predictThisYearEps')),
        'predict_this_year_pe': _safe_float(rec.get('predictThisYearPe')),
        'predict_next_year_eps': _safe_float(rec.get('predictNextYearEps')),
        'predict_next_year_pe': _safe_float(rec.get('predictNextYearPe')),
        'info_code': info_code,
        'report_type': rec.get('reportType'),
    }


def _batch_upsert(cursor, records: List[Dict]) -> int:
    count = 0
    sql = """
        INSERT INTO eastmoney_report (
            ts_code, stock_name, industry, analyst, broker,
            publish_date, title, s_rating, s_rating_code, em_rating,
            rating_change, target_priceHigh, target_priceLow,
            predict_this_year_eps, predict_this_year_pe,
            predict_next_year_eps, predict_next_year_pe,
            info_code, report_type, synced_at)
        VALUES (
            %(ts_code)s, %(stock_name)s, %(industry)s, %(analyst)s, %(broker)s,
            %(publish_date)s, %(title)s, %(s_rating)s, %(s_rating_code)s, %(em_rating)s,
            %(rating_change)s, %(target_priceHigh)s, %(target_priceLow)s,
            %(predict_this_year_eps)s, %(predict_this_year_pe)s,
            %(predict_next_year_eps)s, %(predict_next_year_pe)s,
            %(info_code)s, %(report_type)s, NOW())
        ON CONFLICT (ts_code, analyst, broker, publish_date, info_code)
        DO UPDATE SET
            stock_name = EXCLUDED.stock_name,
            industry = EXCLUDED.industry,
            title = EXCLUDED.title,
            s_rating = EXCLUDED.s_rating,
            s_rating_code = EXCLUDED.s_rating_code,
            em_rating = EXCLUDED.em_rating,
            rating_change = EXCLUDED.rating_change,
            target_priceHigh = EXCLUDED.target_priceHigh,
            target_priceLow = EXCLUDED.target_priceLow,
            predict_this_year_eps = EXCLUDED.predict_this_year_eps,
            predict_this_year_pe = EXCLUDED.predict_this_year_pe,
            predict_next_year_eps = EXCLUDED.predict_next_year_eps,
            predict_next_year_pe = EXCLUDED.predict_next_year_pe,
            report_type = EXCLUDED.report_type,
            synced_at = NOW()
    """
    for rec in records:
        try:
            cursor.execute(sql, rec)
            count += 1
        except Exception as e:
            logger.debug(f'Eastmoney upsert error for {rec.get("ts_code","?")}: {e}')
            cursor.connection.rollback()
    return count


def validate_coverage(cursor) -> dict:
    """Validate eastmoney_report data coverage by year and month. Returns gap report."""
    cursor.execute("""
        SELECT EXTRACT(YEAR FROM publish_date)::int AS yr,
               EXTRACT(MONTH FROM publish_date)::int AS mo,
               COUNT(*) AS cnt,
               MIN(publish_date) AS min_date,
               MAX(publish_date) AS max_date
        FROM eastmoney_report
        GROUP BY yr, mo
        ORDER BY yr, mo
    """)
    rows = cursor.fetchall()

    yearly = {}
    monthly = {}
    for yr, mo, cnt, min_d, max_d in rows:
        yearly.setdefault(yr, {'count': 0, 'months': set()})
        yearly[yr]['count'] += cnt
        yearly[yr]['months'].add(mo)
        monthly[(yr, mo)] = {'count': cnt, 'min': min_d, 'max': max_d}

    gaps = []
    for yr in sorted(yearly.keys()):
        info = yearly[yr]
        if info['count'] < 500:
            gaps.append(f"Year {yr}: only {info['count']} records")
        missing_months = set(range(1, 13)) - info['months']
        for mo in sorted(missing_months):
            gaps.append(f"Year {yr} Month {mo}: no data")

    return {
        'yearly': {yr: info['count'] for yr, info in sorted(yearly.items())},
        'total_dates': len(rows),
        'gaps': gaps,
        'total_records': sum(info['count'] for info in yearly.values()),
    }


def sync_eastmoney_reports(db_conn, start_year: int = 2017, end_year: int = 2026,
                           batch_size: int = 500,
                           mode_override: str = None, max_pages_override: int = None) -> dict:
    """
    Sync Eastmoney research reports to eastmoney_report table.

    Args:
        db_conn: Database connection
        start_year: Start year (default 2017)
        end_year: End year (default 2026)
        batch_size: Batch insert size
        mode_override: Ignored
        max_pages_override: Max pages per year (0=all)

    Returns:
        dict with sync statistics
    """
    session = requests.Session()
    stats = {'total_records': 0, 'pages_synced': 0, 'errors': 0, 'new': 0, 'years': {}}

    all_records = []
    cursor = db_conn.cursor()

    for year in range(start_year, end_year + 1):
        logger.info(f'Syncing Eastmoney reports for {year}...')
        page_no = 1
        year_total = 0

        while True:
            if max_pages_override and max_pages_override > 0 and page_no > max_pages_override:
                break

            data = fetch_page(year, page_no, page_size=50, session=session)
            if not data or not data.get('data'):
                break

            records = data['data']
            total_hits = data.get('hits', 0)

            for rec in records:
                p = parse_record(rec)
                if p:
                    all_records.append(p)
                    year_total += 1
                    stats['total_records'] += 1

            stats['pages_synced'] += 1

            if len(all_records) >= batch_size:
                count = _batch_upsert(cursor, all_records)
                stats['new'] += count
                db_conn.commit()
                all_records = []

            if page_no * 50 >= total_hits:
                break

            page_no += 1
            time.sleep(0.2)

        stats['years'][year] = year_total
        logger.info(f'  {year}: {year_total} records')

    if all_records:
        count = _batch_upsert(cursor, all_records)
        stats['new'] += count
        db_conn.commit()

    cursor.close()
    session.close()
    logger.info(f'Eastmoney sync complete: {stats}')
    return stats


if __name__ == '__main__':
    import sys
    sys.path.insert(0, r'D:\code\Python\ai_quant_python')
    from config.settings import settings

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    import psycopg2
    conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )

    result = sync_eastmoney_reports(conn, start_year=2017, end_year=2026)
    print(f'Result: {result}')

    conn.close()
