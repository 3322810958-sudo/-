from __future__ import annotations

import json
import os
import shutil
import smtplib
import urllib.error
import urllib.request
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .attachments import attachment_path
from .config import RUNTIME_HOME
from .database import connect, transaction, utc_now


FEEDBACK_RECIPIENT = "3322810958@qq.com"
FEEDBACK_DIR = RUNTIME_HOME / "feedback"


def _build_archive(report_id: str) -> Path:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        report_row = conn.execute("SELECT * FROM feedback_reports WHERE id=?", (report_id,)).fetchone()
        files = conn.execute(
            """SELECT f.original_name,a.stored_name,a.mime_type,a.size_bytes
            FROM feedback_attachments f JOIN attachments a ON a.id=f.attachment_id
            WHERE f.report_id=? ORDER BY f.created_at""",
            (report_id,),
        ).fetchall()
    if not report_row:
        raise ValueError("问题反馈不存在")
    report = dict(report_row)
    archive_path = FEEDBACK_DIR / report["archive_name"]
    manifest = {
        "report_id": report_id,
        "created_at": report["created_at"],
        "season_id": report.get("season_id"),
        "name": report.get("reporter_name", ""),
        "department": report.get("department", ""),
        "contact": report.get("contact", ""),
        "description": report.get("description", ""),
        "privacy": "姓名和联系方式仅用于本地队内处理或邮件，不写入公开 GitHub 问题。",
    }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("问题说明.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        used: set[str] = set()
        for index, row in enumerate(files, 1):
            source = attachment_path(row["stored_name"])
            if not source.is_file():
                continue
            safe = Path(str(row["original_name"])).name or f"附件_{index}"
            name = f"附件/{index:03d}_{safe}"
            while name.casefold() in used:
                name = f"附件/{index:03d}_{source.stem}_{len(used) + 1}{source.suffix}"
            used.add(name.casefold())
            archive.write(source, arcname=name)
    return archive_path


def _send_email(report: dict[str, Any], archive_path: Path) -> str:
    sender = os.environ.get("YXRT_QQ_SMTP_USER", "").strip()
    auth_code = os.environ.get("YXRT_QQ_SMTP_AUTH_CODE", "").strip()
    if not sender or not auth_code:
        raise RuntimeError("QQ 邮箱 SMTP 未配置，反馈已保留在本机待发送队列")
    message = EmailMessage()
    message["Subject"] = f"燕翔车队软件问题反馈 {report['id']}"
    message["From"] = sender
    message["To"] = FEEDBACK_RECIPIENT
    message.set_content(
        f"赛季：{report.get('season_id') or '未填写'}\n"
        f"组别：{report.get('department') or '未填写'}\n"
        f"姓名：{report.get('reporter_name') or '未填写'}\n"
        f"联系方式：{report.get('contact') or '未填写'}\n\n"
        f"问题描述：\n{report.get('description') or '未填写'}\n"
    )
    message.add_attachment(archive_path.read_bytes(), maintype="application", subtype="zip", filename=archive_path.name)
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as smtp:
        smtp.login(sender, auth_code)
        smtp.send_message(message)
    return f"mailto:{FEEDBACK_RECIPIENT}"


def _create_anonymous_github_issue(report: dict[str, Any]) -> str:
    token = os.environ.get("YXRT_FEEDBACK_GITHUB_TOKEN", "").strip()
    repository = os.environ.get("YXRT_FEEDBACK_GITHUB_REPO", "").strip()
    if not token or "/" not in repository:
        raise RuntimeError("GitHub 反馈通道未配置")
    body = {
        "title": f"[软件反馈] {report['id']}",
        "body": (
            f"赛季：{report.get('season_id') or '未填写'}\n\n"
            f"组别：{report.get('department') or '未填写'}\n\n"
            f"问题描述：\n{report.get('description') or '未填写'}\n\n"
            "隐私说明：姓名、联系方式和本地附件未提交到公开仓库。"
        ),
        "labels": ["用户反馈"],
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "YanxiangExpenseFeedback",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub 反馈提交失败：{exc}") from exc
    return str(result.get("html_url") or "")


def deliver_feedback(report_id: str) -> dict[str, Any]:
    archive_path = _build_archive(report_id)
    with connect() as conn:
        row = conn.execute("SELECT * FROM feedback_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise ValueError("问题反馈不存在")
    report = dict(row)
    method = "local_queue"
    reference = str(archive_path)
    error = ""
    status = "queued"
    try:
        reference = _send_email(report, archive_path)
        method = "qq_smtp"; status = "sent"
    except RuntimeError as email_error:
        error = str(email_error)
        try:
            reference = _create_anonymous_github_issue(report)
            method = "github_issue"; status = "sent"
        except RuntimeError as github_error:
            error = f"{error}；{github_error}"
    with transaction() as conn:
        conn.execute(
            """UPDATE feedback_reports SET status=?,delivery_method=?,delivery_reference=?,last_error=?,updated_at=?
            WHERE id=?""",
            (status, method, reference, error[:1000], utc_now(), report_id),
        )
    return {"id": report_id, "status": status, "delivery_method": method, "reference": reference, "message": error}


def clear_feedback_archive(report_id: str) -> None:
    with connect() as conn:
        row = conn.execute("SELECT archive_name FROM feedback_reports WHERE id=?", (report_id,)).fetchone()
    if row:
        (FEEDBACK_DIR / Path(str(row["archive_name"])).name).unlink(missing_ok=True)
