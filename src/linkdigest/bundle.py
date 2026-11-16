"""Bundle 构造与落盘：确定性层的出口。"""

from __future__ import annotations

import json
from pathlib import Path

from .config import PROJECT_ROOT
from .models import (
    Chapter,
    LanguageInfo,
    MediaMeta,
    Quality,
    Segment,
    SourceMeta,
    TranscriptBundle,
)
from .normalize import script_of
from .store import job_path, read_json, write_json

SCHEMA_PATH = PROJECT_ROOT / "schemas" / "transcript_bundle.schema.json"


def mechanical_chapters(segments: list[dict], target_sec: float = 300.0) -> list[Chapter]:
    """确定性切章：按目标时长切，优先落在长停顿处。标题留给下层模型改写。"""
    if not segments:
        return []
    has_time = segments[-1]["end"] > len(segments) * 1.5
    chapters: list[Chapter] = []
    bucket: list[dict] = []
    bucket_start = segments[0]["start"]

    for seg in segments:
        bucket.append(seg)
        span = seg["end"] - bucket_start
        is_last = seg is segments[-1]
        if not has_time:
            if len(bucket) >= 6 or is_last:
                chapters.append(Chapter(title=f"第 {len(chapters)+1} 节", start=bucket_start, end=seg["end"]))
                bucket, bucket_start = [], seg["end"]
            continue
        if span >= target_sec or is_last:
            chapters.append(
                Chapter(title=f"第 {len(chapters)+1} 节", start=bucket_start, end=seg["end"])
            )
            bucket, bucket_start = [], seg["end"]
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


def save_bundle(bundle: TranscriptBundle, job_id: str) -> Path:
    path = job_path(job_id, "transcript.json")
    write_json(path, json.loads(bundle.model_dump_json()))
    return path


def load_bundle(path: Path) -> TranscriptBundle:
    return TranscriptBundle(**read_json(path))


def validate_bundle(payload: dict) -> list[str]:
    """轻量校验：不引入 jsonschema 依赖，只查契约关键不变量。"""
    problems: list[str] = []
    if payload.get("bundle_version") != "1.0":
        problems.append("bundle_version 必须为 1.0")
    segs = payload.get("segments") or []
    if not segs:
        problems.append("segments 为空")
    prev_end = -1.0
    for i, seg in enumerate(segs):
        if seg.get("idx") != i:
            problems.append(f"segment #{i} idx 不连续")
            break
        if seg["end"] < seg["start"]:
            problems.append(f"segment #{i} 时间倒挂")
            break
        if seg["start"] < prev_end - 1.0:
            problems.append(f"segment #{i} 与上一段重叠异常")
            break
        if not str(seg.get("text", "")).strip():
            problems.append(f"segment #{i} 空文本")
            break
        prev_end = seg["end"]
    if payload.get("quality", {}).get("chars", 0) <= 0:
        problems.append("quality.chars 为零")
    return problems


def export_schema() -> Path:
    schema = TranscriptBundle.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    return SCHEMA_PATH
