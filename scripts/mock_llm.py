"""假模型服务：验证 map/reduce 管路与 JSON 解析，不花钱、不联网。

用法：
    python scripts/mock_llm.py 8899 &
    LLM_BASE_URL=http://127.0.0.1:8899/v1 LLM_API_KEY=mock \
    python -m linkdigest.cli tests/fixtures/samples/demo_zh.aiff --show
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MAP_REPLY = {
    "summary": "本段说明放弃视频抽帧方案的三条理由：成本、收益、工程复杂度。",
    "points": ["视觉推理开销是语音识别的 5-10 倍", "仅存在于画面的信息占比不足 8%", "抽帧引入场景切换与对齐等失败模式"],
    "data": [{"label": "画面独有信息占比", "value": "8%", "at": "00:30"}],
    "quotes": [{"text": "先把语音链路做到极致，再考虑画面", "zh": "", "at": "01:00"}],
    "terms": {"ASR": "自动语音识别"},
}

REDUCE_REPLY = {
    "one_liner": "论证视频内容总结应放弃抽帧，专注语音链路",
    "tldr": ["抽帧的成本是语音的 5-10 倍，收益不足 8%", "语音链路的核心是文本校验而非模型", "结论必须带时间码才可核对"],
    "sections": [
        {"title": "为什么砍掉抽帧", "start": 0, "end": 45, "points": ["成本：一小时的视频抽帧 3600 张", "收益：画面独有信息不足 8%", "复杂度：场景切换与去重"]},
        {"title": "语音链路的真正瓶颈", "start": 45, "end": 90, "points": ["转录错误必须在落盘前用规则压掉", "重复、幻觉、时间戳倒挂占错误八成"]},
    ],
    "key_points": ["视觉推理比 ASR 贵 5-10 倍", "画面独有信息集中在屏幕录制与图表讲解", "确定性规则可解决大部分转写错误", "报告价值在于可核对而非文采"],
    "data_points": [{"label": "画面独有信息占比", "value": "8%", "start": 30}],
    "quotes": [{"text": "每一个结论后面都应该有一个时间码", "start": 89, "note": "这是产品设计的底线"}],
    "controversies": [],
    "open_questions": ["屏幕录制类内容是否值得单开视觉链路"],
    "terms": {"ASR": "自动语音识别", "关键帧": "用于代表一段画面的帧"},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        user = "".join(m.get("content", "") for m in body.get("messages", []) if m.get("role") == "user")
        reply = REDUCE_REPLY if "分段摘要" in user else MAP_REPLY
        payload = {
            "id": "mock",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": len(user) // 2, "completion_tokens": 200},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"mock llm on http://127.0.0.1:{port}/v1")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
