"""两种形态同源落盘：report.md 给终端与版本库，report.html 给人读。

收在这里是为了让「加一种输出格式」只改一处——管道与外部交接都走它。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts.models import Report, TranscriptBundle
from .html import render_html
from .markdown import render_markdown

if TYPE_CHECKING:
    from ..jobs.db import Store


def save_report(
    store: "Store",
    job_id: str,
    bundle: TranscriptBundle,
    report: Report,
    extra: dict | None = None,
) -> tuple[Path, Path]:
    md_path = store.job_path(job_id, "report.md")
    md_path.write_text(render_markdown(bundle, report, extra), encoding="utf-8")
    html_path = store.job_path(job_id, "report.html")
    html_path.write_text(render_html(bundle, report, extra), encoding="utf-8")
    return md_path, html_path
