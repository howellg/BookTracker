"""v0.17 EPUB reading comfort, inline annotation tools and sync-safe renaming."""
from __future__ import annotations

import hashlib

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
from test_v015 import make_epub, with_book

from booktracker import sync
from booktracker.database import Library
from booktracker.reader_gui import EpubReader


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    library = Library(tmp_path / "reader.sqlite3")
    yield library
    library.close()


@pytest.fixture
def reader(app, db, tmp_path):
    path = make_epub(tmp_path / "comfort.epub", sections=[
        "<h1>Chapter One</h1>" + "<p>The elegant dragon flies.</p>" * 80,
        "<h2>Chapter Two</h2><p>New country and plot.</p>",
    ])
    work, file_id = with_book(db, path)
    settings = QSettings(str(tmp_path / "reader.ini"), QSettings.Format.IniFormat)
    view = EpubReader(None, db, file_id, settings=settings)
    view.show()
    app.processEvents()
    yield view, db, work, file_id, settings, path
    view.close()


def test_display_controls_and_reader_tabs(reader, app):
    view, db, work, _file_id, settings, _ = reader
    assert view.sidebar_tabs.count() == 3
    assert [view.sidebar_tabs.tabText(i) for i in range(3)] == [
        "Contents", "Search", "Saved"
    ]
    view.font_family.setCurrentText("Mono")
    view.line_spacing.setCurrentText("Relaxed")
    view.page_margin.setCurrentText("Wide")
    view.theme.setCurrentText("High contrast")
    app.processEvents()
    assert view.viewer.document().defaultFont().family() == "DejaVu Sans Mono"
    assert view.viewer.document().documentMargin() == 55
    first = view.viewer.document().firstBlock()
    assert first.blockFormat().lineHeight() == 185
    assert settings.value("reader_family") == "Mono"
    assert settings.value("reader_margin") == "Wide"
    assert settings.value("reader_theme") == "High contrast"
    view.focus_button.setChecked(True)
    app.processEvents()
    assert not view.splitter.widget(0).isVisible()
    assert view.splitter.widget(1).isVisible()
    view.focus_button.setChecked(False)
    app.processEvents()
    assert view.splitter.widget(0).isVisible()
    assert db.history(work) == []
    assert db.active_run(work) is None


def test_compact_reader_layout_is_usable(reader, app):
    view, *_ = reader
    view.resize(660, 480)
    app.processEvents()
    assert view.minimumSizeHint().width() <= 660
    assert view.minimumSizeHint().height() <= 480
    assert view.search.width() >= 150
    assert view.viewer.viewport().width() >= 300
    view.sidebar_tabs.setCurrentIndex(2)
    app.processEvents()
    assert view.notes_list.width() >= 150


def test_settings_restore_and_invalid_setting_fallback(reader, app, tmp_path):
    view, db, work, file_id, settings, _ = reader
    view.theme.setCurrentText("Sepia")
    view.font_family.setCurrentText("Sans")
    view.line_spacing.setCurrentText("Compact")
    view.page_margin.setCurrentText("Narrow")
    view.focus_button.setChecked(True)
    view.close()
    reopened = EpubReader(None, db, file_id, settings=settings)
    try:
        assert reopened.theme.currentText() == "Sepia"
        assert reopened.font_family.currentText() == "Sans"
        assert reopened.line_spacing.currentText() == "Compact"
        assert reopened.page_margin.currentText() == "Narrow"
        assert reopened.focus_button.isChecked()
        assert db.history(work) == []
    finally:
        reopened.close()
    settings.setValue("reader_family", "Unknown")
    settings.setValue("reader_margin", "Dangerously huge")
    fallback = EpubReader(None, db, file_id, settings=settings)
    try:
        assert fallback.font_family.currentText() == "Serif"
        assert fallback.page_margin.currentText() == "Comfortable"
    finally:
        fallback.close()


def test_section_meter_does_not_create_reading_history(reader, app):
    view, db, work, _, _, _ = reader
    bar = view.viewer.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 2)
    app.processEvents()
    assert 45 <= view.section_meter.value() <= 55
    assert "approx." in view.section_meter.format()
    assert db.active_run(work) is None
    assert db.history(work) == []


def test_copy_selected_text_and_reader_bookmark_rename(reader, monkeypatch, app):
    view, db, work, file_id, _, _ = reader
    cursor = view.viewer.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    assert view.viewer.find("dragon")
    view.copy_selection()
    assert QApplication.clipboard().text().casefold() == "dragon"
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_kw: ("A special scene", True))
    view.add_bookmark()
    assert view.sidebar_tabs.currentIndex() == 2
    bookmark = db.list_epub_bookmarks(file_id)[0]
    view.marks.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_kw: ("Renamed!", True))
    view.rename_bookmark()
    renamed = db.list_epub_bookmarks(file_id)[0]
    assert renamed["id"] == bookmark["id"]
    assert renamed["label"] == "Renamed!"
    assert renamed["scroll_ratio"] == bookmark["scroll_ratio"]
    assert renamed["section_href"] == bookmark["section_href"]
    assert view.marks.currentItem().text().endswith("Renamed!")
    assert db.history(work) == []


