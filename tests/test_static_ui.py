from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")


def test_high_readability_modes_are_available() -> None:
    for mode in ("clear-dark", "light", "racing-blue", "graphite", "midnight", "teal", "warm"):
        assert f'value="{mode}"' in HTML
        assert f'html[data-theme="{mode}"]' in CSS or mode == "clear-dark"
    assert "yanxiang-display-mode" in JS


def test_destructive_actions_use_centered_app_dialog() -> None:
    assert 'id="decisionDialog"' in HTML
    assert "showDecision" in JS
    assert "confirm(" not in JS
    assert "prompt(" not in JS
    assert "alert(" not in JS


def test_custom_shortcut_editor_is_wired() -> None:
    assert 'id="shortcutsDialog"' in HTML
    assert 'id="openShortcutsBtn"' in HTML
    assert "SHORTCUT_DEFINITIONS" in JS
    assert "handleGlobalShortcut" in JS
    assert "shortcutDuplicates" in JS


def test_season_and_creator_interfaces_are_wired() -> None:
    for element_id in (
        "currentSeasonLabel",
        "seasonManagerDialog",
        "seasonManagerList",
        "departmentManagerList",
        "creatorDialog",
        "creatorCards",
    ):
        assert f'id="{element_id}"' in HTML
    assert 'data-view="creators"' in HTML
    assert 'id="creatorsSection"' in HTML
    assert "/api/admin/seasons" in JS
    assert "/api/admin/departments" in JS
    assert "/api/admin/creators" in JS
    assert "openSeasonManager" in JS
    assert "renderCreators" in JS
    assert "全赛季共享" in HTML and "全赛季共享" in JS
    assert 'id="creatorSeason"' not in HTML
    assert '$("creatorSeason")' not in JS


def test_v222_previews_pdf_backup_and_defaults_are_wired() -> None:
    for element_id in (
        "invoiceAttachmentPreview", "batchFilePreview", "attachmentViewerDialog",
        "exportPdfBtn", "pdfExportDialog", "downloadBackupBtn", "invoiceDefaultsDialog",
    ):
        assert f'id="{element_id}"' in HTML
    assert "/api/export/pdf" in JS
    assert "downloadBackup" in JS
    assert "openAttachmentViewer" in JS
    assert "showSaveFilePicker" in JS
