"""外挂交接：没有模型密钥时，把「理解」这一步交出去。

确定性层（下载 / 抽音轨 / ASR / 清洗 / 契约）跑完就没有任何外部依赖了，
剩下的归纳与跨语言理解可以交给任意一个会读文件的 agent。

这里只做两件事：
  export_handoff()  写一份自包含的作业单，agent 只读它就能干活；
  apply_answer()    收它的 JSON，校验、渲染、把任务标成完成。

版本版式全部在 resources/templates/brief.md，改口径不改代码。
"""

from .answer import apply_answer, read_answer
from .export import export_handoff
from .paths import ANSWER, BRIEF, INLINE_LIMIT, TEMPLATE, TRANSCRIPT_MD

__all__ = [
    "ANSWER",
    "BRIEF",
    "INLINE_LIMIT",
    "TEMPLATE",
    "TRANSCRIPT_MD",
    "apply_answer",
    "export_handoff",
    "read_answer",
]
