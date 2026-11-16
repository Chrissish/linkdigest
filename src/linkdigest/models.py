"""契约数据模型。与 schemas/transcript_bundle.schema.json 一一对应。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["video", "audio", "article", "local"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceMeta(_Base):
    kind: Kind
    platform: str
    url: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    duration_sec: float | None = None
    lang_hint: str | None = None
    extractor: str
    extra: dict[str, Any] = Field(default_factory=dict)


class MediaMeta(_Base):
    audio_path: str
    audio_sha256: str
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    bytes: int | None = None


class LanguageInfo(_Base):
    detected: str
    probability: float
    script: str | None = None


class Segment(_Base):
    idx: int
    start: float
    end: float
    text: str = Field(min_length=1)
    speaker: str | None = None
    confidence: float | None = None
    flags: list[str] = Field(default_factory=list)


class Chapter(_Base):
    title: str
    start: float
    end: float
    summary: str | None = None


class Quality(_Base):
    asr_engine: str
    segment_count: int
    chars: int
    avg_confidence: float | None = None
    distinct_speakers: int | None = None
    flags: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class TranscriptBundle(_Base):
    bundle_version: Literal["1.0"] = "1.0"
    id: str
    source: SourceMeta
    media: MediaMeta | None = None
    language: LanguageInfo
    segments: list[Segment]
    chapters: list[Chapter] = Field(default_factory=list)
    quality: Quality
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def full_text(self) -> str:
        return "\n".join(seg.text for seg in self.segments)

    def char_count(self) -> int:
        return sum(len(seg.text) for seg in self.segments)


class ReportSection(_Base):
    title: str
    start: float | None = None
    end: float | None = None
    points: list[str] = Field(default_factory=list)


class Quote(_Base):
    text: str
    start: float | None = None
    note: str | None = None


class DataPoint(_Base):
    label: str
    value: str
    start: float | None = None


class Report(_Base):
    # —— 契约层：机器渲染与导航依赖这三个 ——
    one_liner: str = ""
    tags: list[str] = Field(default_factory=list)   # 内容标签词，≤5，供知识库归类
    sections: list[ReportSection] = Field(default_factory=list)
    # —— 自由层：深度由内容决定，不设字数上限 ——
    intent: str = ""          # 这份报告给谁看、要回答什么问题
    body: str = ""            # 自由 Markdown 正文（深读用）
    appendix: str = ""        # 内容特有结构：时间线 / 代码 / 人物表 / 对照表
    # —— 可选层：有则写，无则空 ——
    tldr: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    data_points: list[DataPoint] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    controversies: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    terms: dict[str, str] = Field(default_factory=dict)
    degraded: bool = False
    notes: list[str] = Field(default_factory=list)
