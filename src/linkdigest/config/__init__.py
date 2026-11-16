"""配置层：把环境变量变成值，不做探测、不改进程环境。

对外只有三样东西：`Settings`（不可变配置模型）、`load()`（进程入口调一次）、
`ConfigError`（坏配置的统一异常）。运行期要换参数走 `config.override`，拿新对象。
"""

from .settings import ConfigError, Settings, load

__all__ = ["ConfigError", "Settings", "load"]
