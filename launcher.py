from __future__ import annotations

import os
import sys


def ensure_runtime_streams() -> None:
    """Provide safe streams for windowed/frozen Python processes.

    PyInstaller's windowed bootloader intentionally sets stdout and stderr to
    None. Some libraries inspect ``isatty()`` during startup even when their
    output is disabled, so keep writable null streams available for the full
    process lifetime.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", buffering=1)


def hide_packaged_console() -> None:
    """Hide a console when a console-enabled compatibility build is used."""
    if not getattr(sys, "frozen", False) or os.environ.get("YXRT_KEEP_CONSOLE") == "1":
        return
    try:
        import ctypes

        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 0)
    except Exception:
        pass


ensure_runtime_streams()
hide_packaged_console()

from app.desktop import main


if __name__ == "__main__":
    main()
