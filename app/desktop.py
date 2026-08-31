from __future__ import annotations

import multiprocessing
import html
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
import psutil

from . import __version__
from .config import RUNTIME_HOME


STARTUP_TEMPLATE = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><style>
html,body{height:100%;margin:0;background:#07111b;color:#eaf7ff;font-family:'Microsoft YaHei UI',sans-serif}
body{display:grid;place-items:center}.box{width:min(520px,76vw)}small{color:#27d3ff;letter-spacing:.18em}
h1{font-size:30px;margin:12px 0}p{color:#8fa8b9;line-height:1.7}.detail{font-size:13px;word-break:break-all}
.track{height:5px;background:#122b3b;overflow:hidden;margin-top:28px}
.track:after{content:'';display:block;width:35%;height:100%;background:#27d3ff;animation:run 1.1s ease-in-out infinite alternate}
  body.failed .track:after{animation:none;width:100%;background:#ff5f67}body.failed small{color:#ff7a82}
  .detail{display:none}body.failed .detail{display:block}
  @keyframes run{to{transform:translateX(190%)}}</style><body class='__STATE__'><div class='box'><small>YANXIANG RACING</small>
  <h1 id='startup-title'>__TITLE__</h1><p id='startup-message'>__MESSAGE__</p>__DETAIL__<div class='track'></div></div>__SCRIPT__</body></html>"""


def startup_page(
    message: str = "正在加载本地数据，OCR 将在需要时启动。",
    *,
    failed: bool = False,
    log_path: Path | None = None,
    target_url: str | None = None,
) -> str:
    detail = ""
    if log_path is not None:
        detail = f"<p class='detail'>诊断日志：{html.escape(str(log_path))}</p>"
    script = ""
    if target_url:
        safe_target = json.dumps(target_url).replace("</", "<\\/")
        script = f"""<script>(() => {{
        const target = {safe_target};
        const started = Date.now();
        let navigating = false;
        async function probe() {{
          if (navigating) return;
          try {{
            await fetch(target + '/health', {{mode: 'no-cors', cache: 'no-store'}});
            navigating = true;
            window.location.replace(target);
            return;
          }} catch (_) {{}}
          const elapsed = Date.now() - started;
          if (elapsed >= 90000) {{
            document.body.classList.add('failed');
            document.getElementById('startup-title').textContent = '启动失败';
            document.getElementById('startup-message').textContent = '启动超过 90 秒仍未完成。请关闭软件后重试，并将诊断日志交给管理员。';
            return;
          }}
          if (elapsed >= 15000) {{
            document.getElementById('startup-message').textContent = '正在升级本地数据库，数据较多时可能需要约 1 分钟，请勿强制关闭。';
          }}
          window.setTimeout(probe, 350);
        }}
        probe();
        }})();</script>"""
    return (
        STARTUP_TEMPLATE
        .replace("__STATE__", "failed" if failed else "")
        .replace("__TITLE__", "启动失败" if failed else "经费管理系统正在启动")
        .replace("__MESSAGE__", html.escape(message))
        .replace("__DETAIL__", detail)
        .replace("__SCRIPT__", script)
    )


STARTUP_HTML = startup_page()


def configure_startup_logging(runtime_home: Path) -> tuple[logging.Logger, Path]:
    log_dir = runtime_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "startup.log"
    logger = logging.getLogger("yxrt")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved = str(log_path.resolve()).casefold()
    if not any(
        isinstance(handler, RotatingFileHandler)
        and str(Path(getattr(handler, "baseFilename", "")).resolve()).casefold() == resolved
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger, log_path


def open_edge_or_default(
    url: str,
    *,
    app_mode: bool = False,
    user_data_dir: Path | None = None,
) -> subprocess.Popen | None:
    if os.name == "nt":
        roots = [os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")]
        candidates = [Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for root in roots if root]
        edge = next((item for item in candidates if item.is_file()), None)
        if edge:
            arguments = [str(edge)]
            if app_mode:
                arguments.extend([f"--app={url}", "--no-first-run"])
                arguments.extend(["--disable-background-mode", "--disable-extensions"])
                if user_data_dir is not None:
                    user_data_dir.mkdir(parents=True, exist_ok=True)
                    arguments.append(f"--user-data-dir={user_data_dir}")
            else:
                arguments.append(url)
            return subprocess.Popen(arguments, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return None
        except OSError:
            pass
    webbrowser.open(url)
    return None


def edge_app_running(user_data_dir: Path) -> bool:
    marker = str(user_data_dir.resolve()).casefold()
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            if str(process.info.get("name") or "").casefold() != "msedge.exe":
                continue
            command = " ".join(str(value) for value in (process.info.get("cmdline") or [])).casefold()
            if marker in command:
                return True
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return False


def show_startup_error(message: str, log_path: Path) -> None:
    detail = f"{message}\n\n诊断日志：{log_path}"
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, detail, "燕翔车队经费管理系统启动失败", 0x10)
            return
        except Exception:
            pass
    logging.getLogger("yxrt").error(detail)


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
    logger, log_path = configure_startup_logging(runtime_home)
    logger.info("desktop startup version=%s executable=%s", __version__, sys.executable)
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
    server_thread: threading.Thread | None = None
    startup_failure: list[str] = []
    service_ready = app_ready(host, port)
    if not service_ready:
        config = desktop_server_config(host, port)
        server = uvicorn.Server(config)

        def run_server() -> None:
            try:
                logger.info("web service starting host=%s port=%s", host, port)
                server.run()
                if not server.started:
                    startup_failure.append("本地服务未能完成启动")
                    logger.error("web service stopped before startup completed")
            except BaseException as exc:
                startup_failure.append(f"{type(exc).__name__}: {exc}")
                logger.exception("web service startup failed")

        server_thread = threading.Thread(target=run_server, name="yxrt-web", daemon=True)
        server_thread.start()

    # GitHub Actions has no interactive desktop. Keep the frozen process alive
    # so the release workflow can verify the bundled service without WebView2.
    if os.environ.get("YXRT_SMOKE_TEST") == "1":
        logger.info("headless frozen smoke mode enabled")
        try:
            while True:
                if app_ready(host, port):
                    logger.info("headless frozen smoke service ready url=%s", url)
                    time.sleep(1)
                    continue
                if startup_failure:
                    raise RuntimeError(startup_failure[-1])
                if server_thread is not None and not server_thread.is_alive():
                    raise RuntimeError("本地服务在健康检查前意外停止")
                time.sleep(0.1)
        finally:
            if server is not None:
                server.should_exit = True

    # Edge app mode isolates the UI from Python's GUI thread and avoids
    # WebView2/pythonnet deadlocks seen on some Windows installations.
    if os.name == "nt" and os.environ.get("YXRT_EMBEDDED_WEBVIEW") != "1":
        ready = service_ready
        if not ready:
            for _ in range(900):
                if app_ready(host, port):
                    ready = True
                    break
                if startup_failure or (server_thread is not None and not server_thread.is_alive()):
                    break
                time.sleep(0.1)
        if not ready:
            message = startup_failure[-1] if startup_failure else "启动超过 90 秒仍未完成，请关闭软件后重试。"
            logger.error("edge app startup failed: %s", message)
            show_startup_error(message, log_path)
            if server is not None:
                server.should_exit = True
            return
        logger.info("opening Edge app window url=%s", url)
        edge_profile = runtime_home / "data" / "edge-app"
        edge_process = open_edge_or_default(
            url,
            app_mode=True,
            user_data_dir=edge_profile,
        )
        if edge_process is not None:
            # Edge can hand the app window to another process and let the
            # launcher exit. Track the isolated profile instead of the broker
            # PID, and always allow a short startup grace period.
            grace_deadline = time.monotonic() + 10
            while (
                edge_process.poll() is None
                or time.monotonic() < grace_deadline
                or edge_app_running(edge_profile)
            ):
                time.sleep(0.5)
        elif server is not None:
            while server_thread is not None and server_thread.is_alive():
                time.sleep(1)
        if server is not None:
            server.should_exit = True
        return

    try:
        import webview
        webview_storage = runtime_home / "data" / "webview"
        webview_storage.mkdir(parents=True, exist_ok=True)
        desktop_api = DesktopApi()
        window = webview.create_window(
            f"燕翔车队经费管理系统 V{__version__}",
            url if service_ready else None,
            html=None if service_ready else startup_page(log_path=log_path, target_url=url),
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
                logger.info("reused ready service url=%s", url)
                return
            for attempt in range(900):
                if app_ready(host, port):
                    logger.info("web service ready after %.1fs", attempt / 10)
                    return
                if startup_failure:
                    logger.error("startup failure visible after client timeout: %s", startup_failure[-1])
                    return
                if server_thread is not None and not server_thread.is_alive():
                    message = "本地服务意外停止，请查看诊断日志。"
                    logger.error(message)
                    return
                time.sleep(0.1)
            message = "启动超过 90 秒仍未完成。请关闭软件后重试，并将诊断日志交给管理员。"
            logger.error(message)

        webview.start(
            finish_startup,
            gui="edgechromium",
            private_mode=False,
            storage_path=str(webview_storage),
            debug=False,
        )
    except Exception:
        logger.exception("desktop window startup failed")
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
