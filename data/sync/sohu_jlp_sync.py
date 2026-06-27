# -*- coding: utf-8 -*-
"""
搜狐金罗盘 - 券商研究员推荐数据同步
数据来源: https://q.stock.sohu.com/jlp/res/listresv2.up
"""
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

SOHU_JLP_URL = 'https://q.stock.sohu.com/jlp/res/listresv2.up'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Referer': 'https://q.stock.sohu.com/jlp/res/listresv2.up',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# Form 1 base fields (all 11 hidden fields required for pagination to work)
BASE_FORM_FIELDS = [
    ('query.induCode', ''),
    ('query.secCode', ''),
    ('query.secName', ''),
    ('query.bestAnalyst', 'false'),
    ('query.indiCode', ''),
    ('query.indiName', ''),
    ('query.orgCode', ''),
    ('query.orgName', ''),
    ('query.due', 'false'),
    ('priceLevel', '0'),
]


def fetch_page(page_num: int, session: requests.Session = None, retries: int = 3,
               last_query: str = '') -> Optional[str]:
    """Fetch a single page of JLP data using Form 1 fields."""
    data = dict(BASE_FORM_FIELDS)
    data['pageNum'] = str(page_num)
    if last_query:
        data['lastQuery'] = last_query

    sess = session or requests.Session()
    for attempt in range(retries):
        try:
            resp = sess.post(SOHU_JLP_URL, data=data, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            html = resp.content.decode('gbk', errors='replace')
            if '<tbody>' in html and 'class="code"' in html:
                return html
            logger.warning(f'Page {page_num}: no tbody found (attempt {attempt+1})')
        except Exception as e:
            logger.warning(f'Page {page_num} fetch error (attempt {attempt+1}): {e}')
        time.sleep(1)
    return None


def parse_page(html: str) -> List[Dict]:
    """Parse a page of JLP HTML into structured records."""
    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.find_all('tr', class_='code')
    records = []

    for row in rows:
        try:
            record = _parse_row(row)
            if record and record.get('ts_code'):
                records.append(record)
        except Exception as e:
            logger.debug(f'Row parse error: {e}')
    return records


def _parse_row(row) -> Optional[Dict]:
    """Parse a single table row into a record dict."""
    code = row.get('code', '').replace('cn_', '')
    if not code:
        return None

    # Convert to tushare format: 600000 -> 600000.SH, 000001 -> 000001.SZ
    ts_code = _code_to_ts(code)
    if not ts_code:
        return None

    # Analyst names
    td1 = row.find('td', class_='td1')
    analysts = []
    if td1:
        for a in td1.find_all('a'):
            name = a.get_text(strip=True)
            if name:
                analysts.append(name)
    analyst_str = '|'.join(analysts) if analysts else ''

    # Stock name
    td2 = row.find('td', class_='td2')
    stock_name = td2.find('a').get_text(strip=True) if td2 and td2.find('a') else ''

    # Industry
    td3 = row.find('td', class_='td3')
    industry = td3.find('a').get_text(strip=True) if td3 and td3.find('a') else ''

    # Comment date
    td4 = row.find('td', class_='td4')
    comment_date = td4.get_text(strip=True) if td4 else ''

    # Comment price
    td5 = row.find('td', class_='td5')
    comment_price = _parse_float(td5.get_text(strip=True)) if td5 else None

    # Target price
    td7 = row.find('td', class_='td7')
    target_price = _parse_float(td7.get_text(strip=True)) if td7 else None

    # Target space
    td8 = row.find('td', class_='td8')
    target_space = td8.get_text(strip=True) if td8 else ''

    # 20-day data
    td11_ = row.find('td', class_='td11_')
    price_20d = _parse_float(td11_.get_text(strip=True)) if td11_ else None

    td9s = row.find_all('td', class_='td9')
    change_20d = td9s[0].get_text(strip=True) if td9s else ''
    profit_20d = td9s[0].get_text(strip=True) if len(td9s) > 1 else ''

    td10s = row.find_all('td', class_='td10')
    profit_20d = td10s[0].get_text(strip=True) if td10s else ''

    # 60-day data
    td9_ = row.find('td', class_='td9_')
    price_60d = _parse_float(td9_.get_text(strip=True)) if td9_ else None

    td11s = row.find_all('td', class_='td11')
    change_60d = td11s[0].get_text(strip=True) if td11s else ''

    td12s = row.find_all('td', class_='td12')
    profit_60d = td12s[0].get_text(strip=True) if td12s else ''

    # Broker
    last_td = row.find('td', class_='last')
    broker = last_td.find('a').get_text(strip=True) if last_td and last_td.find('a') else ''

    # Recommend reason - extract from HTML comments
    recommend_reason = _extract_reason(row)

    return {
        'ts_code': ts_code,
        'stock_name': stock_name,
        'industry': industry,
        'analyst': analyst_str,
        'broker': broker,
        'comment_date': comment_date,
        'comment_price': comment_price,
        'target_price': target_price,
        'target_space': target_space,
        'price_20d': price_20d,
        'change_20d': change_20d,
        'profit_20d': profit_20d,
        'price_60d': price_60d,
        'change_60d': change_60d,
        'profit_60d': profit_60d,
        'recommend_reason': recommend_reason,
    }


def _extract_reason(row) -> str:
    """Extract recommendation reason from HTML comments in the row."""
    row_html = str(row)
    comments = re.findall(r'<!--(.*?)-->', row_html, re.DOTALL)
    for comment_html in comments:
        if 'td13' in comment_html:
            # Get from hidden div text (full reason)
            div_match = re.search(r'<div[^>]*display\s*:\s*none[^>]*>(.*?)</div>', comment_html, re.DOTALL | re.I)
            if div_match:
                text = re.sub(r'<[^>]+>', '', div_match.group(1)).strip()
                # Decode HTML entities
                text = text.replace('&ldquo;', '"').replace('&rdquo;', '"')
                text = text.replace('&mdash;', '—').replace('&middot;', '·')
                text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    return text
            # Fallback: get from title attribute of log1 link
            title_match = re.search(r'action="log1"\s+title="([^"]*)"', comment_html)
            if title_match:
                return title_match.group(1).strip()
    return ''


def _code_to_ts(code: str) -> Optional[str]:
    """Convert 6-digit code to tushare ts_code format."""
    if not code or len(code) != 6 or not code.isdigit():
        return None
    if code.startswith('6'):
        return f'{code}.SH'
    elif code.startswith(('0', '3')):
        return f'{code}.SZ'
    elif code.startswith(('4', '8')):
        return f'{code}.BJ'
    return None


def _parse_float(s: str) -> Optional[float]:
    """Parse a float from string, handling % and --."""
    if not s or s.strip() in ('--', '-', '', 'N/A'):
        return None
    s = s.strip().replace('%', '').replace(',', '')
    try:
        return float(s)
    except ValueError:
        return None


def get_total_pages(html: str) -> int:
    """Extract total pages from HTML."""
    m = re.search(r'(\d+)/(\d+)', html)
    if m:
        return int(m.group(2))
    return 0


def get_last_query(html: str) -> str:
    """Extract lastQuery value from HTML."""
    m = re.search(r'name="lastQuery"\s+value="([^"]*)"', html)
    if m:
        return m.group(1)
    return ''


def validate_coverage(cursor) -> dict:
    """Validate JLP data coverage by year and month. Returns gap report."""
    cursor.execute("""
        SELECT EXTRACT(YEAR FROM comment_date)::int AS yr,
               EXTRACT(MONTH FROM comment_date)::int AS mo,
               COUNT(*) AS cnt,
               MIN(comment_date) AS min_date,
               MAX(comment_date) AS max_date
        FROM sohu_jlp
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

    # Detect gaps: years with < 1000 records, or months with 0 records
    gaps = []
    for yr in sorted(yearly.keys()):
        info = yearly[yr]
        if info['count'] < 1000:
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


def _check_record_exists(cursor, record: Dict) -> bool:
    """Check if a record already exists in the database."""
    cursor.execute(
        "SELECT 1 FROM sohu_jlp WHERE ts_code=%s AND analyst=%s AND broker=%s AND comment_date=%s AND comment_price=%s",
        (record['ts_code'], record['analyst'], record['broker'], record['comment_date'], record['comment_price'])
    )
    return cursor.fetchone() is not None


def sync_sohu_jlp(db_conn, mode: str = 'incremental', max_pages: int = 0,
                   start_page: int = 1, batch_size: int = 50,
                   mode_override: str = None, max_pages_override: int = None) -> dict:
    """
    Sync Sohu JLP data to PostgreSQL.
    Pagination requires submitting all Form 1 hidden fields.
    lastQuery is extracted from page 1 and reused for all pages.

    Args:
        db_conn: Database connection
        mode: 'full' or 'incremental'
        max_pages: Max pages to sync (0=all)
        start_page: Starting page number
        batch_size: Pages per batch insert
        mode_override: Override mode from engine config
        max_pages_override: Override max_pages from engine config

    Returns:
        dict with sync statistics
    """
    if mode_override:
        mode = mode_override
    if max_pages_override is not None:
        max_pages = max_pages_override

    session = requests.Session()
    stats = {'total_records': 0, 'pages_synced': 0, 'errors': 0, 'new': 0, 'updated': 0}

    # Get total_pages and lastQuery from page 1
    html = fetch_page(1, session)
    if not html:
        logger.error('Failed to fetch page 1')
        return stats

    total_pages = get_total_pages(html)
    last_query = get_last_query(html)
    logger.info(f'JLP API: total_pages={total_pages}, lastQuery={last_query[:60]}...')

    if max_pages > 0:
        total_pages = min(total_pages, max_pages)

    all_records = []
    cursor = db_conn.cursor()
    stop_sync = False

    logger.info(f'Starting {mode} sync: {total_pages} pages, batch_size={batch_size}')

    for page_num in range(start_page, total_pages + 1):
        page_html = fetch_page(page_num, session, last_query=last_query)

        if not page_html:
            stats['errors'] += 1
            continue

        records = parse_page(page_html)
        stats['pages_synced'] += 1
        stats['total_records'] += len(records)

        # Incremental mode: stop when we hit existing records
        if mode == 'incremental' and records:
            new_records = []
            for rec in records:
                if _check_record_exists(cursor, rec):
                    stop_sync = True
                    break
                new_records.append(rec)
            records = new_records

        all_records.extend(records)

        # Batch insert every N pages
        if len(all_records) >= batch_size * 13:
            count = _batch_upsert(cursor, all_records)
            stats['new'] += count
            db_conn.commit()
            all_records = []

        # Progress log
        if page_num % 500 == 0 or page_num == total_pages:
            logger.info(f'JLP progress: page {page_num}/{total_pages} ({page_num*100//total_pages}%), records={stats["total_records"]}, new={stats["new"]}')

        if stop_sync:
            logger.info(f'Incremental sync: reached existing data at page {page_num}, stopping')
            break

        # Rate limiting
        if page_num % 10 == 0:
            time.sleep(0.5)

    # Final batch
    if all_records:
        count = _batch_upsert(cursor, all_records)
        stats['new'] += count
        db_conn.commit()

    # Validate coverage
    coverage = validate_coverage(cursor)
    stats['coverage'] = coverage
    if coverage['gaps']:
        logger.warning(f'Data gaps detected: {len(coverage["gaps"])} issues')
        for gap in coverage['gaps'][:20]:
            logger.warning(f'  Gap: {gap}')
        if len(coverage['gaps']) > 20:
            logger.warning(f'  ... and {len(coverage["gaps"]) - 20} more gaps')

    cursor.close()
    session.close()
    logger.info(f'JLP sync complete: {stats}')
    return stats


def _batch_upsert(cursor, records: List[Dict]) -> int:
    """Batch upsert records into PostgreSQL. Returns count of upserted records."""
    count = 0

    sql = """
        INSERT INTO sohu_jlp (ts_code, stock_name, industry, analyst, broker,
            comment_date, comment_price, target_price, target_space,
            price_20d, change_20d, profit_20d, price_60d, change_60d, profit_60d,
            recommend_reason, synced_at)
        VALUES (%(ts_code)s, %(stock_name)s, %(industry)s, %(analyst)s, %(broker)s,
            %(comment_date)s, %(comment_price)s, %(target_price)s, %(target_space)s,
            %(price_20d)s, %(change_20d)s, %(profit_20d)s, %(price_60d)s, %(change_60d)s, %(profit_60d)s,
            %(recommend_reason)s, NOW())
        ON CONFLICT (ts_code, analyst, broker, comment_date, comment_price)
        DO UPDATE SET
            stock_name = EXCLUDED.stock_name,
            industry = EXCLUDED.industry,
            target_price = EXCLUDED.target_price,
            target_space = EXCLUDED.target_space,
            price_20d = EXCLUDED.price_20d,
            change_20d = EXCLUDED.change_20d,
            profit_20d = EXCLUDED.profit_20d,
            price_60d = EXCLUDED.price_60d,
            change_60d = EXCLUDED.change_60d,
            profit_60d = EXCLUDED.profit_60d,
            recommend_reason = EXCLUDED.recommend_reason,
            synced_at = NOW()
    """

    for rec in records:
        try:
            cursor.execute(sql, rec)
            count += 1
        except Exception as e:
            logger.debug(f'Upsert error: {e}')

    return count


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

    max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    result = sync_sohu_jlp(conn, mode='full', max_pages=max_pages)
    print(f'Result: {result}')

    conn.close()
