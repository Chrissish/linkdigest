# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中

- 任务队列持久化：进程重启后能续跑未完成的任务
- 报告正文的分节跳转目录
- 更多正文抽取站点的适配

## [0.1.0] - 2026-11-17

首个可用版本：端到端跑通，产物可核对。

### 新增

- **采集层**：yt-dlp（YouTube / B站 / Vimeo / X / TikTok / 快手 / 微博）、
  RSS 与「单集页面 → 反查原始 RSS」的播客路线、trafilatura 正文抽取、本地文件直入。
- **转写层**：ffmpeg 抽音轨 + faster-whisper / mlx-whisper 转写，确定性规则清洗
  （重复、幻觉、时间戳倒挂）；`ASR_ENGINE=auto` 按环境挑引擎。
- **契约**：`transcript.json` 作为唯一下游输入，含 schema 校验与 `bundle_id` 去重。
- **理解层**：OpenAI 兼容端点的 map-reduce 长文总结；无密钥时导出 `handoff/`
  交接包给任意 agent，回填 `answer.json` 后 `--apply` 渲染。
- **产物**：`report.md`（带 frontmatter）、`report.html`（单文件、样式内联、
  时间码可点回原片）、`report.json`。
- **网页**：FastAPI 单页界面，支持链接与文件上传、阶段进度、历史任务、交接包操作。
- **排错**：出口探测（直连 → 环境变量代理 → macOS 系统代理）与失败归因；
  `--check` 与 `/api/diagnostics` 逐条打印每条路径的结果。
- **登录态**：cookies.txt 合并写入脚本与联网校验（`LOGGED_IN` 真打一次站点），
  明确区分「没登录态」与「IP 被判成机器人」。
- **自检**：`scripts/selftest.py` 18 项断言，不联网、不调模型；`tests/fixtures/`
  存真实产物作为回归基准。

### 已知限制

- 单机单进程，无任务持久化。
- 抖音 / 小红书依赖 cookies，随时可能失效。
- 音乐类内容（DRM 加密）永久不支持。
