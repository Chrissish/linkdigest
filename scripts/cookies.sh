#!/usr/bin/env bash
# 把剪贴板里的 cookie 头写成 data/cookies.txt（合并，不覆盖其他站点）
#
#   pbpaste | ./scripts/cookies.sh
#   ./scripts/cookies.sh --value 'SID=xx; LOGIN_INFO=yy'
#
# 用 venv 的 python，绕开「系统里没有 python / 有 python3 但缺依赖」这类坑。
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "找不到 $PY —— 先跑 ./setup.sh 建虚拟环境。" >&2
  exit 1
fi

exec "$PY" scripts/cookies_from_header.py "$@"
