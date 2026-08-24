from __future__ import annotations

import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tmp" / "test-runtime"
if RUNTIME.exists():
    shutil.rmtree(RUNTIME)
RUNTIME.mkdir(parents=True)
os.environ["YXRT_HOME"] = str(RUNTIME)
os.environ["YXRT_DATA_DIR"] = str(RUNTIME / "data")
os.environ["YXRT_UPLOAD_DIR"] = str(RUNTIME / "uploads")
os.environ["YXRT_MODEL_DIR"] = str(RUNTIME / "models")
os.environ["YXRT_TMP_DIR"] = str(RUNTIME / "tmp")
os.environ["YXRT_MODE"] = "test"
