"""文本度量与截断。不涉及清洗规则（那在 clean 层），只做「看一眼、切一刀」。"""

from __future__ import annotations

import re

_CJK = r"\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def first_line(text: str, limit: int = 200) -> str:
    """取第一行非空文本，去掉 ANSI 色码——用来说清「底层到底报了什么」。"""
    lines = [line.strip() for line in strip_ansi(text).splitlines() if line.strip()]
    return lines[0][:limit] if lines else ""


def clip(text: str, limit: int) -> str:
    """按句子/词边界截断，别把单词切成 "st"、"th" 这种碎片。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for sep in ("。", "！", "？", ". ", "! ", "? ", "；", "，", ", "):
        index = window.rfind(sep)
        if index >= int(limit * 0.6):
            return window[: index + len(sep)].rstrip(" ,，")
    index = window.rfind(" ")
    if index >= int(limit * 0.6):
        return window[:index]
    return window.rstrip() + "…"


def script_of(text: str) -> str:
    """粗判书写系统。文章类源没有语言标签时，用它推断该按什么语言处理。"""
    sample = text[:4000]
    if not sample:
        return "unknown"
    cjk = len(re.findall(rf"[{_CJK}]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk and latin and min(cjk, latin) / max(cjk, latin) > 0.2:
        return "mixed"
    return "cjk" if cjk > latin else "latin"
