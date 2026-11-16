"""产物读取：下游（API、脚本、外部 agent）要什么就从任务目录里取什么。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..conventions.jsonio import read_json

if TYPE_CHECKING:
    from .db import Store

ARTIFACTS = (
    "transcript.json",
    "report.json",
    "report.md",
    "report.html",
    "usage.json",
    "source.json",
    "config.json",
    "handoff/BRIEF.md",
    "handoff/answer.json",
)


def load_artifacts(store: "Store", job_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, key in [
        ("transcript.json", "bundle"),
        ("report.json", "report"),
        ("usage.json", "usage"),
        ("source.json", "source"),
        ("config.json", "config"),
    ]:
        path = store.job_path(job_id, name)
        if path.exists():
            out[key] = read_json(path)
    markdown_path = store.job_path(job_id, "report.md")
    if markdown_path.exists():
        out["markdown"] = markdown_path.read_text(encoding="utf-8")
    return out


def artifacts_present(store: "Store", job_id: str) -> dict[str, bool]:
    return {name: store.job_path(job_id, name).exists() for name in ARTIFACTS}
