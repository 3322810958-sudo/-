from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("YXRT_MODEL_DIR", str(ROOT / "models"))
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(ROOT / "models" / "paddlex"))
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
sys.path.insert(0, str(ROOT))

from app.ocr_engine import _get_ocr


if __name__ == "__main__":
    print("正在准备 PP-OCRv5 中文轻量模型...")
    _get_ocr()
    print("OCR 模型准备完成。断网后仍可识别。")
