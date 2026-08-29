from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".m4v", ".mov", ".avi", ".mkv"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".log"}
OFFICE_ARCHIVES = {".docx", ".pptx", ".xlsx", ".xlsm", ".odt", ".ods", ".odp"}


def story_asset_role(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "gallery"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "document"


def _clean_text(value: str, limit: int = 120_000) -> str:
    value = value.replace("\x00", " ").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()[:limit]


def _zip_xml_text(path: Path) -> str:
    pieces: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(archive.namelist())
        preferred = [
            name for name in names
            if name == "word/document.xml"
            or name == "content.xml"
            or name == "xl/sharedStrings.xml"
            or (name.startswith("ppt/slides/slide") and name.endswith(".xml"))
            or (name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        ]
        for name in preferred[:300]:
            data = archive.read(name)
            if len(data) > 20 * 1024 * 1024:
                continue
            try:
                root = ElementTree.fromstring(data)
            except ElementTree.ParseError:
                continue
            text = " ".join(part.strip() for part in root.itertext() if part and part.strip())
            if text:
                pieces.append(text)
            if sum(len(item) for item in pieces) >= 120_000:
                break
    return _clean_text("\n\n".join(pieces))


def extract_story_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return _clean_text("\n\n".join((page.extract_text() or "") for page in reader.pages[:300]))
        if suffix in TEXT_EXTENSIONS:
            for encoding in ("utf-8-sig", "gb18030", "utf-16"):
                try:
                    return _clean_text(path.read_text(encoding=encoding, errors="strict"))
                except UnicodeError:
                    continue
            return _clean_text(path.read_text(encoding="utf-8", errors="replace"))
        if suffix in OFFICE_ARCHIVES:
            return _zip_xml_text(path)
    except (OSError, ValueError, zipfile.BadZipFile):
        return ""
    return ""


def extract_embedded_images(path: Path, target_dir: Path) -> list[Path]:
    if path.suffix.lower() not in OFFICE_ARCHIVES:
        return []
    extracted: list[Path] = []
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = [
                info for info in archive.infolist()
                if not info.is_dir()
                and Path(info.filename).suffix.lower() in IMAGE_EXTENSIONS
                and any(marker in info.filename.replace("\\", "/") for marker in ("word/media/", "ppt/media/", "xl/media/", "Pictures/"))
            ]
            for index, info in enumerate(candidates[:100], 1):
                if info.file_size > 50 * 1024 * 1024:
                    continue
                suffix = Path(info.filename).suffix.lower()
                target = target_dir / f"embedded_{index:03d}{suffix}"
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                extracted.append(target)
    except (OSError, zipfile.BadZipFile):
        return []
    return extracted
