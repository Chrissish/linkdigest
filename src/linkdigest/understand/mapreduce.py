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
from ..capabilities.llm.context import (
    direct_input_budget,
    estimate_tokens,
    infer_context_tokens,
)
from ..config import Settings
from ..contracts.models import Report, TranscriptBundle
from ..contracts.spec import spec_text
from ..conventions.timecode import ts
from . import prompts
from .chunking import Chunk, chapters_block, chunk_bundle, digest_block, full_text
from .degraded import degraded_report
from .payload import _line, report_from_payload


@dataclass(slots=True)
class ReportRun:
    """这一轮理解是怎么跑完的：mode 进 frontmatter，chunks/path 供排查与展示。"""

    mode: str
    chunks: int
    path: str = "map-reduce"


def build_report(
    cfg: Settings, bundle: TranscriptBundle, client: JsonChat | None = None
) -> tuple[Report, ReportRun]:
    client = client if client is not None else LLMClient(cfg)
    chunks = chunk_bundle(cfg, bundle)
    if not client.available:
        return degraded_report(bundle), ReportRun("degraded", len(chunks))

    if _direct_fits(cfg, bundle):
        try:
            report = _direct_report(cfg, bundle, client)
            report.notes.append(f"直读全文 1 次调用，模型 {cfg.llm_model}")
            return report, ReportRun("llm", 1, path="direct")
        except LLMError as exc:
            # 直读失败（超限 / 网络 / 解析）自动退回 map-reduce，不浪费这次会话
            client.stats.errors.append(f"direct 回落 map-reduce: {exc}")

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


def _direct_fits(cfg: Settings, bundle: TranscriptBundle) -> bool:
    """全文塞不塞得下：窗口推断 + 语种感知估算，超了老实走 map-reduce。"""
    context = cfg.llm_context_tokens or infer_context_tokens(cfg.llm_model)
    transcript = full_text(bundle)
    prompt = prompts.direct_user().format(
        title=bundle.source.title or "未命名内容",
        platform=bundle.source.platform,
        author=bundle.source.author or "未知",
        duration=ts(bundle.duration or 0),
        lang=bundle.language.detected,
        chars=bundle.quality.chars,
        transcript=transcript,
        chapters=chapters_block(bundle),
    )
    ratio = cfg.llm_chars_per_token
    est = estimate_tokens(prompt, ratio) + estimate_tokens(spec_text(bundle.has_timeline), ratio)
    return est <= direct_input_budget(context)


def _direct_report(cfg: Settings, bundle: TranscriptBundle, client: JsonChat) -> Report:
    """单次直读：全文 + spec 一次出完整报告。可选层兜底复用 reduce 的。"""
    user = prompts.direct_user().format(
        title=bundle.source.title or "未命名内容",
        platform=bundle.source.platform,
        author=bundle.source.author or "未知",
        duration=ts(bundle.duration or 0),
        lang=bundle.language.detected,
        chars=bundle.quality.chars,
        transcript=full_text(bundle),
        chapters=chapters_block(bundle),
    )
    payload = _reduce_payload(client, bundle, user, max_tokens=9000)
    return report_from_payload(payload)


def _optional_layer_empty(report: Report) -> bool:
    """可选层是不是全空。单空正常（素材真的没有），全空基本是漏。"""
    return not (
        report.tldr or report.data_points or report.quotes or report.terms or report.controversies
    )


# 正文自己写出了「有争议 / 缺乏研究」这类判断，controversies 却是空的，那就是漏
_DISPUTE_MARKERS = (
    "争议",
    "矛盾",
    "未证实",
    "没有定论",
    "说法不一",
    "不清楚",
    "缺乏研究",
    "研究不足",
    "错误信息",
    "很难说",
    "尚无",
)


def _recover_controversies(client: JsonChat, payload: dict) -> dict:
    """质疑层空了而正文明确提到争议时，补一次只读正文的小抽取。

    整份重跑代价高（全文上万 token），争议又是最容易整层丢掉的一块：
    正文与 tldr 已写明「这一点有争议 / 可靠研究不足」，模型仍可能回空数组。
    这里只把正文喂回去抽争议，输入小、输出短，且限定「不许引入正文没有的信息」。
    """
    raw = payload.get("controversies")
    if isinstance(raw, str):
        raw = [raw] if raw.strip() and raw.strip() not in ("[]", "{}") else []
    # 判空要看过滤后的结果：模型有时回 [""] 或 [{}]，非空但一条也落不下来
    if [s for s in (_line(x) for x in (raw or [])) if s]:
        return payload
    body = str(payload.get("body") or "")
    if len(body) < 600 or sum(1 for m in _DISPUTE_MARKERS if m in body) < 2:
        return payload
    try:
        extra = client.chat_json(
            prompts.controversies_system(), prompts.controversies_user(body), max_tokens=1200
        )
    except LLMError as exc:
        client.stats.errors.append(f"争议层补抽: {exc}")
        return payload
    found = [s for s in (_line(x) for x in (extra.get("controversies") or [])) if s][:6]
    if found:
        payload["controversies"] = found
    return payload


def _reduce_payload(
    client: JsonChat, bundle: TranscriptBundle, user: str, *, max_tokens: int = 6000
) -> dict:
    """reduce 调用 + 可选层兜底。

    spec 给了省略许可（「拿不准就删」），模型偶尔把可选层整层留空而正文写得
    很满——这属于漏，不是简。正文可观而可选层全空时，把自检要求说透再问一次。
    max_tokens：map-reduce 的 reduce 稿正文短，6000 够；直读全文正文长得多，
    用更宽的口子，避免撞 finish=length 白烧一次重试。
    """
    payload = client.chat_json(prompts.reduce_system(bundle), user, max_tokens=max_tokens)
    first = report_from_payload(payload)
    if len(first.body) < 600 or not _optional_layer_empty(first):
        return _recover_controversies(client, payload)
    nudge = (
        user
        + "\n\n补充要求：上一稿的可选层（tldr / data_points / quotes / terms / controversies）"
        "全部留空，但正文篇幅可观——对照正文重新自检：结论性数字进 data_points、"
        "值得原样保留的话进 quotes、外文术语进 terms、主要结论进 tldr、"
        "矛盾与未证实的说法进 controversies。正文里确有素材的字段不许空。"
        "重新输出完整 JSON。"
    )
    try:
        return _recover_controversies(
            client, client.chat_json(prompts.reduce_system(bundle), nudge, max_tokens=max_tokens)
        )
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
