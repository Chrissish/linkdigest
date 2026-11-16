"""规则清洗里的文本级处理：标点、零宽字符、CJK 间距。

NFKC 会把中文全角标点压成半角（，→,），中文排版不能接受。
所以先把它们换成私用区占位符，归一后再换回来。
"""

from __future__ import annotations

import re
import unicodedata

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
CJK = r"\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"
CJK_SPACE_RE = re.compile(rf"(?<=[{CJK}])\s+(?=[{CJK}])")
MULTI_SPACE_RE = re.compile(r"[ \t\u00a0]{2,}")

PUNCT_GUARD = {
    "，": "\ue000",
    "。": "\ue001",
    "、": "\ue002",
    "；": "\ue003",
    "：": "\ue004",
    "？": "\ue005",
    "！": "\ue006",
    "（": "\ue007",
    "）": "\ue008",
    "“": "\ue009",
    "”": "\ue00a",
    "‘": "\ue00b",
    "’": "\ue00c",
    "《": "\ue00d",
    "》": "\ue00e",
    "—": "\ue00f",
    "…": "\ue010",
}
PUNCT_RESTORE = {v: k for k, v in PUNCT_GUARD.items()}
# Whisper 输出的中文常混半角标点（"第一,成本。"），排版上很刺眼。
HALF_IN_CJK_RE = re.compile(rf"(?<=[{CJK}])\s*([,:;!?])\s*(?=[{CJK}])")
HALF_TO_FULL = {",": "，", ":": "：", ";": "；", "!": "！", "?": "？"}


def comparison_key(text: str) -> str:
    """用于重复检测的比较键：去标点、去空白、小写。"""
    return re.sub(rf"[^\w{CJK}]+", "", text).lower()


def clean_text(text: str) -> str:
    for source, guard in PUNCT_GUARD.items():
        text = text.replace(source, guard)
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)
    for guard, source in PUNCT_RESTORE.items():
        text = text.replace(guard, source)
    text = text.replace("\u3000", " ")
    text = MULTI_SPACE_RE.sub(" ", text)
    text = CJK_SPACE_RE.sub("", text)
    text = HALF_IN_CJK_RE.sub(lambda m: HALF_TO_FULL[m.group(1)], text)
    return text.strip()
