"""可选 OCR：只服务图文源里的内嵌图片（截图型文字）。

视频永不抽帧，所以这里不参与视频链路。未安装引擎时返回空并在 warnings 记账。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_engine = None


def available() -> bool:
    return (
        importlib.util.find_spec("rapidocr_onnxruntime") is not None
        or importlib.util.find_spec("rapidocr") is not None
    )


def _load():
    global _engine
    if _engine is not None:
        return _engine
    if importlib.util.find_spec("rapidocr"):
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    elif importlib.util.find_spec("rapidocr_onnxruntime"):
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    else:
        raise RuntimeError("未安装 OCR 引擎（pip install rapidocr onnxruntime）")
    return _engine


def ocr_image(path: Path) -> str:
    engine = _load()
    result = engine(str(path))
    lines = getattr(result, "txts", None)
    if lines is None and isinstance(result, (list, tuple)) and result:
        first = result[0]
        lines = [item[1] for item in first] if isinstance(first, list) else []
    return "\n".join(str(line) for line in (lines or [])).strip()


def ocr_images(paths: list[Path]) -> tuple[str, list[str]]:
    if not paths:
        return "", []
    if not available():
        return "", ["未安装 OCR 引擎，图文源的内嵌图片未识别"]
    chunks: list[str] = []
    warnings: list[str] = []
    for path in paths:
        try:
            text = ocr_image(path)
            if text:
                chunks.append(text)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"OCR 失败 {path.name}: {exc}")
    return "\n".join(chunks), warnings
