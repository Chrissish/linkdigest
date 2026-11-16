"""统一的 httpx 客户端。

三件事固定下来，避免各调用点各写一遍：UA、超时、重定向跟随。
`direct=True` 会忽略环境变量里的代理——探活时「直连」必须是真直连。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import httpx

from ..config import settings

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


@contextmanager
def client(
    *,
    timeout: float | None = None,
    proxy: str | None = None,
    direct: bool = False,
    headers: dict[str, str] | None = None,
) -> Iterator[httpx.Client]:
    options: dict = {
        "timeout": settings.fetch_timeout if timeout is None else timeout,
        "follow_redirects": True,
        "headers": {"User-Agent": UA, **(headers or {})},
    }
    if proxy:
        options["proxy"] = proxy
    elif direct:
        options["trust_env"] = False
    with httpx.Client(**options) as session:
        yield session


def get_text(url: str, *, timeout: float | None = None, headers: dict[str, str] | None = None) -> str:
    with client(timeout=timeout, headers=headers) as session:
        response = session.get(url)
        response.raise_for_status()
        return response.text


def get_json(url: str, *, timeout: float | None = None) -> dict:
    with client(timeout=timeout, headers={"Accept": "application/json"}) as session:
        response = session.get(url)
        response.raise_for_status()
        return response.json()
