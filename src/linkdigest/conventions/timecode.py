"""时间码的唯一实现：秒 ↔ mm:ss / hh:mm:ss。

报告、Markdown、HTML、交接单都从这里取，避免各处格式不一致。
"""

from __future__ import annotations

import re
from typing import Any


def ts(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", value.strip())
        if match:
            hours = int(match.group(1) or 0)
            return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    return None