def test_rename_bookmark_rejects_missing_and_invalid(db, tmp_path):
    _, file_id = with_book(db, make_epub(tmp_path / "validation.epub"))
    mark = db.add_epub_bookmark(file_id, 0, "OPS/chapters/0.xhtml", 100, "Before")
    for invalid in ("", " " * 3, "x" * 301):
        with pytest.raises(ValueError):
            db.rename_epub_bookmark(mark, invalid)
    assert db.list_epub_bookmarks(file_id)[0]["label"] == "Before"
    with pytest.raises(ValueError, match="no longer exists"):
        db.rename_epub_bookmark("not-a-bookmark", "New")


def test_inline_notes_review_edit_copy_delete(reader, monkeypatch):
    view, db, work, _, _, _ = reader
    other = db.add_work({"title": "Other work", "author": "Another"})
    outside = db.add_annotation(other, kind="note", body="Unrelated note")
    entry = db.add_annotation(work, kind="quote", body="EPUB · Chapter One\nQuote:\nDragon line",
                              location=1, location_unit="chapters")
    view.refresh_notes()
    assert view.notes_list.count() == 1
    view.notes_list.setCurrentRow(0)
    assert view.selected_note()["id"] == entry
    view.copy_saved_note()
    assert "Dragon line" in QApplication.clipboard().text()
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText",
        lambda *_a, **_kw: ("Revised quote in journal", True)
    )
    view.edit_saved_note()
    changed = db.get_annotation(entry)
    assert changed["body"] == "Revised quote in journal"
    assert changed["kind"] == "quote"
    assert changed["location"] == 1
    view.notes_list.setCurrentRow(0)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes
    )
    view.delete_saved_note()
    assert not db.get_annotation(entry)
    assert db.get_annotation(outside)["body"] == "Unrelated note"
    assert db.history(work) == []


def test_save_note_appears_in_reader_saved_tab(reader, monkeypatch):
    view, db, work, _, _, _ = reader
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText",
        lambda *_a, **_kw: ("We should revisit this character", True)
    )
    view.save_note()
    assert view.sidebar_tabs.currentIndex() == 2
    assert view.notes_list.count() == 1
    note = view.selected_note()
    assert note is None or note["work_id"] == work
    saved = db.list_annotations(work_id=work)
    assert len(saved) == 1
    assert saved[0]["body"].startswith("EPUB · ")
    assert db.history(work) == []


def test_markdown_export_only_this_work(reader, monkeypatch, tmp_path):
    view, db, work, _, _, path = reader
    other = db.add_work({"title": "Secret book", "author": "Elsewhere"})
    db.add_annotation(other, kind="note", body="Secret, not for this export")
    db.add_annotation(work, kind="note", body="A first note")
    db.add_annotation(work, kind="quote", body="EPUB · Chapter One\nThe marvelous line",
                      location=1, location_unit="chapters")
    destination = tmp_path / "reader-notes.md"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *_a, **_kw: (str(destination), "Markdown files (*.md)")
    )
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    view.export_work_notes()
    text = destination.read_text(encoding="utf-8")
    assert "A first note" in text
    assert "The marvelous line" in text
    assert "EPUB section 1" in text
    assert "Secret, not for this export" not in text
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    assert "Exported 2" in view.message.text()
    assert db.history(work) == []


def test_epub_bookmark_label_sync_with_shared_folder(tmp_path):
    share = tmp_path / "shared"
    a = Library(tmp_path / "A" / "library.sqlite3")
    b = Library(tmp_path / "B" / "library.sqlite3")
    try:
        work, file_id = with_book(a, make_epub(tmp_path / "book.epub"))
        marker = a.add_epub_bookmark(file_id, 1, "OPS/chapters/1.xhtml", 600, "Original")
        def exchange(source, target):
            return sync.apply(
                target,
                sync.plan(target, sync.read_snapshot(sync.publish(source, share)))
            )
        exchange(a,b)
        exchange(b,a)
        b.rename_epub_bookmark(marker, "Renamed remotely")
        exchange(b,a)
        row = a.list_epub_bookmarks(file_id)[0]
        assert row["id"] == marker
        assert row["label"] == "Renamed remotely"
        assert row["scroll_ratio"] == 600
        assert a.get_work(work)["status"] == "plan_to_read"
    finally:
        a.close()
        b.close()
