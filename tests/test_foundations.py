"""Regression tests for v0.4 library safeguards and tools."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from booktracker.backups import automatic_backup
from booktracker.database import Library
from booktracker.gui import MainWindow
from booktracker.integrations import export_file, import_file


@pytest.fixture
def lib(tmp_path):
    db = Library(tmp_path / "library.sqlite3")
    yield db
    db.close()


def test_completed_history_no_fake_dates(lib):
    ident = lib.add_work({"title": "Way of Kings", "status": "completed", "progress_unit": "pages"})
    assert lib.get_work(ident)["finished_at"] == ""
    assert lib.runs(ident) == []
    assert lib.stats()["completed_works"] == 1
    assert lib.stats()["completed_reads"] == 0


def test_shelves_crud_and_membership(lib):
    first = lib.add_work({"title": "Elantris"})
    second = lib.add_work({"title": "Warbreaker"})
    shelf = lib.create_shelf("Cosmere")
    lib.set_shelf_membership([first,second], shelf)
    assert len(lib.list_works(shelf_id=shelf)) == 2
    assert lib.shelves_for_work(first) == [shelf]
    lib.rename_shelf(shelf, "Sanderson")
    assert lib.list_shelves()[0]["name"] == "Sanderson"
    lib.set_shelf_membership([first], shelf, include=False)
    assert len(lib.list_works(shelf_id=shelf)) == 1
    lib.delete_shelf(shelf)
    assert lib.list_shelves() == []


def test_v2_json_xml_shelves_roundtrip(lib, tmp_path):
    work = lib.add_work({"title": "The Final Empire"})
    shelf = lib.create_shelf("Mistborn")
    lib.set_shelf_membership([work], shelf)
    for ext in ("json", "xml"):
        path = tmp_path / ("backup." + ext)
        export_file(lib, path)
        new = Library(tmp_path / ("restored-"+ext+".db"))
        try:
            assert import_file(new, path) == 1
            assert len(new.list_shelves()) == 1
            assert len(new.list_works(shelf_id=new.list_shelves()[0]["id"])) == 1
            assert import_file(new, path) == 0
            assert len(new.list_works(shelf_id=new.list_shelves()[0]["id"])) == 1
        finally:
            new.close()


def test_v1_backup_still_supported(lib):
    lib.add_work({"title": "Legacy"})
    data = lib.export_data()
    data["schema_version"] = 1
    data.pop("shelves")
    data.pop("work_shelves")
    assert lib.import_data(data) == 0


def test_batch_edit_atomic_without_fake_runs(lib):
    ids = [lib.add_work({"title": x}) for x in ("A", "B")]
    assert lib.batch_edit(ids, status="completed", favorite=True, add_tags=["Fantasy", "Liked"]) == 2
    for work_id in ids:
        book = lib.get_work(work_id)
        assert book["status"] == "completed"
        assert book["finished_at"] == ""
        assert book["tags"] == ["Fantasy", "Liked"]
        assert book["favorite"]
        assert lib.runs(work_id) == []
    with pytest.raises(ValueError):
        lib.batch_edit([ids[0], "not-an-id"], add_tags=["oops"])
    assert "oops" not in lib.get_work(ids[0])["tags"]


def test_remove_progress_milestone(lib):
    work = lib.add_work({"title": "Long LitRPG"})
    lib.set_progress(work, 200, date="2026-09-01")
    lib.set_progress(work, 270, date="2026-09-11")
    lib.set_progress(work, 300, date="2026-09-20")
    events = lib.history(work)
    lib.delete_progress_event(events[0]["id"])
    assert lib.active_run(work)["current_progress"] == 270
    lib.delete_progress_event(events[1]["id"])
    assert lib.active_run(work)["current_progress"] == 200
    lib.delete_progress_event(events[2]["id"])
    assert lib.active_run(work)["current_progress"] == 0


def test_sqlite_backup_and_daily_retention(lib, tmp_path):
    lib.add_work({"title": "The Way of Kings"})
    folder = tmp_path / "backups"
    first = automatic_backup(lib, destination=folder, keep=3)
    assert first is not None and first.exists()
    assert automatic_backup(lib, destination=folder) is None
    snapshot = Library(first)
    assert snapshot.integrity_check() == "ok"
    assert len(snapshot.list_works()) == 1
    snapshot.close()
    manual = lib.backup_to(tmp_path / "manual.sqlite3")
    assert manual.exists()


def test_gui_gallery_and_shelf_filter(lib):
    qt_app = QApplication.instance() or QApplication([])
    assert qt_app is not None
    work = lib.add_work({"title": "Way of Kings", "author": "Brandon Sanderson"})
    shelf = lib.create_shelf("Cosmere")
    lib.set_shelf_membership([work], shelf)
    window = MainWindow(lib)
    try:
        assert window.table.rowCount() == 1
        window.view_selector.setCurrentIndex(1)
        assert window.gallery.count() == 1
        window.gallery.setCurrentRow(0)
        assert window.selected_ids() == [work]
        window.shelf_filter.setCurrentIndex(1)
        assert window.gallery.count() == 1
    finally:
        window.close()


def test_merge_does_not_replace_newer_personal_data(lib):
    original = lib.add_work({
        "title": "Warbreaker", "author": "Brandon Sanderson",
        "status": "reading", "notes": "My latest notes",
        "rating": 4.5, "favorite": True,
    })
    imported = lib.export_data()
    imported["works"][0]["notes"] = "OLD notes"
    imported["works"][0]["status"] = "completed"
    imported["works"][0]["rating"] = 2.0
    imported["works"][0]["favorite"] = 0
    imported["works"][0]["description"] = "A public description"
    assert lib.import_data(imported) == 0
    current = lib.get_work(original)
    assert current["notes"] == "My latest notes"
    assert current["status"] == "reading"
    assert current["rating"] == 4.5
    assert current["favorite"] == 1
    assert current["description"] == "A public description"


def test_old_v1_backup_with_shelves_left_intact_on_merge(lib):
    book = lib.add_work({"title": "Elantris"})
    shelf = lib.create_shelf("My shelf")
    lib.set_shelf_membership([book], shelf)
    legacy = lib.export_data()
    legacy["schema_version"] = 1
    legacy.pop("shelves")
    legacy.pop("work_shelves")
    assert lib.import_data(legacy) == 0
    assert lib.shelves_for_work(book) == [shelf]
