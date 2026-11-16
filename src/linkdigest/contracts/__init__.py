"""契约层：确定性层与智能层之间唯一的通信格式。"""

from .builder import build_bundle, mechanical_chapters
from .models import Report, SourceMeta, TranscriptBundle
from .validation import export_schema, validate_bundle

__all__ = [
    "Report",
    "SourceMeta",
    "TranscriptBundle",
    "build_bundle",
    "export_schema",
    "mechanical_chapters",
    "validate_bundle",
]
