from __future__ import annotations

import os
import sys


def hide_packaged_console() -> None:
    """Use the reliable console bootloader without leaving a console visible."""
    if not getattr(sys, "frozen", False) or os.environ.get("YXRT_KEEP_CONSOLE") == "1":
        return
    try:
        import ctypes

        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 0)
    except Exception:
        pass


hide_packaged_console()

from app.desktop import main


if __name__ == "__main__":
    main()
