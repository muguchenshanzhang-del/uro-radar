"""统一按配置里的时区取「今天」，不要用系统时间。

GitHub Actions 的 runner 是 UTC。定时任务在 UTC 23:30 触发，对应北京时间第二天
07:30 —— 这时候 date.today() 返回的还是前一天，页面日期会整体错一天，
「N 天内发表」的新鲜度加分也会跟着错。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Shanghai"

_current = None


def configure(name: str | None = None) -> None:
    global _current
    zone = name or os.environ.get("RADAR_TZ") or DEFAULT_TZ
    try:
        _current = ZoneInfo(zone)
    except Exception:
        # Windows 上没有 tzdata 包时会走到这里，退回固定 +8
        _current = timezone(timedelta(hours=8))


def _tz():
    if _current is None:
        configure(None)
    return _current


def now() -> datetime:
    return datetime.now(_tz())


def today() -> date:
    return now().date()
