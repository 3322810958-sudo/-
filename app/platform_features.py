from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


OFFICE_WORD = {".doc", ".docx", ".rtf", ".odt"}
OFFICE_EXCEL = {".xls", ".xlsx", ".xlsm", ".ods", ".csv"}
OFFICE_POWERPOINT = {".ppt", ".pptx", ".odp"}
TEXT_EXTENSIONS = {".txt", ".md", ".log"}


def open_local_file(path: Path) -> None:
    resolved = path.resolve(strict=True)
    if os.name == "nt":
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return
    if os.name == "posix":
        command = "open" if shutil.which("open") else "xdg-open"
        subprocess.Popen([command, str(resolved)], close_fds=True)
        return
    raise OSError("当前系统不支持直接打开本机文件")


def _office_com_to_pdf(source: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False
    pythoncom.CoInitialize()
    app = None
    document = None
    try:
        suffix = source.suffix.lower()
        if suffix in OFFICE_WORD:
            app = win32com.client.DispatchEx("Word.Application")
            app.Visible = False
            document = app.Documents.Open(str(source.resolve()), ReadOnly=True)
            document.ExportAsFixedFormat(str(target.resolve()), 17)
        elif suffix in OFFICE_EXCEL:
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            document = app.Workbooks.Open(str(source.resolve()), ReadOnly=True)
            document.ExportAsFixedFormat(0, str(target.resolve()))
        elif suffix in OFFICE_POWERPOINT:
            app = win32com.client.DispatchEx("PowerPoint.Application")
            document = app.Presentations.Open(str(source.resolve()), WithWindow=False)
            document.SaveAs(str(target.resolve()), 32)
        else:
            return False
        return target.is_file() and target.stat().st_size > 0
    except Exception:
        target.unlink(missing_ok=True)
        return False
    finally:
        try:
            if document is not None:
                document.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def _libreoffice_to_pdf(source: Path, target_dir: Path) -> Path | None:
    candidates = [
        shutil.which("soffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    executable = next((Path(value) for value in candidates if value and Path(value).is_file()), None)
    if not executable:
        return None
    profile = Path(tempfile.mkdtemp(prefix="yxrt_lo_profile_"))
    try:
        result = subprocess.run(
            [
                str(executable), "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--convert-to", "pdf", "--outdir", str(target_dir), str(source.resolve()),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        candidate = target_dir / f"{source.stem}.pdf"
        return candidate if result.returncode == 0 and candidate.is_file() else None
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def convert_office_to_pdf(source: Path, target_dir: Path) -> Path | None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}.pdf"
    if _office_com_to_pdf(source, target):
        return target
    converted = _libreoffice_to_pdf(source, target_dir)
    if converted and converted != target:
        converted.replace(target)
    return target if target.is_file() else None


def text_to_pdf(source: Path, target_dir: Path) -> Path | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
    except ImportError:
        return None
    target = target_dir / f"{source.stem}.pdf"
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    page_width, page_height = A4
    drawing = canvas.Canvas(str(target), pagesize=A4)
    drawing.setFont("STSong-Light", 10)
    y = page_height - 40
    for line in source.read_text("utf-8", errors="replace").splitlines() or [""]:
        chunks = [line[index:index + 75] for index in range(0, max(1, len(line)), 75)] or [""]
        for chunk in chunks:
            if y < 40:
                drawing.showPage(); drawing.setFont("STSong-Light", 10); y = page_height - 40
            drawing.drawString(36, y, chunk); y -= 15
    drawing.save()
    return target if target.is_file() else None
