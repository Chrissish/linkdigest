"""交接包的路径契约。这些名字是外部 agent 的接口，改名就是破坏性变更。"""

from __future__ import annotations

BRIEF = "handoff/BRIEF.md"
ANSWER = "handoff/answer.json"
TEMPLATE = "handoff/answer.template.json"
TRANSCRIPT_MD = "handoff/transcript.md"

# 超过这个字数就切片，BRIEF 里只放目录——单文件太大，读的人会漏。
INLINE_LIMIT = 40000
