"""清洗流水线：ASR 的错不会自己消失，必须在落盘前用确定性规则压掉。

只做规则能保证的事：标点归一、时间戳单调、幻觉段丢弃、卡带压缩、碎片合并。
判断类问题留给下层模型（并在报告里标注）。所有动作计数进 quality.flags。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .hallucination import is_hallucination, load_blacklist
from .text import CJK, clean_text, comparison_key

SENT_END = tuple("。！？.!?…；;")


class SpokenSegment(Protocol):
    """只要形状对得上就能进清洗，不绑定具体的 ASR 实现。"""

    start: float
    end: float
    text: str
    confidence: float | None


@dataclass(slots=True)
class RawSegment:
    start: float
    end: float
    text: str
    confidence: float | None = None


@dataclass(slots=True)
class CleanResult:
    segments: list[dict]
    flags: dict[str, int]
    warnings: list[str]
    stats: dict


def collapse_inline_repetition(text: str, min_repeat: int = 4) -> tuple[str, int]:
    """同一短语在单段内连续重复（Whisper 卡带），压成一次。"""
    tokens = list(re.finditer(rf"[{CJK}]{{2,}}|[A-Za-z']+", text))
    if len(tokens) < min_repeat:
        return text, 0
    counts = Counter(t.group(0) for t in tokens)
    removed = 0
    for word, count in counts.items():
        if count >= min_repeat and len(word) >= 2:
            run = re.search(rf"(?:(?:{re.escape(word)}[\s,，、]*)){{{min_repeat},}}", text)
            if run:
                text = text[: run.start()] + word + text[run.end() :]
                removed += count - 1
    return text, removed


def normalize_segments(raw: list[SpokenSegment]) -> CleanResult:
    blacklist = load_blacklist()
    flags: Counter[str] = Counter()
    warnings: list[str] = []

    prepared: list[dict] = []
    for seg in raw:
        text = clean_text(seg.text)
        if not text:
            flags["empty_dropped"] += 1
            continue
        text, removed = collapse_inline_repetition(text)
        if removed:
            flags["repetition_collapsed"] += removed
        if is_hallucination(text, blacklist):
            flags["hallucination_dropped"] += 1
            continue
        start = max(0.0, float(seg.start))
        end = float(seg.end)
        if end <= start + 0.05:
            end = start + 0.05
            flags["clamp_fixed"] += 1
        prepared.append({"start": start, "end": end, "text": text, "confidence": seg.confidence})

    # 时间戳单调化
    for i in range(1, len(prepared)):
        prev, cur = prepared[i - 1], prepared[i]
        if cur["start"] < prev["end"] - 0.5:
            cur["start"] = prev["end"]
            if cur["end"] <= cur["start"]:
                cur["end"] = cur["start"] + 0.05
            flags["clamp_fixed"] += 1

    # 连续同文段落去重（跨段卡带）
    deduped: list[dict] = []
    for seg in prepared:
        key = comparison_key(seg["text"])
        if deduped and key and key == comparison_key(deduped[-1]["text"]):
            deduped[-1]["end"] = max(deduped[-1]["end"], seg["end"])
            flags["repetition_collapsed"] += 1
            continue
        deduped.append(seg)

    # 碎片合并：太短的段并入前一段
    merged: list[dict] = []
    for seg in deduped:
        too_short = len(seg["text"]) < 4 and (seg["end"] - seg["start"]) < 1.2
        if merged and too_short:
            merged[-1]["text"] += seg["text"]
            merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
            flags["merged"] += 1
            continue
        merged.append(seg)

    # 段尾无标点且相邻段同语言时拼接（减少碎句）
    stitched: list[dict] = []
    for seg in merged:
        if (
            stitched
            and not stitched[-1]["text"].endswith(SENT_END)
            and len(stitched[-1]["text"]) < 60
            and seg["start"] - stitched[-1]["end"] < 1.0
        ):
            stitched[-1]["text"] += seg["text"]
            stitched[-1]["end"] = max(stitched[-1]["end"], seg["end"])
            flags["merged"] += 1
            continue
        stitched.append(seg)

    confidences = [s["confidence"] for s in stitched if s["confidence"] is not None]
    stats = {
        "segment_count": len(stitched),
        "chars": sum(len(s["text"]) for s in stitched),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }
    if stats["segment_count"] == 0:
        warnings.append("清洗后无有效文本，可能是纯音乐/静音/无语音")
    if stats["avg_confidence"] is not None and stats["avg_confidence"] < 0.55:
        warnings.append(f"平均置信度偏低（{stats['avg_confidence']}），报告结论需人工复核")
    if flags["hallucination_dropped"] > 5:
        warnings.append(f"命中幻觉黑名单 {flags['hallucination_dropped']} 段，音频含大量静音或噪声")
    return CleanResult(stitched, dict(flags), warnings, stats)


def assign_idx(segments: list[dict]) -> list[dict]:
    for i, seg in enumerate(segments):
        seg["idx"] = i
    return segments


def split_long_paragraph(text: str, budget: int = 180) -> list[str]:
    """图文源的段落常常一整段几百字，切到句级再装箱，否则分块与要点会糊在一起。"""
    if len(text) <= budget:
        return [text]
    sentences = re.split(r"(?<=[。！？；!?;\n])", text)
    out: list[str] = []
    buf = ""
    for sentence in sentences:
        if not sentence.strip():
            continue
        if buf and len(buf) + len(sentence) > budget:
            out.append(buf.strip())
            buf = sentence
        else:
            buf += sentence
    if buf.strip():
        out.append(buf.strip())
    return out or [text]


def text_to_segments(text: str) -> CleanResult:
    """图文源：按段落/句子切，start/end 用段落下标充当引用锚点。"""
    blocks: list[str] = []
    for paragraph in re.split(r"\n{1,}", text):
        cleaned = clean_text(paragraph)
        if cleaned:
            blocks.extend(split_long_paragraph(cleaned))
    raw = [RawSegment(start=float(i), end=float(i) + 1.0, text=p) for i, p in enumerate(blocks)]
    result = normalize_segments(raw)
    for seg in result.segments:
        seg["flags"] = ["para_index"]
    return result
