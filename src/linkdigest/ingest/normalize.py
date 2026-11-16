"""把用户实际粘贴的内容还原成一个干净链接或本地路径。

处理的真实情况：终端复制来的 `?v\\=xxx`、JSON 里的 `https:\\/\\/`、
带书名号/引号的整句「看这个 https://… 挺好」、以及末尾的中文标点。
"""

from __future__ import annotations

import html as htmllib
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from .platforms import BARE_DOMAIN, SHELL_ESCAPES, TRAIL_PUNCT, URL_IN_TEXT


def normalize_input(raw: str) -> str:
    text = unicodedata.normalize("NFKC", (raw or "").strip())
    text = text.strip().strip("'\"`").strip()
    if not text:
        raise ValueError("输入为空")

    local = Path(text).expanduser()
    if local.exists():
        return str(local)

    text = htmllib.unescape(text)
    text = SHELL_ESCAPES.sub(r"\1", text)
    text = text.replace("\\", "").replace("\u200b", "").replace("\u2060", "")

    match = URL_IN_TEXT.search(text)
    if match:
        url = match.group(0).rstrip(TRAIL_PUNCT)
    else:
        url = text.rstrip(TRAIL_PUNCT)
        if BARE_DOMAIN.match(url):
            url = "https://" + url

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"无法识别的输入：{raw[:80]}（需要 http/https 链接或已存在的本地文件路径）")
    return url
