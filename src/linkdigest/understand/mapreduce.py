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
        payload = _reduce_payload(client, bundle, user)
    except LLMError as exc:
        client.stats.errors.append(f"reduce: {exc}")
        report = degraded_report(bundle)
        report.notes.append(f"汇总阶段调用失败（{exc}），已降级")
        return report, ReportRun("partial", len(chunks))

    report = report_from_payload(payload)
    report.notes.append(f"分块 {len(chunks)} 段，模型 {cfg.llm_model}")
    return report, ReportRun("llm", len(chunks))


def _optional_layer_empty(report: Report) -> bool:
    """可选层是不是全空。单空正常（素材真的没有），全空基本是漏。"""
    return not (
        report.tldr or report.data_points or report.quotes or report.terms or report.controversies
    )


def _reduce_payload(client: JsonChat, bundle: TranscriptBundle, user: str) -> dict:
    """reduce 调用 + 可选层兜底。

    spec 给了省略许可（「拿不准就删」），模型偶尔把可选层整层留空而正文写得
    很满——这属于漏，不是简。正文可观而可选层全空时，把自检要求说透再问一次。
    """
    payload = client.chat_json(prompts.reduce_system(bundle), user, max_tokens=6000)
    first = report_from_payload(payload)
    if len(first.body) < 600 or not _optional_layer_empty(first):
        return payload
    nudge = (
        user
        + "\n\n补充要求：上一稿的可选层（tldr / data_points / quotes / terms / controversies）"
        "全部留空，但正文篇幅可观——对照正文重新自检：结论性数字进 data_points、"
        "值得原样保留的话进 quotes、外文术语进 terms、主要结论进 tldr、"
        "矛盾与未证实的说法进 controversies。正文里确有素材的字段不许空。"
        "重新输出完整 JSON。"
    )
    try:
        return client.chat_json(prompts.reduce_system(bundle), nudge, max_tokens=6000)
    except LLMError as exc:
        client.stats.errors.append(f"reduce 兜底重问: {exc}")
        return payload


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
