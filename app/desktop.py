from __future__ import annotations

import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from . import __version__
from .config import RUNTIME_HOME


STARTUP_HTML = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><style>
html,body{height:100%;margin:0;background:#07111b;color:#eaf7ff;font-family:'Microsoft YaHei UI',sans-serif}
body{display:grid;place-items:center}.box{width:min(520px,76vw)}small{color:#27d3ff;letter-spacing:.18em}
h1{font-size:30px;margin:12px 0}p{color:#8fa8b9}.track{height:5px;background:#122b3b;overflow:hidden;margin-top:28px}
.track:after{content:'';display:block;width:35%;height:100%;background:#27d3ff;animation:run 1.1s ease-in-out infinite alternate}
@keyframes run{to{transform:translateX(190%)}}</style><body><div class='box'><small>YANXIANG RACING</small>
<h1>经费管理系统正在启动</h1><p>正在加载本地数据，OCR 将在需要时启动。</p><div class='track'></div></div></body></html>"""


def open_edge_or_default(url: str) -> None:
    if os.name == "nt":
        roots = [os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")]
        candidates = [Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for root in roots if root]
        edge = next((item for item in candidates if item.is_file()), None)
        if edge:
            subprocess.Popen([str(edge), url], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return
        except OSError:
            pass
    webbrowser.open(url)


class DesktopApi:
    def __init__(self) -> None:
        self.window = None

    def choose_backup_path(self, suggested_name: str) -> str:
        if self.window is None:
            return ""
        import webview
        chosen = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=Path(str(suggested_name or "燕翔车队经费完整备份.zip")).name,
            file_types=("ZIP 压缩包 (*.zip)",),
        )
        if not chosen:
            return ""
        if isinstance(chosen, (tuple, list)):
            chosen = chosen[0]
        return str(chosen)


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


def desktop_server_config(host: str, port: int) -> uvicorn.Config:
    """Build a GUI-safe server config without terminal-dependent formatters."""
    return uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,
    )


def main() -> None:
    multiprocessing.freeze_support()
    runtime_home = RUNTIME_HOME
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
    service_ready = app_ready(host, port)
    if not service_ready:
        config = desktop_server_config(host, port)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="yxrt-web", daemon=True)
        thread.start()

    try:
        import webview
        webview_storage = runtime_home / "data" / "webview"
        webview_storage.mkdir(parents=True, exist_ok=True)
        desktop_api = DesktopApi()
        window = webview.create_window(
            f"燕翔车队经费管理系统 V{__version__}",
            url if service_ready else None,
            html=None if service_ready else STARTUP_HTML,
            width=1460,
            height=920,
            min_size=(1024, 700),
            background_color="#050a11",
            confirm_close=True,
            js_api=desktop_api,
        )
        desktop_api.window = window

        def finish_startup() -> None:
            if service_ready:
                return
            for _ in range(600):
                if app_ready(host, port):
                    window.load_url(url)
                    return
                if server is not None and server.should_exit:
                    return
                time.sleep(0.1)
            window.load_html(STARTUP_HTML.replace("正在加载本地数据，OCR 将在需要时启动。", "启动超时，请检查安全软件后重试。"))

        webview.start(
            finish_startup,
            gui="edgechromium",
            private_mode=False,
            storage_path=str(webview_storage),
            debug=False,
        )
    except Exception:
        open_edge_or_default(url)
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
