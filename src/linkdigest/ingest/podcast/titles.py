"""标题模糊匹配。

播客单集标题跨平台常被改写（加 ft.、加集号、截断），必须模糊比，
但不能松到「取最新一集」——那会拿错音源却看不出来。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def title_key(title: str | None) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def title_score(a: str | None, b: str | None) -> float:
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if ka in kb or kb in ka:
        return 0.95
    return SequenceMatcher(None, ka, kb).ratio()
