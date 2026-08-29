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
from .config import RUNTIME_HOME, TMP_DIR
from .database import new_id, utc_now


UPDATE_REPOSITORY = os.environ.get("YXRT_UPDATE_REPOSITORY", "3322810958-sudo/YXRT_Money_APP").strip().strip("/")
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


def _release_details(release: dict[str, Any]) -> dict[str, Any]:
    latest_version = str(release.get("tag_name") or "").lstrip("vV")
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    packages = [item for item in assets if str(item.get("name") or "").lower().endswith(".zip")]

    def package_score(item: dict[str, Any]) -> tuple[int, int]:
        name = str(item.get("name") or "").lower()
        is_patch = any(word in name for word in ("update", "patch", "补丁"))
        is_full = any(word in name for word in ("full", "complete", "完整版"))
        return (0 if is_patch else (2 if is_full else 1), 0 if "windows" in name else 1)

    packages.sort(key=package_score)
    package = packages[0] if packages else None
    checksum = None
    if package:
        target = str(package.get("name") or "").lower()
        checksum = next((item for item in assets if str(item.get("name") or "").lower() == f"{target}.sha256"), None)
        if checksum is None:
            checksum = next((item for item in assets if str(item.get("name") or "").lower().endswith(".sha256")), None)
    comparison = (_version_tuple(latest_version) > _version_tuple(__version__)) - (_version_tuple(latest_version) < _version_tuple(__version__))
    return {
        "current_version": __version__, "latest_version": latest_version or __version__,
        "available": bool(latest_version and latest_version != __version__), "release_available": True,
        "direction": "upgrade" if comparison > 0 else ("rollback" if comparison < 0 else "current"),
        "release_name": str(release.get("name") or release.get("tag_name") or latest_version),
        "release_notes": str(release.get("body") or ""), "published_at": str(release.get("published_at") or ""),
        "release_url": str(release.get("html_url") or f"https://github.com/{UPDATE_REPOSITORY}/releases"),
        "package": {
            "name": str(package.get("name") or ""), "url": str(package.get("url") or package.get("browser_download_url") or ""),
            "browser_url": str(package.get("browser_download_url") or ""), "size": int(package.get("size") or 0),
        } if package else None,
        "checksum_url": str(checksum.get("url") or checksum.get("browser_download_url") or "") if checksum else "",
        "install_supported": bool(package and checksum and getattr(sys, "frozen", False)),
        "repository": UPDATE_REPOSITORY,
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
    result = _release_details(response.json())
    result["available"] = result["direction"] == "upgrade"
    result["message"] = "发现新版本" if result["available"] else "当前已是最新版本"
    return result


def list_patch_releases(limit: int = 30) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases?per_page={max(1, min(50, limit))}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers=_github_headers()) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"无法读取 GitHub 补丁列表：{exc}") from exc
    releases = response.json() if isinstance(response.json(), list) else []
    return [_release_details(item) for item in releases if not item.get("draft") and not item.get("prerelease")]


def release_for_version(version: str) -> dict[str, Any]:
    safe = re.sub(r"[^0-9A-Za-z._-]", "", str(version or ""))
    if not safe:
        return check_for_update()
    url = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/tags/v{safe.lstrip('vV')}"
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers=_github_headers()) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"未找到 V{safe} 补丁：{exc}") from exc
    return _release_details(response.json())


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


