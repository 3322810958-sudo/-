from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .config import TMP_DIR
from .database import new_id, utc_now


UPDATE_REPOSITORY = os.environ.get("YXRT_UPDATE_REPOSITORY", "3322810958-sudo/-").strip().strip("/")
UPDATE_API = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
UPDATE_JOBS: dict[str, dict[str, Any]] = {}
UPDATE_LOCK = threading.Lock()


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(item) for item in re.findall(r"\d+", str(value or ""))[:3]]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def _github_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"YanxiangExpenseV2/{__version__}",
    }


def check_for_update() -> dict[str, Any]:
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers=_github_headers()) as client:
            response = client.get(UPDATE_API)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"无法连接 GitHub：{exc}") from exc
    if response.status_code == 404:
        return {
            "current_version": __version__, "available": False, "release_available": False,
            "message": "GitHub 暂无可用的软件发布包", "repository": UPDATE_REPOSITORY,
        }
    if response.status_code != 200:
        raise RuntimeError(f"GitHub 版本检查失败：HTTP {response.status_code}")
    release = response.json()
    latest_version = str(release.get("tag_name") or "").lstrip("vV")
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    packages = [item for item in assets if str(item.get("name") or "").lower().endswith((".zip", ".exe"))]

    def package_score(item: dict[str, Any]) -> tuple[int, int, int]:
        name = str(item.get("name") or "").lower()
        is_update = any(word in name for word in ("update", "patch", "补丁"))
        is_full = any(word in name for word in ("full", "complete", "完整版"))
        return (
            0 if "windows" in name else 1,
            0 if is_update else (2 if is_full else 1),
            0 if name.endswith(".zip") else 1,
        )

    packages.sort(key=package_score)
    package = packages[0] if packages else None
    checksum = None
    if package:
        target = str(package.get("name") or "").lower()
        checksum = next((item for item in assets if str(item.get("name") or "").lower() == f"{target}.sha256"), None)
        if checksum is None:
            checksum = next((item for item in assets if str(item.get("name") or "").lower().endswith(".sha256")), None)
    available = bool(latest_version and _version_tuple(latest_version) > _version_tuple(__version__))
    return {
        "current_version": __version__,
        "latest_version": latest_version or __version__,
        "available": available,
        "release_available": True,
        "release_name": str(release.get("name") or release.get("tag_name") or latest_version),
        "release_notes": str(release.get("body") or ""),
        "published_at": str(release.get("published_at") or ""),
        "release_url": str(release.get("html_url") or f"https://github.com/{UPDATE_REPOSITORY}/releases"),
        "package": {
            "name": str(package.get("name") or ""),
            "url": str(package.get("url") or package.get("browser_download_url") or ""),
            "browser_url": str(package.get("browser_download_url") or ""),
            "size": int(package.get("size") or 0),
        } if package else None,
        "checksum_url": str(checksum.get("url") or checksum.get("browser_download_url") or "") if checksum else "",
        "install_supported": bool(package and checksum and getattr(sys, "frozen", False)),
        "message": "发现新版本" if available else "当前已是最新版本",
        "repository": UPDATE_REPOSITORY,
    }


def _safe_asset_url(url: str) -> bool:
    allowed = (
        f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/assets/",
        f"https://github.com/{UPDATE_REPOSITORY}/releases/download/",
    )
    return any(str(url).startswith(prefix) for prefix in allowed)


def _set_job(job_id: str, **changes: Any) -> None:
    with UPDATE_LOCK:
        if job_id in UPDATE_JOBS:
            UPDATE_JOBS[job_id].update(changes)
            UPDATE_JOBS[job_id]["updated_at"] = utc_now()


