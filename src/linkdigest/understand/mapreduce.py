"""map-reduce 理解：分块摘要（并行）→ 汇总成报告。

跨语言在提示层解决，不设独立翻译阶段：无论原文是英/日/中，
最终报告一律以简体中文产出，金句保留原文并附意译。

失败策略：单块失败跳过并记账；汇总失败整体降级并标注。
绝不静默产出半份内容。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..capabilities.llm import LLMClient, LLMError
from ..capabilities.llm.base import JsonChat
from ..config import Settings
from ..contracts.models import Report, TranscriptBundle
from ..conventions.timecode import ts
from . import prompts
from .chunking import Chunk, chapters_block, chunk_bundle, digest_block
from .degraded import degraded_report
from .payload import report_from_payload


@dataclass(slots=True)
class ReportRun:
    """这一轮理解是怎么跑完的：mode 进 frontmatter，chunks 供排查。"""

    mode: str
    chunks: int


def build_report(
    cfg: Settings, bundle: TranscriptBundle, client: JsonChat | None = None
) -> tuple[Report, ReportRun]:
    client = client if client is not None else LLMClient(cfg)
    chunks = chunk_bundle(cfg, bundle)
    if not client.available:
        return degraded_report(bundle), ReportRun("degraded", len(chunks))

    workers = min(4, max(1, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = list(pool.map(lambda c: _map_chunk(client, c, bundle.language.detected), chunks))

    digests_text = "\n".join(
        digest_block(i + 1, chunk, digest) for i, (chunk, digest) in enumerate(zip(chunks, digests))
    )
    duration = bundle.duration or 0
    user = prompts.reduce_user().format(
        title=bundle.source.title or "未命名内容",
        platform=bundle.source.platform,
        author=bundle.source.author or "未知",
        duration=ts(duration) if duration else f"{bundle.quality.chars} 字",
        lang=bundle.language.detected,
        chars=bundle.quality.chars,
        digests=digests_text[: cfg.llm_chunk_chars * 4],
        chapters=chapters_block(bundle),
    )
    try:
        payload = client.chat_json(prompts.reduce_system(), user, max_tokens=6000)
    except LLMError as exc:
        client.stats.errors.append(f"reduce: {exc}")
        report = degraded_report(bundle)
        report.notes.append(f"汇总阶段调用失败（{exc}），已降级")
        return report, ReportRun("partial", len(chunks))

    report = report_from_payload(payload)
    report.notes.append(f"分块 {len(chunks)} 段，模型 {cfg.llm_model}")
    return report, ReportRun("llm", len(chunks))


def _map_chunk(client: JsonChat, chunk: Chunk, lang: str) -> dict:
    try:
        return client.chat_json(
            prompts.map_system(),
            prompts.map_user().format(
                start=ts(chunk.start), end=ts(chunk.end), lang=lang, body=chunk.body
            ),
            max_tokens=1600,
        )
    except LLMError as exc:
        client.stats.errors.append(f"map {ts(chunk.start)}: {exc}")
        return {"summary": "", "points": [], "data": [], "quotes": [], "terms": {}}