def start_update_download(user_id: str, target_version: str = "") -> dict[str, Any]:
    release = release_for_version(target_version) if target_version else check_for_update()
    if not release.get("available"):
        raise RuntimeError(str(release.get("message") or "目标版本与当前版本相同"))
    if not release.get("package") or not release.get("checksum_url"):
        raise RuntimeError("新版本缺少 Windows 更新包或 SHA-256 校验文件")
    job_id = new_id("update")
    job = {
        "id": job_id, "status": "downloading", "progress": 1, "message": "正在连接 GitHub",
        "error": "", "created_by": user_id, "created_at": utc_now(), "updated_at": utc_now(),
        "latest_version": release.get("latest_version"), "release_url": release.get("release_url"),
        "direction": release.get("direction", "upgrade"),
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
  [Parameter(Mandatory=$true)][string]$ExecutableName,
  [Parameter(Mandatory=$true)][string]$RuntimePath,
  [Parameter(Mandatory=$true)][string]$ExpectedVersion,
  [string]$RestoreArchivePath = '',
  [string]$SafetyBackupPath = ''
)
$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($TargetPath)
$archive = [IO.Path]::GetFullPath($ArchivePath)
$runtime = [IO.Path]::GetFullPath($RuntimePath)
if ($target.Length -lt 6 -or -not (Test-Path -LiteralPath $target -PathType Container)) { throw '更新目标目录不安全' }
if ($runtime.Length -lt 6 -or -not (Test-Path -LiteralPath $runtime -PathType Container)) { throw '运行数据目录不安全' }
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
$manifestPath = Join-Path $source 'patch-manifest.json'
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
  $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($manifest.product -ne '燕翔车队经费管理系统' -or $manifest.target_version -ne $ExpectedVersion) { throw '补丁版本清单不匹配' }
  foreach ($entry in $manifest.files) {
    $candidate = [IO.Path]::GetFullPath((Join-Path $source ([string]$entry.path)))
    if (-not $candidate.StartsWith($source, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw '补丁文件清单不安全' }
    if ((Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() -ne ([string]$entry.sha256).ToLowerInvariant()) { throw ('补丁文件校验失败：' + [string]$entry.path) }
  }
} elseif ([version]$ExpectedVersion -ge [version]'2.3.5') {
  throw '补丁缺少版本清单'
}
$newExe = Join-Path $source $ExecutableName
if (-not (Test-Path -LiteralPath $newExe -PathType Leaf)) { throw '更新包中缺少主程序' }
$backup = Join-Path $target ('.update-backup-' + (Get-Date -Format 'yyyyMMddHHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$preserve = @('data','uploads','models','tmp','backups','.env')
$installed = [Collections.Generic.List[string]]::new()
$currentName = ''
function Restore-RuntimeArchive([string]$ZipPath, [string]$RuntimeRoot, [string]$WorkRoot) {
  if (-not $ZipPath) { return }
  $zip = [IO.Path]::GetFullPath($ZipPath)
  if (-not (Test-Path -LiteralPath $zip -PathType Leaf)) { throw '目标版本缺少数据恢复点' }
  $restore = Join-Path $WorkRoot ('runtime-' + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $restore -Force | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $restore -Force
  $database = Join-Path $restore 'database.sqlite'
  if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { throw '数据恢复点缺少数据库' }
  $dataDir = Join-Path $RuntimeRoot 'data'
  New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
  foreach ($name in @('yanxiang_expense.db-wal','yanxiang_expense.db-shm')) {
    $sidecar = Join-Path $dataDir $name
    if (Test-Path -LiteralPath $sidecar) { Remove-Item -LiteralPath $sidecar -Force }
  }
  Copy-Item -LiteralPath $database -Destination (Join-Path $dataDir 'yanxiang_expense.db') -Force
  $uploadsSource = Join-Path $restore 'uploads'
  if (Test-Path -LiteralPath $uploadsSource -PathType Container) {
    $uploadsTarget = Join-Path $RuntimeRoot 'uploads'
    if (Test-Path -LiteralPath $uploadsTarget) { Remove-Item -LiteralPath $uploadsTarget -Recurse -Force }
    Copy-Item -LiteralPath $uploadsSource -Destination $uploadsTarget -Recurse -Force
  }
}
try {
  foreach ($item in Get-ChildItem -LiteralPath $source) {
    $currentName = $item.Name
    if ($preserve -contains $item.Name) { continue }
    $destination = Join-Path $target $item.Name
    if (Test-Path -LiteralPath $destination) {
      Move-Item -LiteralPath $destination -Destination (Join-Path $backup $item.Name) -Force
    }
    Copy-Item -LiteralPath $item.FullName -Destination $destination -Recurse -Force
    $installed.Add($item.Name)
  }
  $exe = Join-Path $target $ExecutableName
  if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw '更新后主程序校验失败' }
  if ($RestoreArchivePath) { Restore-RuntimeArchive $RestoreArchivePath $runtime $extract }
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
  if ($SafetyBackupPath) {
    try { Restore-RuntimeArchive $SafetyBackupPath $runtime $extract } catch { }
  }
  "$(Get-Date -Format s) 更新失败并已回滚：$message" | Out-File -LiteralPath (Join-Path $target 'update-error.log') -Encoding utf8 -Append
  $oldExe = Join-Path $target $ExecutableName
  if (Test-Path -LiteralPath $oldExe -PathType Leaf) { Start-Process -FilePath $oldExe -WorkingDirectory $target }
  throw
}
'''


def schedule_update_install(
    job_id: str, user_id: str, *, restore_archive: Path | None = None,
    safety_backup: Path | None = None,
) -> dict[str, Any]:
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
    install_home = executable.parent
    data_home = RUNTIME_HOME.resolve()
    script_path = archive.with_suffix(".ps1")
    script_path.write_text(_UPDATER_SCRIPT, encoding="utf-8-sig")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(script_path), "-ProcessId", str(os.getpid()), "-ArchivePath", str(archive),
            "-TargetPath", str(install_home), "-ExecutableName", executable.name,
            "-RuntimePath", str(data_home), "-RestoreArchivePath", str(restore_archive or ""),
            "-SafetyBackupPath", str(safety_backup or ""), "-ExpectedVersion", str(job.get("latest_version") or ""),
        ],
        cwd=str(install_home),
        creationflags=creation_flags,
        close_fds=True,
    )
    threading.Timer(1.2, lambda: os._exit(0)).start()
    return {"ok": True, "message": "更新安装程序已启动，软件将自动重启"}