def _download_update(job_id: str, release: dict[str, Any]) -> None:
    package = release.get("package") or {}
    asset_url = str(package.get("url") or "")
    checksum_url = str(release.get("checksum_url") or "")
    if not _safe_asset_url(asset_url) or not _safe_asset_url(checksum_url):
        _set_job(job_id, status="failed", error="更新包地址未通过安全检查")
        return
    update_dir = TMP_DIR / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(str(package.get("name") or f"yanxiang-{job_id}.zip")).name
    target = update_dir / f"{job_id}-{safe_name}"
    digest = hashlib.sha256()
    try:
        download_headers = {**_github_headers(), "Accept": "application/octet-stream"}
        with httpx.Client(timeout=60, follow_redirects=True, headers=download_headers) as client:
            with client.stream("GET", asset_url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or package.get("size") or 0)
                received = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        output.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        progress = 5 + int(received / total * 85) if total else min(90, 5 + received // (2 * 1024 * 1024))
                        _set_job(job_id, progress=min(90, progress), message="正在下载更新包")
            checksum_response = client.get(checksum_url)
            checksum_response.raise_for_status()
        expected_match = re.search(r"\b([a-fA-F0-9]{64})\b", checksum_response.text)
        if not expected_match:
            raise RuntimeError("更新包校验文件格式不正确")
        actual = digest.hexdigest().lower()
        expected = expected_match.group(1).lower()
        if actual != expected:
            raise RuntimeError("更新包完整性校验失败，已停止安装")
        _set_job(
            job_id, status="ready", progress=100, message="更新包已下载并通过 SHA-256 校验",
            file_path=str(target), sha256=actual,
        )
    except Exception as exc:
        target.unlink(missing_ok=True)
        _set_job(job_id, status="failed", error=str(exc)[:500], message="更新下载失败")


def start_update_download(user_id: str) -> dict[str, Any]:
    release = check_for_update()
    if not release.get("available"):
        raise RuntimeError(str(release.get("message") or "当前没有可安装的新版本"))
    if not release.get("package") or not release.get("checksum_url"):
        raise RuntimeError("新版本缺少 Windows 更新包或 SHA-256 校验文件")
    job_id = new_id("update")
    job = {
        "id": job_id, "status": "downloading", "progress": 1, "message": "正在连接 GitHub",
        "error": "", "created_by": user_id, "created_at": utc_now(), "updated_at": utc_now(),
        "latest_version": release.get("latest_version"), "release_url": release.get("release_url"),
    }
    with UPDATE_LOCK:
        UPDATE_JOBS[job_id] = job
    threading.Thread(target=_download_update, args=(job_id, release), name="yxrt-update-download", daemon=True).start()
    return dict(job)


def get_update_job(job_id: str) -> dict[str, Any] | None:
    with UPDATE_LOCK:
        job = UPDATE_JOBS.get(job_id)
        if not job:
            return None
        return {key: value for key, value in job.items() if key != "file_path"}


_UPDATER_SCRIPT = r'''param(
  [Parameter(Mandatory=$true)][int]$ProcessId,
  [Parameter(Mandatory=$true)][string]$ArchivePath,
  [Parameter(Mandatory=$true)][string]$TargetPath,
  [Parameter(Mandatory=$true)][string]$ExecutableName
)
$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($TargetPath)
$archive = [IO.Path]::GetFullPath($ArchivePath)
if ($target.Length -lt 6 -or -not (Test-Path -LiteralPath $target -PathType Container)) { throw '更新目标目录不安全' }
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw '更新包不存在' }
try { Wait-Process -Id $ProcessId -Timeout 180 -ErrorAction Stop } catch { Start-Sleep -Seconds 2 }
$extract = Join-Path ([IO.Path]::GetDirectoryName($archive)) ('extract-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $extract -Force | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
$source = $extract
$top = @(Get-ChildItem -LiteralPath $extract)
if ($top.Count -eq 1 -and $top[0].PSIsContainer) { $source = $top[0].FullName }
$source = [IO.Path]::GetFullPath($source)
if (-not $source.StartsWith([IO.Path]::GetFullPath($extract), [StringComparison]::OrdinalIgnoreCase)) { throw '更新包目录结构不安全' }
$newExe = Join-Path $source $ExecutableName
if (-not (Test-Path -LiteralPath $newExe -PathType Leaf)) { throw '更新包中缺少主程序' }
$backup = Join-Path $target ('.update-backup-' + (Get-Date -Format 'yyyyMMddHHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$preserve = @('data','uploads','models','tmp','.env')
$installed = [Collections.Generic.List[string]]::new()
$currentName = ''
try {
  foreach ($item in Get-ChildItem -LiteralPath $source) {
    $currentName = $item.Name
    if ($preserve -contains $item.Name -and (Test-Path -LiteralPath (Join-Path $target $item.Name))) { continue }
    $destination = Join-Path $target $item.Name
    if (Test-Path -LiteralPath $destination) {
      Move-Item -LiteralPath $destination -Destination (Join-Path $backup $item.Name) -Force
    }
    Copy-Item -LiteralPath $item.FullName -Destination $destination -Recurse -Force
    $installed.Add($item.Name)
  }
  $exe = Join-Path $target $ExecutableName
  if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw '更新后主程序校验失败' }
  Start-Process -FilePath $exe -WorkingDirectory $target
  Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
} catch {
  $message = $_.Exception.Message
  if ($currentName -and -not $installed.Contains($currentName)) { $installed.Add($currentName) }
  foreach ($name in $installed) {
    $destination = Join-Path $target $name
    if (Test-Path -LiteralPath $destination) {
      Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($item in Get-ChildItem -LiteralPath $backup -ErrorAction SilentlyContinue) {
    Move-Item -LiteralPath $item.FullName -Destination (Join-Path $target $item.Name) -Force
  }
  "$(Get-Date -Format s) 更新失败并已回滚：$message" | Out-File -LiteralPath (Join-Path $target 'update-error.log') -Encoding utf8 -Append
  $oldExe = Join-Path $target $ExecutableName
  if (Test-Path -LiteralPath $oldExe -PathType Leaf) { Start-Process -FilePath $oldExe -WorkingDirectory $target }
  throw
}
'''


def schedule_update_install(job_id: str, user_id: str) -> dict[str, Any]:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("源码运行模式不能自动覆盖文件，请使用 GitHub 拉取更新")
    with UPDATE_LOCK:
        job = UPDATE_JOBS.get(job_id)
        if not job or job.get("created_by") != user_id:
            raise RuntimeError("更新任务不存在")
        if job.get("status") != "ready" or not job.get("file_path"):
            raise RuntimeError("更新包尚未准备完成")
        archive = Path(str(job["file_path"])).resolve()
        job["status"] = "installing"
        job["message"] = "软件即将重启并安装更新"

    executable = Path(sys.executable).resolve()
    runtime_home = executable.parent
    script_path = archive.with_suffix(".ps1")
    script_path.write_text(_UPDATER_SCRIPT, encoding="utf-8-sig")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(script_path), "-ProcessId", str(os.getpid()), "-ArchivePath", str(archive),
            "-TargetPath", str(runtime_home), "-ExecutableName", executable.name,
        ],
        cwd=str(runtime_home),
        creationflags=creation_flags,
        close_fds=True,
    )
    threading.Timer(1.2, lambda: os._exit(0)).start()
    return {"ok": True, "message": "更新安装程序已启动，软件将自动重启"}
