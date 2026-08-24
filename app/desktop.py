from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from . import __version__


def port_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def app_ready(host: str, port: int) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/health", timeout=0.8) as response:
            return response.status == 200 and f'"version":"{__version__}"'.encode() in response.read()
    except (OSError, URLError):
        return False


def available_port(host: str, preferred: int) -> int:
    if not port_ready(host, preferred) or app_ready(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def main() -> None:
    multiprocessing.freeze_support()
    default_home = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    runtime_home = Path(os.environ.get("YXRT_HOME", default_home)).resolve()
    runtime_home.mkdir(parents=True, exist_ok=True)
    # Keep model paths relative on Windows. Paddle's native runtime can fail
    # when an absolute model path contains Chinese characters.
    os.chdir(runtime_home)
    os.environ.setdefault("YXRT_MODE", "desktop")
    os.environ.setdefault("YXRT_HOST", "127.0.0.1")
    os.environ.setdefault("YXRT_PORT", "8765")
    host = os.environ["YXRT_HOST"]
    port = available_port(host, int(os.environ["YXRT_PORT"]))
    os.environ["YXRT_PORT"] = str(port)
    url = f"http://{host}:{port}"

    server: uvicorn.Server | None = None
    if not app_ready(host, port):
        config = uvicorn.Config("app.main:app", host=host, port=port, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="yxrt-web", daemon=True)
        thread.start()
        for _ in range(600):
            if app_ready(host, port):
                break
            if not thread.is_alive():
                raise RuntimeError("本地服务启动失败")
            time.sleep(0.1)
        if not app_ready(host, port):
            raise RuntimeError("本地服务启动超时，请检查安全软件后重试")

    try:
        import webview
        webview_storage = runtime_home / "data" / "webview"
        webview_storage.mkdir(parents=True, exist_ok=True)
        window = webview.create_window(
            f"燕翔车队经费管理系统 V{__version__}",
            url,
            width=1460,
            height=920,
            min_size=(1024, 700),
            background_color="#050a11",
            confirm_close=True,
        )
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(webview_storage),
            debug=False,
        )
    except Exception:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        if server is not None:
            server.should_exit = True


if __name__ == "__main__":
    main()
