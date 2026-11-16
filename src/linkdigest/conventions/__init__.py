"""跨层共用的小工具：时间码、命名、文本度量、JSON 落盘、资源读取。

这里只放「没有业务含义」的东西。凡是需要知道平台、报告、任务的逻辑，都不属于这一层。
"""

from .jsonio import read_json, write_json
from .naming import slugify
from .text import clip, first_line, script_of, strip_ansi
from .timecode import parse_ts, ts

__all__ = [
    "clip",
    "first_line",
    "parse_ts",
    "read_json",
    "script_of",
    "slugify",
    "strip_ansi",
    "ts",
    "write_json",
]
