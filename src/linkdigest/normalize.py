"""文本稿校验与归一。

ASR 的错不会自己消失，必须在落盘前用确定性规则压掉。这里只做规则能保证的事，
判断类问题留给下层模型（并在报告里标注）。所有清洗动作都计数进 quality.flags。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .asr import ASRSegment
from .config import settings

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
CJK = r"\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"
CJK_SPACE_RE = re.compile(rf"(?<=[{CJK}])\s+(?=[{CJK}])")
MULTI_SPACE_RE = re.compile(r"[ \t\u00a0]{2,}")
SENT_END = tuple("。！？.!?…；;")

# NFKC 会把中文全角标点压成半角（，→,），中文排版不能接受。
# 先换成私用区占位符，归一后再换回来。
PUNCT_GUARD = {
    "，": "\ue000", "。": "\ue001", "、": "\ue002", "；": "\ue003", "：": "\ue004",
    "？": "\ue005", "！": "\ue006", "（": "\ue007", "）": "\ue008",
    "“": "\ue009", "”": "\ue00a", "‘": "\ue00b", "’": "\ue00c",
    "《": "\ue00d", "》": "\ue00e", "—": "\ue00f", "…": "\ue010",
}
PUNCT_RESTORE = {v: k for k, v in PUNCT_GUARD.items()}
# Whisper 输出的中文常混半角标点（"第一,成本。"），排版上很刺眼。
HALF_IN_CJK_RE = re.compile(rf"(?<=[{CJK}])\s*([,:;!?])\s*(?=[{CJK}])")
HALF_TO_FULL = {",": "，", ":": "：", ";": "；", "!": "！", "?": "？"}

BUILTIN_HALLUCINATIONS = (
    "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "字幕由amaraorg社区提供",
    "字幕志愿者", "amaraorg", "subtitle", "subtitlesby",
    "thankyouforwatching", "thanksforwatch", "pleaseSubscribe".lower(),
    "明镜与点点", "小玲和Peter", "本字幕由",
)


def _key(text: str) -> str:
    """用于重复检测的比较键：去标点、去空白、小写。"""
    return re.sub(rf"[^\w{CJK}]+", "", text).lower()


def clean_text(text: str) -> str:
    for src, guard in PUNCT_GUARD.items():
        text = text.replace(src, guard)
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)
    for guard, src in PUNCT_RESTORE.items():
        text = text.replace(guard, src)
    text = text.replace("\u3000", " ")
    text = MULTI_SPACE_RE.sub(" ", text)
    text = CJK_SPACE_RE.sub("", text)
    text = HALF_IN_CJK_RE.sub(lambda m: HALF_TO_FULL[m.group(1)], text)
    return text.strip()


def load_hallucinations() -> tuple[str, ...]:
    extra: list[str] = []
    path = settings.data_dir / "hallucinations.txt"
    if path.exists():
        extra = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return tuple(BUILTIN_HALLUCINATIONS) + tuple(extra)


def is_hallucination(text: str, blacklist: tuple[str, ...]) -> bool:
    key = _key(text)
    if not key:
        return True
    if len(key) <= 2:
        return True
    # 同一字符反复（"啊啊啊" / "哈哈哈哈"）：Whisper 在噪声段的典型产物，无信息量
    if len(set(key)) == 1 and len(key) <= 6:
        return True
    return any(key == _key(item) or (len(_key(item)) > 8 and _key(item) in key) for item in blacklist)


def collapse_inline_repetition(text: str, min_repeat: int = 4) -> tuple[str, int]:
    """同一短语在单段内连续重复（Whisper 卡带），压成一次。"""
    tokens = list(re.finditer(rf"[{CJK}]{{2,}}|[A-Za-z']+", text))
    if len(tokens) < min_repeat:
        return text, 0
    words = [t.group(0) for t in tokens]
    counts = Counter(words)
    removed = 0
    for word, count in counts.items():
        if count >= min_repeat and len(word) >= 2:
            run = re.search(rf"(?:(?:{re.escape(word)}[\s,，、]*)){{{min_repeat},}}", text)
            if run:
                text = text[: run.start()] + word + text[run.end():]
                removed += count - 1
    return text, removed


def normalize_segments(raw: list[ASRSegment]) -> tuple[list[dict], dict[str, int], list[str], dict]:
    blacklist = load_hallucinations()
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
        if deduped and _key(seg["text"]) and _key(seg["text"]) == _key(deduped[-1]["text"]):
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
    return stitched, dict(flags), warnings, stats


def assign_idx(segments: list[dict]) -> list[dict]:
    for i, seg in enumerate(segments):
        seg["idx"] = i
    return segments


def _split_long_paragraph(text: str, budget: int = 180) -> list[str]:
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


def text_to_segments(text: str) -> tuple[list[ASRSegment], dict[str, int], list[str], dict]:
    """图文源：按段落/句子切，start/end 用段落下标充当引用锚点。"""
    blocks: list[str] = []
    for paragraph in re.split(r"\n{1,}", text):
        cleaned = clean_text(paragraph)
        if cleaned:
            blocks.extend(_split_long_paragraph(cleaned))
    raw = [ASRSegment(start=float(i), end=float(i) + 1.0, text=p) for i, p in enumerate(blocks)]
    segments, flags, warnings, stats = normalize_segments(raw)
    for seg in segments:
        seg["flags"] = ["para_index"]
    return segments, flags, warnings, stats


def script_of(text: str) -> str:
    sample = text[:4000]
    if not sample:
        return "unknown"
    cjk = len(re.findall(rf"[{CJK}]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk and latin and min(cjk, latin) / max(cjk, latin) > 0.2:
        return "mixed"
    return "cjk" if cjk > latin else "latin"
