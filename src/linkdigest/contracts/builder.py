"""由清洗结果构造契约对象：切章规则放这儿，因为章节边界是确定性信息。"""

from __future__ import annotations

from ..conventions.text import script_of
from .models import (
    Chapter,
    LanguageInfo,
    MediaMeta,
    Quality,
    Segment,
    SourceMeta,
    TranscriptBundle,
)


def mechanical_chapters(segments: list[dict], target_sec: float = 300.0) -> list[Chapter]:
    """确定性切章：按目标时长切，优先落在长停顿处。标题留给下层模型改写。

    文章类源没有时间轴（段落 start 是下标），改按段数切——每 6 段一节。
    """
    if not segments:
        return []
    has_time = segments[-1]["end"] > len(segments) * 1.5
    chapters: list[Chapter] = []
    bucket_start = segments[0]["start"]
    bucket_len = 0

    for seg in segments:
        bucket_len += 1
        is_last = seg is segments[-1]
        span = seg["end"] - bucket_start
        by_count = not has_time and bucket_len >= 6
        by_time = has_time and span >= target_sec
        if by_count or by_time or is_last:
            chapters.append(Chapter(title=f"第 {len(chapters) + 1} 节", start=bucket_start, end=seg["end"]))
            bucket_start = seg["end"]
            bucket_len = 0
    return chapters


def build_bundle(
    *,
    bundle_id: str,
    source: SourceMeta,
    segments: list[dict],
    flags: dict[str, int],
    warnings: list[str],
    stats: dict,
    language: str,
    language_probability: float,
    asr_engine: str,
    media: MediaMeta | None,
) -> TranscriptBundle:
    texts = "".join(s["text"] for s in segments)
    models = [
        Segment(
            idx=i,
            start=float(s["start"]),
            end=float(s["end"]),
            text=s["text"],
            confidence=s.get("confidence"),
            speaker=s.get("speaker"),
            flags=s.get("flags", []),
        )
        for i, s in enumerate(segments)
    ]
    return TranscriptBundle(
        id=bundle_id,
        source=source,
        media=media,
        language=LanguageInfo(
            detected=language, probability=round(language_probability, 4), script=script_of(texts)
        ),
        segments=models,
        chapters=mechanical_chapters(segments),
        quality=Quality(
            asr_engine=asr_engine,
            segment_count=len(models),
            chars=sum(len(m.text) for m in models),
            avg_confidence=stats.get("avg_confidence"),
            flags=flags,
            warnings=warnings,
        ),
    )
