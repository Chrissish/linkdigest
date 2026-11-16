"""契约校验与 schema 导出。

校验故意不引入 jsonschema：只查「交给模型前必须成立」的不变量，跑得快、错误可读。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config.env import PROJECT_ROOT
from .models import TranscriptBundle

# 源码树里写回仓库的 schemas/；安装态没有仓库，落到当前目录，不往 site-packages 里写
SCHEMA_PATH = (PROJECT_ROOT or Path.cwd()) / "schemas" / "transcript_bundle.schema.json"


def validate_bundle(payload: dict) -> list[str]:
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


def export_schema(path: Path | None = None) -> Path:
    target = path or SCHEMA_PATH
    schema = TranscriptBundle.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
