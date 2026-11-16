#!/usr/bin/env bash
# 一次装好环境。可重复执行。
#
#   ./setup.sh                                                       # 官方 PyPI
#   PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./setup.sh     # 国内镜像
#   PYTHON=python3.11 ./setup.sh                                      # 指定解释器
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
INDEX="${PIP_INDEX:-}"
PIP_ARGS=()
if [ -n "$INDEX" ]; then
  PIP_ARGS=(-i "$INDEX")
fi

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi

.venv/bin/pip install ${PIP_ARGS[@]+"${PIP_ARGS[@]}"} -q --upgrade pip setuptools wheel

# 依赖只写在 pyproject.toml 一处，这里不另抄一份清单，避免两处漂移
if [ -n "$INDEX" ]; then
  echo "安装依赖，镜像 ${INDEX}"
else
  echo "安装依赖"
fi
.venv/bin/pip install ${PIP_ARGS[@]+"${PIP_ARGS[@]}"} -q -e .

[ -f .env ] || cp .env.example .env

.venv/bin/python -c "import linkdigest; print('linkdigest', linkdigest.__version__, 'ok')"

echo
echo "下一步："
echo "  1. 编辑 .env 填 LLM_API_KEY（不填也能跑，走降级模式）"
echo "  2. ./run.sh  启动网页  http://127.0.0.1:8787"
