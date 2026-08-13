from __future__ import annotations

from datetime import datetime, timedelta


def trailing_windows(as_of: datetime, days: int = 7) -> tuple[datetime, datetime, datetime]:
    """返回前一窗口起点、当前窗口起点、半开区间终点。

    终点向后移动一微秒，使锚点本身被纳入，同时每个窗口严格等长。
    """
    end = as_of + timedelta(microseconds=1)
    current_start = end - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    return previous_start, current_start, end
