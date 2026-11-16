"""启动期的显式副作用：一处写清楚，不藏在 import 里。

原来 `config.py` 一被 import 就把代理塞进 os.environ。导入即改环境会带来两个问题：
测试无法在干净环境下跑，且「谁改的」永远查不到。现在改成显式调用：
进程真正开始干活前调一次 `bootstrap(cfg)`，配置从外面传进来，这里只动手不动配置。
"""

from __future__ import annotations

from .config import Settings
from .net import proxy


def install_proxy(cfg: Settings) -> str | None:
    """把生效出口写进 HTTP(S)_PROXY。

    动机：服务常被别的进程（IDE、沙箱、脚本）带着自己的 HTTP_PROXY 启动，
    用户明明配了代理，实际出口却是另一个，排查时极易误判成「网络不稳」。
    PROXY_URL 一旦设置就强制覆盖，localhost 永远直连。
    """
    chosen = cfg.proxy
    if not chosen:
        return None
    proxy.apply(chosen)
    return chosen


def bootstrap(cfg: Settings) -> Settings:
    """建目录、装代理。返回传进来的 cfg，方便入口写成一行。"""
    cfg.ensure_dirs()
    install_proxy(cfg)
    return cfg
