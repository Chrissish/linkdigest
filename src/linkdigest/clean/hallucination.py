"""幻觉段识别。

黑名单不在代码里：内置表是 `resources/asr/hallucinations.txt`，
用户追加的写 `data/hallucinations.txt`，两边合并生效。
"""

from __future__ import annotations

from ..config import Settings
from ..conventions.resources import text as resource_text
from .text import comparison_key


def load_blacklist(cfg: Settings) -> tuple[str, ...]:
    builtin = _lines(resource_text("asr/hallucinations.txt"))
    extra: list[str] = []
    path = cfg.data_dir / "hallucinations.txt"
    if path.exists():
        extra = _lines(path.read_text(encoding="utf-8"))
    return tuple(builtin) + tuple(extra)


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.startswith("#")]


def is_hallucination(text: str, blacklist: tuple[str, ...]) -> bool:
    key = comparison_key(text)
    if not key:
        return True
    if len(key) <= 2:
        return True
    # 同一字符反复（"啊啊啊" / "哈哈哈哈"）：Whisper 在噪声段的典型产物，无信息量
    if len(set(key)) == 1 and len(key) <= 6:
        return True
    return any(
        key == comparison_key(item) or (len(comparison_key(item)) > 8 and comparison_key(item) in key)
        for item in blacklist
    )
