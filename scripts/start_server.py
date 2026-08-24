from __future__ import annotations

import os
import sys
from pathlib import Path
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("YXRT_HOST", "0.0.0.0"),
        port=int(os.environ.get("YXRT_PORT", "8765")),
        access_log=False,
    )
