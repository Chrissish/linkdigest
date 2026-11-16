"""产物读取：下游（API、脚本、外部 agent）要什么就从任务目录里取什么。"""

from __future__ import annotations

from typing import Any

from ..conventions.jsonio import read_json
from .layout import job_path

ARTIFACTS = (
    "transcript.json",
    "report.json",
    "report.md",
    "report.html",
    "usage.json",
    "source.json",
    "handoff/BRIEF.md",
    "handoff/answer.json",
)


def load_artifacts(job_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, key in [
        ("transcript.json", "bundle"),
        ("report.json", "report"),
        ("usage.json", "usage"),
        ("source.json", "source"),
    ]:
        path = job_path(job_id, name)
        if path.exists():
            out[key] = read_json(path)
    markdown_path = job_path(job_id, "report.md")
    if markdown_path.exists():
        out["markdown"] = markdown_path.read_text(encoding="utf-8")
    return out


def artifacts_present(job_id: str) -> dict[str, bool]:
    return {name: job_path(job_id, name).exists() for name in ARTIFACTS}
