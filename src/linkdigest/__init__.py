"""LinkDigest：媒体链接 → 结构化内容总结报告。

分层（自下而上，依赖只允许向下）：

    conventions   纯工具：时间码、命名、文本度量、JSON 落盘、资源读取
    config        配置快照（只读环境，无副作用）
    net           出口与传输：代理、HTTP、探活、cookies、失败归因
    capabilities  外部依赖：ffmpeg / ASR / OCR / LLM（端口 + 适配器）
    contracts     契约：pydantic 模型、构造、校验
    clean         确定性清洗：标点、时间戳、幻觉、重复
    ingest        采集：平台判定、正文、RSS、播客、yt-dlp
    understand    理解：分块、提示词、map-reduce、降级
    render        渲染：Markdown / HTML
    handoff       外部交接：作业单、答案应用
    jobs          任务：状态库、产物目录、索引、收尾
    app           编排与用例
    api / cli     交付形态（只做装配与格式化）
"""

__version__ = "0.3.0"
