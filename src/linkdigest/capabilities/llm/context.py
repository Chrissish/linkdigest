"""上下文预算：模型窗口推断 + 输入 token 估算。

两个数都不要求手填：

- 窗口大小按模型名从内置家族表推断。官方 API 普遍不暴露窗口（/models 只给
  id），名称本身也不编码，所以只能表 + 覆盖；未知名回落保守默认——宁可错走
  map-reduce，也不能把超限请求打出去吃 400。
- token 估算按内容语种构成加权（CJK ≈ 0.6 token/字，拉丁 ≈ 0.29 token/字，
  对齐 DeepSeek 分词器的公开口径）。不同家 BPE 的方差有限，乘安全系数兜住。
"""

from __future__ import annotations

import math
import re

# 按子串匹配（小写），先命中先得；给的都是保守值——高估窗口会把请求打进 400
_CONTEXT_BY_FAMILY: tuple[tuple[str, int], ...] = (
    ("deepseek", 65536),
    ("gpt-4o", 128000),
    ("gpt-4.1", 128000),  # 官方标 1M，按保守值走
    ("gpt-4-turbo", 128000),
    ("gpt-3.5", 16385),
    ("o1", 200000),
    ("o3", 200000),
    ("o4", 200000),
    ("claude", 200000),
    ("gemini", 1000000),
    ("glm", 128000),
    ("llama", 128000),
    ("qwen", 32768),
    ("moonshot", 32768),
    ("kimi", 32768),
    ("mistral", 32768),
)

# 未知家族：32K 保守值。走 map-reduce 的代价比 context overflow 小得多。
DEFAULT_CONTEXT_TOKENS = 32768

# 单次直读的输入估算要过的两道预留与质量护栏
DIRECT_OUTPUT_RESERVE = 4500  # 直读一次要写完整报告（正文+可选层）
DIRECT_PROMPT_RESERVE = 2048  # spec + 元信息 + 章节锚点
DIRECT_QUALITY_CAP = 0.65  # 塞得下 ≠ 该塞满：超长输入注意力稀释，留给 map-reduce

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def infer_context_tokens(model: str) -> int:
    name = (model or "").lower()
    for family, tokens in _CONTEXT_BY_FAMILY:
        if family in name:
            return tokens
    return DEFAULT_CONTEXT_TOKENS


def estimate_tokens(text: str, chars_per_token: float | None = None) -> int:
    """按语种构成估算 token 数。chars_per_token 是手填逃生口（字/token）。"""
    if not text:
        return 0
    if chars_per_token:
        return math.ceil(len(text) / chars_per_token)
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return math.ceil(cjk * 0.6 + other / 3.5)


def direct_input_budget(context_tokens: int) -> int:
    """单次直读允许的最大输入估算值（tokens）。

    窗口先扣掉输出预留与提示词开销，再乘质量护栏：
    塞得下只是及格线，注意力稀释的超长内容交给 map-reduce 更稳。
    """
    usable = context_tokens - DIRECT_OUTPUT_RESERVE - DIRECT_PROMPT_RESERVE
    return int(usable * DIRECT_QUALITY_CAP)
