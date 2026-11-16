"""理解层：输入是契约，输出是 Report。执行者可以是模型，也可以是外部 agent。"""

from .degraded import degraded_report
from .mapreduce import build_report
from .payload import report_from_payload

__all__ = ["build_report", "degraded_report", "report_from_payload"]
