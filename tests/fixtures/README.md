# tests/fixtures

真实跑过的任务产物，转存成基准样本。用途是给解析/渲染/报错文案做回归对照——
不用联网、不用 ASR、不用模型，就能判断某次改动有没有把已有行为改坏。

体积控制：只留产物（manifest / transcript / report / source / usage / stages / handoff），
**不留音视频**（source.*、audio.wav）。重跑用的源文件放在同目录的 `samples/` 里。

| 目录 | 来源 | 钉住的行为 |
| --- | --- | --- |
| `samples/demo_zh.txt` | 自检输入：纯文本 | — |
| `samples/demo_zh.aiff` | 自检输入：96 秒中文音频（4.2MB） | — |
| `local_text_external_agent/` | `samples/demo_zh.txt`，外部 agent 回填 answer.json | 纯文本入口、frontmatter/tags 写入、handoff 交接包结构 |
| `local_audio_external_agent/` | `samples/demo_zh.aiff`，走完 ASR + 外部理解层 | ASR 段落到 report 的时间码链路、`report.html` 内联样式 |
| `local_audio_degraded/` | 同上，但没有模型，走降级 | 降级报告结构、`degraded` 标记、handoff 提示段 |
| `failed_youtube_botcheck/` | YouTube 要求登录校验 | `explain_error` 对 sign-in/bot 的文案分叉 |
| `failed_spotify_drm/` | Spotify 单集，DRM 拦截 | DRM 类错误的文案（注意：播客走 RSS 是能拿到的，这两个结果不矛盾） |

每个目录里的 `ORIGIN.txt` 记了它原来在 `data/jobs/` 下的目录名。
产物内部（如 `manifest.json` 的 `input`）保留当时的原始路径字符串，是快照的一部分，不做回改。

## 怎么用

产物是纯 JSON / Markdown，直接读、或者喂给模型做对照：

```python
import json
from pathlib import Path

fx = Path("tests/fixtures/local_audio_degraded")
bundle = json.loads((fx / "transcript.json").read_text())   # 契约对象，字段见 schemas/
report = json.loads((fx / "report.json").read_text())
stages = [json.loads(l) for l in (fx / "stages.jsonl").read_text().splitlines() if l.strip()]
```

改清洗规则、改报告模板、改报错文案时，先跑 `scripts/selftest.py`，
再拿这里的 `transcript.json` / `report.md` 对照，确认差异是你想要的那一处。

## 怎么补新样本

真实任务跑完、且行为值得钉住时：

```bash
# 只拷产物，不拷音视频
rsync -a --exclude 'source.*' --exclude 'audio.wav' --exclude '.DS_Store' \
  data/jobs/<任务目录>/ tests/fixtures/<语义化名字>/
```

然后在上表补一行，写清它钉住的是什么行为。
