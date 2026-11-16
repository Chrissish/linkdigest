"""外部依赖能力层：一个能力一个包，端口与实现分文件。

约定（新增能力照这个来）：

    capabilities/<能力>/
        base.py        端口：数据结构 + Protocol，上层只认这个
        <实现>.py      适配器：只做「把某个第三方库/二进制接到端口上」
        __init__.py    发现、选择、暴露门面

上层（ingest / clean / understand / app）只 import 端口和门面，不 import 实现，
也不 import 第三方库。这样换引擎、换模型、换 OCR 都只是加一个文件 + 改选择逻辑，
不需要动业务代码，测试也可以塞一个假实现进来。

当前能力：
    ffmpeg    抽音轨 / 时长（二进制）
    speech    ASR：faster-whisper ｜ mlx-whisper
    ocr       图文源内嵌图片（可选，rapidocr）
    llm       理解层：OpenAI 兼容端点
"""
