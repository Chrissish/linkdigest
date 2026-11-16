#!/usr/bin/env bash
# 导出浏览器登录态为 data/cookies.txt
#
# 用法：
#   ./scripts/export_cookies.sh                 # 自动挑一个能读的浏览器
#   ./scripts/export_cookies.sh chrome          # 指定浏览器
#   ./scripts/export_cookies.sh "chrome:Default"          # 指定 Chrome profile
#   ./scripts/export_cookies.sh "chrome:Profile 1"        # profile 名带空格要加引号
#   ./scripts/export_cookies.sh --doctor        # 只诊断，不导出
#
# 读浏览器 cookies 需要终端 App 有「完全磁盘访问权限」：
#   系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 加上你用的终端
#   然后 Cmd+Q 完全退出终端再打开（不是关窗口）。
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${URL:-https://www.youtube.com/watch?v=a1K2V8iDYQA}"
OUT="data/cookies.txt"
PY=".venv/bin/python"

detect_terminal() {
  if [ -n "${TERM_PROGRAM:-}" ]; then
    echo "${TERM_PROGRAM}"
  else
    ps -o comm= -p "${PPID:-$$}" 2>/dev/null | sed 's|.*/||' || echo "当前终端"
  fi
}

# 打印某浏览器的 cookie 库路径与可读性，把「没装 / 没登录过 / 权限被拦」分开
check_browser() {
  local name="${1}" base="${2}" db
  if [ ! -d "${base}" ]; then
    echo "  ${name}: 未安装或从未启动过"
    return 1
  fi
  if ! ls "${base}" >/dev/null 2>&1; then
    echo "  ${name}: 目录存在但读不了 → 权限被 macOS 拦（需要完全磁盘访问权限）"
    return 1
  fi
  for db in "${base}/Default/Network/Cookies" "${base}/Default/Cookies" "${base}/Profile 1/Network/Cookies"; do
    if [ -f "${db}" ]; then
      echo "  ${name}: 找到 ${db}"
      return 0
    fi
  done
  echo "  ${name}: 目录可读，但没找到 Cookies 库（可能只用过访客模式）"
  return 1
}

doctor() {
  echo "终端 App: $(detect_terminal)"
  echo "（若下面写「权限被 macOS 拦」，去给它开完全磁盘访问权限，然后完全退出终端重开）"
  echo
  check_browser "Chrome" "${HOME}/Library/Application Support/Google/Chrome" || true
  check_browser "Edge" "${HOME}/Library/Application Support/Microsoft/Edge" || true
  check_browser "Brave" "${HOME}/Library/Application Support/BraveSoftware/Brave-Browser" || true
  check_browser "Safari" "${HOME}/Library/Containers/com.apple.Safari/Data/Library/Cookies" || true
  if [ -d "${HOME}/Library/Application Support/Google/Chrome" ] \
     && ls "${HOME}/Library/Application Support/Google/Chrome" >/dev/null 2>&1; then
    echo
    echo "Chrome 的 profile 目录："
    ls "${HOME}/Library/Application Support/Google/Chrome" | grep -E "^(Default|Profile )" | sed 's/^/  · /' || true
  fi
}

if [ "${1:-}" = "--doctor" ]; then
  doctor
  exit 0
fi

BROWSER="${1:-}"
if [ -z "${BROWSER}" ]; then
  if ls "${HOME}/Library/Application Support/Google/Chrome/Default" >/dev/null 2>&1; then
    BROWSER="chrome"
  elif ls "${HOME}/Library/Containers/com.apple.Safari/Data" >/dev/null 2>&1; then
    BROWSER="safari"
  elif ls "${HOME}/Library/Application Support/Microsoft/Edge/Default" >/dev/null 2>&1; then
    BROWSER="edge"
  else
    echo "没找到能读的浏览器库，先跑诊断："
    echo "  ./scripts/export_cookies.sh --doctor"
    exit 1
  fi
  echo "自动选择浏览器：${BROWSER}（也可显式指定 chrome / safari / edge）"
fi

mkdir -p data
rm -f "${OUT}"

echo "[1/2] 从 ${BROWSER} 导出 cookies → ${OUT}"
echo "      首次会弹钥匙串授权，输入登录密码即可。"

set +e
"${PY}" -m yt_dlp \
  --cookies "${OUT}" \
  --cookies-from-browser "${BROWSER}" \
  --skip-download --no-warnings --socket-timeout 20 \
  "${URL}"
STATUS=${?}
set -e

if [ ! -s "${OUT}" ]; then
  echo
  echo "导出失败（yt-dlp 退出码 ${STATUS}）。先跑诊断看卡在哪一层："
  echo "  ./scripts/export_cookies.sh --doctor"
  echo
  echo "若诊断显示「权限被 macOS 拦」，两条路："
  echo "  A. 给终端开完全磁盘访问权限，完全退出终端重开，再跑本脚本"
  echo "  B. 绕过磁盘权限：用浏览器扩展「Get cookies.txt LOCALLY」导出，"
  echo "     存成 $(pwd)/${OUT}；更省事的办法是粘 Cookie 头："
  echo "         pbpaste | ./scripts/cookies.sh"
  exit 1
fi

echo
echo "[2/3] 校验 ${OUT} 是否含登录态"
"${PY}" - <<'PY'
import json
from linkdigest.net.cookies import summary as cookies_summary

info = cookies_summary()
print(json.dumps(info, ensure_ascii=False, indent=2))
if info.get("logged_in"):
    print("\n✅ 含登录 Cookie。")
else:
    print("\n⚠️ 只有访客 Cookie（未登录）。先在浏览器里登录 YouTube，再导出一次。")
    raise SystemExit(3)
PY

echo
echo "[3/3] 用这份 cookies 实际解析一次（验证平台是否放行）"
set +e
"${PY}" -m yt_dlp \
  --cookies "${OUT}" \
  --skip-download --no-warnings --socket-timeout 20 \
  --print "解析成功：%(title)s | %(duration)s 秒" \
  "${URL}"
VERIFY=${?}
set -e
if [ "${VERIFY}" -ne 0 ]; then
  echo "解析仍失败（退出码 ${VERIFY}）。cookies 已写入，但平台没放行——"
  echo "通常是这份 cookies 没包含有效的登录态，或账号本身触发了风控。"
  exit 1
fi
echo
echo "搞定。服务不用重启，下次提交链接就会用上这份 cookies。"
