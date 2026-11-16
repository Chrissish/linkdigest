#!/usr/bin/env bash
# 启动网页服务（默认自动打开浏览器；NO_OPEN=1 可关闭）
#
#   ./scripts/run.sh                 # 起服务并打开 http://127.0.0.1:8787/
#   NO_OPEN=1 ./scripts/run.sh       # 只起服务
#   PORT=9000 ./scripts/run.sh       # 换端口
set -euo pipefail
# 脚本在 scripts/ 下，工作目录要回到仓库根（.venv 与包都在根上）
cd "$(dirname "$0")/.."
PORT="${PORT:-8787}"
URL="http://127.0.0.1:${PORT}/"

if [[ "${NO_OPEN:-0}" != "1" ]] && command -v open >/dev/null 2>&1; then
  # 等服务真正开始监听后再开浏览器，避免打开一个连不上的页面
  (
    for _ in $(seq 1 80); do
      if curl -sf -o /dev/null "${URL}api/health"; then open "$URL"; exit 0; fi
      sleep 0.25
    done
  ) &
fi

echo "[linkdigest] 界面 ${URL}  —— 别直接双击 src/linkdigest/resources/web/index.html 打开"
exec .venv/bin/python -m uvicorn linkdigest.api:app --host 127.0.0.1 --port "$PORT" "$@"
