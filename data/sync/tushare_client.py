# -*- coding: utf-8 -*-
import time
import tushare as ts
from config.settings import settings
from utils.logger import get_logger

logger = get_logger("tushare_client")

_pro = None


def get_pro():
    global _pro
    if _pro is None:
        ts.set_token(settings.TUSHARE_TOKEN)
        _pro = ts.pro_api()
    return _pro


class TushareClient:
    def __init__(self, retry_count=3, base_delay=1.0, max_delay=30.0, sleep_interval=0.35):
        self.retry_count = retry_count
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep_interval = sleep_interval
        self.pro = get_pro()

    def call(self, api_name: str, **kwargs):
        method = getattr(self.pro, api_name, None)
        if method is None:
            raise ValueError(f"Unknown tushare API: {api_name}")

        last_exc = None
        for attempt in range(1, self.retry_count + 1):
            try:
                data = method(**kwargs)
                time.sleep(self.sleep_interval)
                return data
            except Exception as e:
                last_exc = e
                delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
                logger.warning(
                    f"API retry {attempt}/{self.retry_count}: {api_name} {e}, "
                    f"waiting {delay:.1f}s"
                )
                if attempt < self.retry_count:
                    time.sleep(delay)

        logger.error(f"API FAILED after {self.retry_count} retries: {api_name} {last_exc}")
        raise last_exc

    def call_by_date(self, api_name: str, date_value: str, date_field: str = "trade_date",
                     fields: str = None, extra_params: dict = None):
        params = {date_field: date_value}
        if fields:
            params["fields"] = fields
        if extra_params:
            params.update(extra_params)
        return self.call(api_name, **params)

    def get_trade_cal(self, exchange: str = "") -> list:
        data = self.call("trade_cal", exchange=exchange)
        if data is not None and not data.empty:
            return data.sort_values("cal_date").to_dict("records")
        return []
