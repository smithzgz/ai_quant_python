# -*- coding: utf-8 -*-
"""通用令牌桶限流器（MINUTE_DATA_PLAN.md 5.2）。

Tushare 有硬性限流（错误 "频率超限"），baostock 无硬限流但建议礼貌节流。
多调用方共享同一实例时按全局速率约束。
"""
import time
import threading


class RateLimiter:
    """线程安全的令牌桶：rate_per_sec 次/秒的匀速放行。"""

    def __init__(self, rate_per_sec: float = 0.0):
        self.rate = float(rate_per_sec) if rate_per_sec and rate_per_sec > 0 else 0.0
        self._interval = 1.0 / self.rate if self.rate > 0 else 0.0
        self._next_time = 0.0
        self._lock = threading.Lock()

    def acquire(self):
        """阻塞直到获得一个许可。rate<=0 时为直通。"""
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_time - now
            self._next_time = max(now, self._next_time) + self._interval
        if wait > 0:
            time.sleep(wait)
