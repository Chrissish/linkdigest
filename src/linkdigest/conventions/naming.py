"""目录名片段：把任意标题压成文件系统能接受、人眼能认的样子。"""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_CHARS = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+")


def slugify(text: str | None, limit: int = 40) -> str:
    """保留中英日文字与数字，其余折叠成单个连字符。"""
    value = _SAFE_CHARS.sub("-", (text or "").strip()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if len(value) > limit:
        value = value[:limit].rstrip("-")
    return value or "untitled"


def source_hint(value: str) -> str:
    """没抓到标题时，从输入里挤出一个能认出来的名字（域名 + 视频 id / 文件名）。"""
    if not value:
        return ""
    if "://" in value:
        from urllib.parse import parse_qs, urlsplit

        # 老记录里存过被 shell 转义过的链接（watch\?v\=xxx），先还原再解析
        value = value.replace("\\?", "?").replace("\\=", "=").replace("\\/", "/")
        parts = urlsplit(value)
        host = (parts.hostname or "").replace("www.", "").split(".")[0]
        tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        for key in ("v", "id", "bvid", "aid"):
            if query.get(key):
                tail = query[key][0]
                break
        return slugify(f"{host}-{tail}" if tail and tail != host else host, 40)
    return slugify(Path(value).stem, 40)
