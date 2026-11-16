#!/usr/bin/env python3
"""把浏览器里复制的 Cookie 请求头转成 yt-dlp 能读的 Netscape cookies.txt。

用它的理由：读浏览器 cookie 库要先给终端「完全磁盘访问权限」，很多机器上
这一步过不去（TCC 拦着，报 “could not find chrome cookies database”）。
粘贴 cookie 头绕开整个磁盘权限问题。

用法（不传参则读标准输入）：

    pbpaste | ./scripts/cookies.sh

    ./scripts/cookies_from_header.py --value 'VISITOR_INFO1_LIVE=xx; LOGIN_INFO=yy; SID=zz'

怎么拿这个头：Chrome 打开 youtube.com 并登录 → F12 → Network → 随便点一条
youtubei 请求 → Headers → Request Headers → 复制 `cookie:` 后面整串。

注意：这些值等价于你的登录凭证，不要提交到仓库、不要发给别人。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 系统里常常只有 python3、没有 python，且 /usr/bin/python3 没有项目依赖（httpx 等）。
# 所以只要项目 venv 存在就把自己顶进 venv 解释器，用户不用记用哪个 python 跑。
_VENV_PY = ROOT / ".venv" / "bin" / "python"
if _VENV_PY.exists():
    try:
        if Path(sys.executable).resolve() != _VENV_PY.resolve():
            os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
    except OSError:
        pass  # 顶不进去就按当前解释器继续，后面缺依赖会给出可读的提示

sys.path.insert(0, str(ROOT / "src"))

DEFAULT_OUT = ROOT / "data" / "cookies.txt"

# 这些是「登录态」cookie，必须同时写给 .google.com，否则 youtubei 接口认不出来
GOOGLE_AUTH = {
    "SID", "HSID", "SSID", "APISID", "SAPISID", "LSID", "LOGIN_INFO",
    "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS", "__Host-1PLSID", "__Host-3PLSID",
}


def parse_header(raw: str) -> list[tuple[str, str]]:
    text = raw.strip()
    # 允许把整行 "cookie: a=1; b=2" 一起粘进来，也允许带引号
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    text = text.strip().strip("'\"")
    text = text.replace("\n", " ").replace("\r", " ")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append((name, value.strip()))
    return pairs


def to_netscape(pairs: list[tuple[str, str]], kept: list[str] | None = None) -> str:
    expires = int(time.time()) + 180 * 24 * 3600
    lines = [
        "# Netscape HTTP Cookie File",
        "# 由 linkdigest/scripts/cookies_from_header.py 生成，含登录凭证，勿外传",
        "",
    ]
    for row in kept or []:
        lines.append(row)
    for name, value in pairs:
        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}")
        if name in GOOGLE_AUTH:
            lines.append(f".google.com\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Cookie 头 → Netscape cookies.txt")
    parser.add_argument("--value", help="直接给 cookie 头内容；省略则读标准输入")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出路径，默认 {DEFAULT_OUT}")
    parser.add_argument("--replace", action="store_true",
                        help="整份替换；默认只替换 youtube/google 相关行，其他站点的 cookie 保留")
    parser.add_argument("--no-verify", action="store_true", help="不做联网校验")
    args = parser.parse_args()

    raw = args.value if args.value is not None else sys.stdin.read()
    pairs = parse_header(raw)
    if not pairs:
        print("没解析到任何 name=value，检查一下粘贴的内容。", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    kept: list[str] = []
    if out.exists() and not args.replace:
        # 这个文件里通常还有 B站/知乎等别的站点的登录态，不能因为换 YouTube 就一并抹掉
        backup = out.with_name(f"{out.stem}.bak-{int(time.time())}{out.suffix}")
        backup.write_text(out.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        print(f"已备份原文件 → {backup}")
        for line in out.read_text(encoding="utf-8", errors="ignore").splitlines():
            body = line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
            if line.startswith("#") or not body.strip():
                continue
            domain = body.split("\t")[0].lstrip(".")
            if "youtube" in domain or domain.endswith("google.com"):
                continue
            kept.append(body)

    out.write_text(to_netscape(pairs, kept), encoding="utf-8")

    from linkdigest.sources import cookies_summary

    info = cookies_summary(out)
    print(f"写入 {len(pairs)} 个 cookie（保留其他站点 {len(kept)} 条）→ {out}")
    print(f"含登录态：{'是' if info.get('logged_in') else '否'}")
    if info.get("auth_names"):
        print(f"登录相关：{', '.join(info['auth_names'])}")
    if not info.get("logged_in"):
        print("\n⚠️ 没有 SID / LOGIN_INFO 这类登录 cookie。")
        print("   确认浏览器里已登录 YouTube，再从 youtubei 请求里复制 cookie 头。")
        if info.get("hint"):
            print(f"   {info['hint']}")
        return 2

    if args.no_verify:
        print("\n✅ 已写入。cookies 每次采集现读，直接重试链接即可，不用重启服务。")
        return 0

    from linkdigest.sources import login_state, reachability

    print("\n联网校验（拿这份 cookies 打一次 youtube.com 看服务端认不认）…")
    reachability("https://www.youtube.com/", timeout=8.0)
    state = login_state()
    if state.get("error"):
        print(f"校验没做成：{state['error']}")
        print("（不影响写入结果，直接重试链接即可）")
        return 0
    if state.get("logged_in") is True:
        print(f"✅ 服务端认这份登录态（via {state.get('via')}）。直接重试链接即可，不用重启服务。")
        return 0
    print(f"❌ 服务端仍判为未登录（LOGGED_IN={state.get('logged_in')}，"
          f"发送了 {state.get('cookies_sent')} 个 cookie，via {state.get('via')}）。")
    print("   多半是复制的那条请求本身没带认证 cookie：登录后刷新页面，"
          "在 youtube.com 自己的 youtubei 请求里复制，别从别的站点复制。")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
