"""语言标签归一：平台给 BCP-47，Whisper 只认两位码。"""

from __future__ import annotations

WHISPER_LANGS = set(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw "
    "he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my "
    "ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt "
    "uk ur uz vi yi yo zh yue".split()
)


def normalize_language(hint: str | None) -> str | None:
    """把平台给的语言标签压成 Whisper 认的两位码。

    YouTube 给的是 `en-US` / `zh-Hans-CN` 这种 BCP-47 标签，直接传给
    faster-whisper 会抛 "'en-US' is not a valid language code"，整条任务失败。
    压不成已知代码就返回 None，交给模型自己检测——比猜错更安全。
    """
    if not hint:
        return None
    code = hint.strip().replace("_", "-").split("-")[0].lower()
    return code if code in WHISPER_LANGS else None
