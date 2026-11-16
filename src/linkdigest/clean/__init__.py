"""确定性清洗层：输入是任意实现产出的语音段，输出是可直接进契约的段列表。"""

from .hallucination import is_hallucination, load_blacklist
from .segments import (
    CleanResult,
    RawSegment,
    SpokenSegment,
    assign_idx,
    collapse_inline_repetition,
    normalize_segments,
    text_to_segments,
)
from .text import clean_text, comparison_key

__all__ = [
    "CleanResult",
    "RawSegment",
    "SpokenSegment",
    "assign_idx",
    "clean_text",
    "collapse_inline_repetition",
    "comparison_key",
    "is_hallucination",
    "load_blacklist",
    "normalize_segments",
    "text_to_segments",
]
