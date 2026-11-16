"""任务层：状态库、产物目录、阶段日志、索引、收尾、对外视图。

刻意拆开，是因为它们的失败模式不同：数据库会锁、磁盘会满、改名会撞名。
混在一个类里时，一次 OSError 会让整条任务看起来像数据库坏了。
"""

from .artifacts import artifacts_present, load_artifacts
from .db import Store
from .finalize import finalize
from .index import refresh_index
from .layout import job_dir, job_dir_name, job_path
from .models import Job
from .stages import STAGES, append_stage, read_stages
from .view import job_view

__all__ = [
    "STAGES",
    "Job",
    "Store",
    "append_stage",
    "artifacts_present",
    "finalize",
    "job_dir",
    "job_dir_name",
    "job_path",
    "job_view",
    "load_artifacts",
    "read_stages",
    "refresh_index",
]
