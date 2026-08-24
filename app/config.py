from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    APP_DIR = BUNDLE_DIR / "app"
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = APP_DIR.parent
RUNTIME_HOME = Path(os.environ.get("YXRT_HOME", PROJECT_DIR)).resolve()
DATA_DIR = Path(os.environ.get("YXRT_DATA_DIR", RUNTIME_HOME / "data")).resolve()
UPLOAD_DIR = Path(os.environ.get("YXRT_UPLOAD_DIR", RUNTIME_HOME / "uploads")).resolve()
MODEL_DIR = Path(os.environ.get("YXRT_MODEL_DIR", RUNTIME_HOME / "models")).resolve()
TMP_DIR = Path(os.environ.get("YXRT_TMP_DIR", RUNTIME_HOME / "tmp")).resolve()
DB_PATH = Path(os.environ.get("YXRT_DB_PATH", DATA_DIR / "yanxiang_expense.db")).resolve()

HOST = os.environ.get("YXRT_HOST", "127.0.0.1")
PORT = int(os.environ.get("YXRT_PORT", "8765"))
APP_MODE = os.environ.get("YXRT_MODE", "desktop").strip().lower()
COOKIE_SECURE = os.environ.get("YXRT_COOKIE_SECURE", "0") == "1"
SESSION_HOURS = int(os.environ.get("YXRT_SESSION_HOURS", "12"))
SYNC_INTERVAL_SECONDS = max(15, int(os.environ.get("YXRT_SYNC_INTERVAL", "60")))
CPU_COUNT = max(1, os.cpu_count() or 4)
DEFAULT_OCR_WORKERS = 1 if CPU_COUNT <= 4 else (2 if CPU_COUNT <= 12 else 3)
CONFIGURED_OCR_WORKERS = int(os.environ.get("YXRT_OCR_WORKERS", "0"))
OCR_WORKERS = DEFAULT_OCR_WORKERS if CONFIGURED_OCR_WORKERS <= 0 else max(1, min(4, CONFIGURED_OCR_WORKERS))
DEFAULT_OCR_CPU_THREADS = max(2, CPU_COUNT // OCR_WORKERS)
CONFIGURED_OCR_CPU_THREADS = int(os.environ.get("YXRT_OCR_CPU_THREADS", "0"))
OCR_CPU_THREADS = max(2, min(10, DEFAULT_OCR_CPU_THREADS if CONFIGURED_OCR_CPU_THREADS <= 0 else CONFIGURED_OCR_CPU_THREADS))
OCR_DETECTION_MAX_SIDE = max(960, min(4000, int(os.environ.get("YXRT_OCR_MAX_SIDE", "2200"))))
DEVICE_LABEL = os.environ.get("YXRT_DEVICE_LABEL", "Windows 本地端" if APP_MODE == "desktop" else "云端服务器")

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".ofd", ".txt"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_DOCUMENT_EXTENSIONS
APPEARANCE_IMAGE_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | {".gif"}
APPEARANCE_VIDEO_EXTENSIONS = {".mp4", ".webm", ".m4v"}
APPEARANCE_MEDIA_EXTENSIONS = APPEARANCE_IMAGE_EXTENSIONS | APPEARANCE_VIDEO_EXTENSIONS

for directory in (DATA_DIR, UPLOAD_DIR, MODEL_DIR, TMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)
