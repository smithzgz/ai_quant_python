# -*- coding: utf-8 -*-
"""5 分钟线同步（baostock 数据源）— MINUTE_DATA_PLAN.md V1.2 §5。

设计要点：
- baostock 免费、无硬限流，但为礼貌节流默认 rate=3/s（RateLimiter）
- baostock 全局单连接不可多线程：夜间增量与回填器均为单线程
- bar 时间口径：baostock time 为 bar 结束时刻，入库统一转 bar 起始时刻（-5min），
  Asia/Shanghai 本地时间语义，timestamptz 存储
- vol 单位：股（与 daily.vol 的"手"差 100 倍，质检规则不得直接对比）
- 空响应语义：空数据 + error_code=0 视为正常停牌，推进 checkpoint；
  异常不推进；夜间清单 = 当前上市股票；退市股历史靠回填器补
- advisory lock 互斥：会话级锁，拿锁连接全程持有直至任务结束；
  回填器持锁期间夜间任务跳过本轮（不排队）
"""
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import baostock as bs
from sqlalchemy import text

from data.database.connection import engine
from data.sync.bulk_writer import copy_upsert_df
from data.sync.rate_limiter import RateLimiter
from utils.logger import get_logger

logger = get_logger("minute_sync")

TABLE = "stk_mins_5min"
PK_COLS = ["ts_code", "trade_time"]
LOCK_KEY = 811_800_501          # advisory lock key（固定常量，回填/夜间共用）
RATE_PER_SEC = 3.0              # baostock 礼貌节流
MAX_DAYS_PER_CALL = 366         # 单次拉取跨度上限（实测一年 11,760 行无截断，按年分段保险）
NIGHTLY_DEFAULT_DAYS = 7        # 夜间模式无 checkpoint 时的保护窗口（历史靠回填器）

_limiter = RateLimiter(RATE_PER_SEC)
_bs_logged_in = False

FIELDS = "date,time,code,open,high,low,close,volume,amount"
CST = timezone(timedelta(hours=8))  # Asia/Shanghai（无夏令时）

