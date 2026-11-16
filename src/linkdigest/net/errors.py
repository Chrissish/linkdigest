"""把底层异常翻成一句人能动手的中文。

文案全部在 `resources/messages/download_errors.json`，这里只做三件事：
匹配关键词、判断条件（比如 cookies 是否已登录）、按需取值填充占位符。
加一条新报错 = 改 JSON，不用碰代码。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Callable
from urllib.parse import urlsplit

from ..config import settings
from ..conventions.resources import json_resource
from ..conventions.text import first_line


class DownloadFailure(RuntimeError):
    """采集失败，消息面向用户：说清原因和下一步，不用「未找到文件」这种废信息。"""


def explain_error(exc: BaseException | str, url: str) -> str:
    message = str(exc)
    lowered = message.lower()
    context = _Context(url, message)
    catalog = json_resource("messages/download_errors.json")

    for rule in catalog["rules"]:
        if not any(word in lowered for word in rule["match_any"]):
            continue
        condition = rule.get("when")
        if condition and not CONDITIONS[condition]():
            continue
        return _render(rule["text"], context)
    return _render(catalog["fallback"], context)


def _render(template: str, context: Mapping[str, str]) -> str:
    try:
        return template.format_map(context)
    except KeyError:  # 模板里有未提供的占位符：宁可原文返回，也不要在报错路径上再抛异常
        return template


def _cookies() -> dict:
    from . import cookies  # 延迟导入：只在真的需要解释登录问题时才读磁盘

    return cookies.summary()


CONDITIONS: dict[str, Callable[[], bool]] = {
    "cookies_logged_in": lambda: bool(_cookies().get("logged_in")),
    "cookies_guest": lambda: not _cookies().get("logged_in"),
}


class _Context(Mapping[str, str]):
    """占位符按需计算：没装 OCR、没配 cookies 的机器，解释报错时不该去读磁盘。"""

    def __init__(self, url: str, message: str) -> None:
        host = urlsplit(url).hostname or url
        self._base = {
            "url": url,
            "host": host,
            "detail": first_line(message, 200) or "未知原因",
            "detail_short": first_line(message, 90) or "未知原因",
        }
        self._providers: dict[str, Callable[[], str]] = {
            "runtime": lambda: settings.js_runtime or "未检测到",
            "cookies_path": lambda: str(settings.cookies_path),
            "cookies_state": self._cookies_state,
        }
        self._computed: dict[str, str] = {}

    def __getitem__(self, key: str) -> str:
        if key in self._base:
            return self._base[key]
        if key in self._computed:
            return self._computed[key]
        provider = self._providers.get(key)
        if provider is None:
            raise KeyError(key)
        value = provider()
        self._computed[key] = value
        return value

    def __iter__(self) -> Iterator[str]:
        return iter({**self._base, **self._providers})

    def __len__(self) -> int:
        return len(self._base) + len(self._providers)

    @staticmethod
    def _cookies_state() -> str:
        info = _cookies()
        if info.get("present"):
            return f"\n当前 {info.get('path')}：{info.get('hint') or '没有可用的登录态。'}"
        return f"\n当前没有 cookies.txt（期望位置 {settings.cookies_path}）。"