_EMPTY_DF = pd.DataFrame(
    columns=["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]
)


def _ensure_login():
    global _bs_logged_in
    if not _bs_logged_in:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")
        _bs_logged_in = True
        logger.info("baostock logged in")


def _reset_login():
    """连接级故障后重置 baostock 会话。"""
    global _bs_logged_in
    try:
        bs.logout()
    except Exception:
        pass
    _bs_logged_in = False
    _ensure_login()


def ts_to_bs(ts_code: str) -> str:
    """000001.SZ -> sz.000001"""
    code, _, suffix = ts_code.partition(".")
    return f"{suffix.lower()}.{code}"


def bs_to_ts(bs_code: str) -> str:
    """sz.000001 -> 000001.SZ"""
    suffix, _, code = bs_code.partition(".")
    return f"{code}.{suffix.upper()}"


def _fetch_5min(bs_code: str, start: date, end: date) -> pd.DataFrame:
    """拉取 [start, end] 闭区间 5min bar，返回标准列 DataFrame（可能为空）。

    trade_time 为 bar 起始时刻（tz-aware）；价格 float；vol 单位股。
    连接级故障（socket 断开等）自动重连重试 3 次；接口不支持（10004011）类
    永久错误直接抛出。
    """
    _ensure_login()
    rs = None
    last_exc = None
    for attempt in range(1, 4):
        _limiter.acquire()
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, FIELDS,
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                frequency="5", adjustflag="3",       # 不复权入库，qfq 查询时算
            )
            if rs.error_code != "0":
                raise RuntimeError(f"baostock query failed: {rs.error_code} {rs.error_msg}")
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if "10004011" in str(e):                 # 接口不支持，永久错误
                raise
            logger.warning(f"baostock fetch retry {attempt}/3 for {bs_code}: {e}")
            time.sleep(min(2 ** attempt, 30))
            _reset_login()
    if last_exc is not None:
        raise last_exc

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        return _EMPTY_DF.copy()

    df = pd.DataFrame(rows, columns=rs.fields)
    # time: YYYYMMDDHHMMSSsss（bar 结束时刻）-> bar 起始时刻（-5min）
    ts = pd.to_datetime(df["time"].str[:14], format="%Y%m%d%H%M%S") - timedelta(minutes=5)
    df["trade_time"] = ts.dt.tz_localize(CST)
    df["ts_code"] = df["code"].map(bs_to_ts)
    for col in ("open", "high", "low", "close", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["vol"] = pd.to_numeric(df["volume"], errors="coerce")
    out = df[["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]]
    return out.sort_values("trade_time").reset_index(drop=True)


def _fetch_segmented(bs_code: str, start: date, end: date) -> pd.DataFrame:
    """按年分段拉取，避免单次跨度过大（MAX_DAYS_PER_CALL）。"""
    if (end - start).days <= MAX_DAYS_PER_CALL:
        return _fetch_5min(bs_code, start, end)
    parts = []
    cur = start
    while cur <= end:
        seg_end = min(date(cur.year, 12, 31), end)
        part = _fetch_5min(bs_code, cur, seg_end)
        if not part.empty:
            parts.append(part)
        cur = seg_end + timedelta(days=1)
    if not parts:
        return _EMPTY_DF.copy()
    return pd.concat(parts, ignore_index=True)


class AdvisoryLock:
    """会话级 advisory lock 上下文：专用 psycopg2 连接全程持有。

    pg_try_advisory_lock 是会话级锁——连接关闭即释放，因此锁连接必须
    与业务连接分离并在整个任务期间保持打开。
    """

    def __init__(self, key: int):
        self.key = key
        self._conn = None

    def acquire(self) -> bool:
        self._conn = engine.raw_connection()
        cur = self._conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (self.key,))
        got = cur.fetchone()[0]
        if not got:
            self._conn.close()
            self._conn = None
        return got

    def release(self):
        if self._conn is None:
            return
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s)", (self.key,))
            self._conn.commit()
        except Exception as e:
            logger.warning(f"advisory unlock failed (连接关闭会自动释放): {e}")
        finally:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()


def _get_checkpoints(codes: list) -> dict:
    """{ts_code: last_sync_time (tz-aware datetime)}"""
    if not codes:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ts_code, last_sync_time FROM sync_code_checkpoint "
                "WHERE table_name = :t AND ts_code = ANY(:codes)"
            ),
            {"t": TABLE, "codes": codes},
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def _advance_checkpoint(ts_code: str, t: datetime):
    """推进 per-code 断点（upsert）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sync_code_checkpoint (table_name, ts_code, last_sync_time, updated_at) "
                "VALUES (:t, :c, :v, NOW()) "
                "ON CONFLICT (table_name, ts_code) DO UPDATE SET "
                "last_sync_time = EXCLUDED.last_sync_time, updated_at = NOW()"
            ),
            {"t": TABLE, "c": ts_code, "v": t},
        )


def _sync_one(ts_code: str, start: date, end: date) -> int:
    """同步单只股票 [start, end]，返回写入行数（空响应算 0 行但推进断点）。"""
    df = _fetch_segmented(ts_to_bs(ts_code), start, end)
    n = 0
    if df is not None and not df.empty:
        n = copy_upsert_df(TABLE, df, pk_cols=PK_COLS)
    # 空响应（停牌/假期）也算成功——推进到 end 当日末尾
    _advance_checkpoint(ts_code, datetime.combine(end, datetime.max.time(), tzinfo=CST))
    return n


def sync_codes(codes: list, start: date, end: date, cancel_check=None, progress_every: int = 50) -> dict:
    """回填入口（backfill_mins.py 调用）：抢锁 -> 逐股拉取写库 -> 推进断点。

    抢不到锁（夜间增量或另一回填进程持锁）直接抛异常。
    """
    if not codes:
        return {"total_records": 0, "codes": 0}

    lock = AdvisoryLock(LOCK_KEY)
    if not lock.acquire():
        raise RuntimeError(
            f"advisory lock {LOCK_KEY} is held (夜间增量或另一回填进程正在运行)，拒绝并发回填"
        )

    checkpoints = _get_checkpoints(codes)
    total_rows = 0
    empty_codes = 0
    done = 0
    try:
        for code in codes:
            if cancel_check and cancel_check():
                logger.info(f"backfill cancelled at {code}")
                raise Exception(f"sync cancelled at {code}")
            cp = checkpoints.get(code)
            seg_start = start if cp is None else max(start, cp.date())
            if seg_start > end:
                done += 1
                continue
            try:
                n = _sync_one(code, seg_start, end)
                total_rows += n
                if n == 0:
                    empty_codes += 1
            except Exception as e:
                logger.error(f"{TABLE} {code} [{seg_start}~{end}] failed: {e}")
            done += 1
            if done % progress_every == 0:
                logger.info(f"{TABLE}: {done}/{len(codes)} codes, {total_rows} rows")
    finally:
        lock.release()

    logger.info(f"{TABLE}: sync_codes done, {done} codes, {total_rows} rows, {empty_codes} empty")
    return {"total_records": total_rows, "codes": done, "empty": empty_codes}


def sync_nightly(raw_conn=None, mode: str = "incremental", max_pages=None, batch_size=None) -> dict:
    """夜间增量入口（engine._sync_custom 调用）：全市场从 per-code checkpoint 续采至今日。

    断点续采无上限——宕机多久就补多久；无 checkpoint 的新股票只拉
    最近 NIGHTLY_DEFAULT_DAYS 天（历史靠回填器，防止夜间意外全量回填）。
    回填器持锁时跳过本轮并告警（不排队——回填以小时计）。
    """
    del raw_conn, max_pages, batch_size  # 兼容 engine._sync_custom 签名，连接自管

    today = date.today()

    lock = AdvisoryLock(LOCK_KEY)
    if not lock.acquire():
        logger.warning(f"{TABLE}: advisory lock {LOCK_KEY} is held, skip nightly sync this round")
        return {"total_records": 0, "skipped": True}

    with engine.connect() as c:
        stock_rows = c.execute(text(
            "SELECT ts_code, list_date FROM stock_basic "
            "WHERE list_status = 'L' AND ts_code NOT LIKE '%%.BJ' "
            "ORDER BY ts_code"
        )).fetchall()
    codes = [r[0] for r in stock_rows]

    if not codes:
        logger.warning(f"{TABLE}: stock_basic empty, skip")
        lock.release()
        return {"total_records": 0}

    checkpoints = _get_checkpoints(codes)
    t0 = time.time()
    total_rows = 0
    done = 0
    no_cp = 0
    try:
        for code in codes:
            cp = checkpoints.get(code)
            if cp is None:
                no_cp += 1
                seg_start = today - timedelta(days=NIGHTLY_DEFAULT_DAYS)
            else:
                seg_start = cp.date()
            if seg_start > today:
                done += 1
                continue
            try:
                n = _sync_one(code, seg_start, today)
                total_rows += n
            except Exception as e:
                logger.error(f"{TABLE} {code} nightly failed: {e}")
            done += 1
            if done % 500 == 0:
                rate = done / max(time.time() - t0, 1)
                logger.info(f"{TABLE}: nightly {done}/{len(codes)} codes, {total_rows} rows, {rate:.1f} codes/s")
    finally:
        lock.release()

    elapsed = time.time() - t0
    logger.info(f"{TABLE}: nightly done, {done} codes ({no_cp} without checkpoint), "
                f"{total_rows} rows, {elapsed/60:.1f} min")
    return {"total_records": total_rows, "codes": done, "no_checkpoint": no_cp}
