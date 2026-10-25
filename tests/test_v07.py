"""v0.7 regressions: saved views, monthly trends, health and serial workflow."""
from __future__ import annotations

import csv
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from booktracker.database import Library
from booktracker.gui import MainWindow
from booktracker.integrations import export_file, import_file


@pytest.fixture
def lib(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    yield library
    library.close()


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def work(title, author="Brandon Sanderson", **kwargs):
    return {"title": title, "author": author, **kwargs}


def test_saved_views_create_list_delete_and_validate(lib):
    view = lib.save_view("Read Stormlight", {
        "search": "", "status": "completed", "kind": "book", "favorites": True,
        "series": "Stormlight Archive", "sort": "title",
        "quality": "missing_cover",
    })
    views = lib.list_views()
    assert len(views) == 1
    assert views[0]["id"] == view
    assert views[0]["filters"]["series"] == "Stormlight Archive"
    assert views[0]["filters"]["quality"] == "missing_cover"
    with pytest.raises(ValueError):
        lib.save_view("read stormlight", {})
    with pytest.raises(ValueError):
        lib.save_view("Broken", {"not_a_filter": "anything"})
    with pytest.raises(ValueError):
        lib.save_view("Broken", {"status": "sentient"})
    assert len(lib.list_views()) == 1
    lib.delete_view(view)
    assert lib.list_views() == []


def test_saved_views_v4_roundtrip_json_xml_portable(lib, tmp_path):
    lib.add_work(work("The Way of Kings", series="Stormlight Archive"))
    saved = lib.save_view("Stormlight", {
        "status": "all", "sort": "series", "series": "Stormlight Archive",
    })
    for ext in (".json", ".xml", ".btbackup"):
        file = tmp_path / ("library" + ext)
        export_file(lib, file)
        dest = Library(tmp_path / (ext[1:] + "-destination.db"))
        try:
            assert import_file(dest, file) == 1
            assert dest.list_views()[0]["name"] == "Stormlight"
            assert dest.list_views()[0]["id"] == saved
            assert import_file(dest, file) == 0
            assert len(dest.list_views()) == 1
        finally:
            dest.close()


def test_saved_view_import_preserves_local_same_named_view(lib, tmp_path):
    lib.save_view("Favorite stories", {"favorites": True})
    file = tmp_path / "source.json"
    export_file(lib, file)
    other = Library(tmp_path / "other.db")
    try:
        other.save_view("Favorite stories", {"favorites": False})
        assert import_file(other, file) == 0
        assert len(other.list_views()) == 1
        assert other.list_views()[0]["filters"]["favorites"] is False
    finally:
        other.close()


def test_schema_v3_backup_still_imports(lib, tmp_path):
    lib.add_work(work("Warbreaker"))
    data = lib.export_data()
    data["schema_version"] = 3
    data.pop("saved_views")
    target = Library(tmp_path / "old-format.db")
    try:
        assert target.import_data(data) == 1
        assert target.list_views() == []
    finally:
        target.close()


def test_batch_edit_extended_changes_and_preserves_runs(lib):
    one = lib.add_work(work("The Way of Kings", status="reading",
                            tags=["Old", "Keep"], rating=4))
    two = lib.add_work(work("Words of Radiance", status="completed",
                            tags=["Keep"], rating=3))
    lib.record_progress_on(one, 10, date="2026-01-03")
    assert lib.batch_edit(
        [one, two], add_tags=["Epic", "Keep"], remove_tags=["old"],
        rating=4.5, publication_status="completed", series="Stormlight Archive",
    ) == 2
    for key in (one, two):
        w = lib.get_work(key)
        assert w["rating"] == 4.5
        assert w["series"] == "Stormlight Archive"
        assert w["publication_status"] == "completed"
        assert "Old" not in w["tags"]
        assert w["tags"].count("Keep") == 1
        assert "Epic" in w["tags"]
    assert lib.active_run(one)["current_progress"] == 10
    assert lib.get_work(two)["status"] == "completed"
    assert lib.get_work(two)["finished_at"] == ""


def test_batch_edit_validation_is_transactional(lib):
    ids = [lib.add_work(work("Book 1")), lib.add_work(work("Book 2"))]
    with pytest.raises(ValueError):
        lib.batch_edit(ids, rating=5.1)
    with pytest.raises(ValueError):
        lib.batch_edit(ids, publication_status="invented")
    with pytest.raises(ValueError):
        lib.batch_edit(ids + ["unknown-id"], add_tags=["Oops"])
    assert all("Oops" not in lib.get_work(i)["tags"] for i in ids)


def test_published_chapters_and_catch_up_to_latest(lib):
    ident = lib.add_work(work(
        "My Serial", author="Writer", kind="web_novel",
        progress_unit="chapters", publication_status="ongoing", status="reading"
    ))
    lib.set_published_units(ident, 160)
    lib.record_progress_on(ident, 121, date="2026-03-01")
    assert lib.quality_counts()["serial_backlog"] == 1
    assert [x["id"] for x in lib.list_works(quality="serial_backlog")] == [ident]
    assert lib.catch_up_to_published(ident) == 39
    assert lib.active_run(ident)["current_progress"] == 160
    assert lib.get_work(ident)["status"] == "caught_up"
    assert lib.catch_up_to_published(ident) == 0
    assert lib.quality_counts()["serial_backlog"] == 0
    lib.set_published_units(ident, 180, publication_status="ongoing")
    assert lib.quality_counts()["serial_backlog"] == 1
    assert lib.catch_up_to_published(ident) == 20
    assert lib.active_run(ident)["current_progress"] == 180
    assert lib.get_work(ident)["publication_status"] == "ongoing"


def test_catch_up_requires_known_published_count(lib):
    ident = lib.add_work(work("Unknown Serial", kind="fanfiction", progress_unit="chapters"))
    with pytest.raises(ValueError, match="published"):
        lib.catch_up_to_published(ident)
    with pytest.raises(ValueError):
        lib.set_published_units(ident, -1)
    with pytest.raises(ValueError):
        lib.set_published_units(ident, 20, publication_status="nonsense")
    assert lib.get_work(ident)["total_units"] == 0


def test_quality_filter_counts_and_missing_fields(lib):
    lib.add_work(work("A", status="completed"))
    lib.add_work({"title": "B", "status": "plan_to_read", "cover_url": "local-x",
                  "author": "", "description": "Description"})
    lib.add_work(work("C", author="Writer", description="Information",
                      cover_url="https://covers.openlibrary.org/b/id/1-M.jpg",
                      source_url="https://example.org/c", series="Series"))
    stats = lib.quality_counts()
    assert stats["total"] == 3
    assert stats["missing_cover"] == 1
    assert stats["missing_author"] == 1
    assert stats["unknown_dates"] == 1
    assert len(lib.list_works(quality="missing_cover")) == 1
    assert len(lib.list_works(quality="missing_author")) == 1
    assert len(lib.list_works(quality="incomplete")) == 2
    assert len(lib.list_works(quality="unknown_dates")) == 1


def test_duplicate_candidates_never_merge_automatically(lib):
    a = lib.add_work(work("The Way of Kings", status="completed", rating=5))
    b = lib.add_work(work("Way of Kings", status="reading"))
    c = lib.add_work(work("Way of Kings", author="Different Author"))
    groups = lib.duplicate_candidates()
    assert len(groups) == 1
    assert {w["id"] for w in groups[0]} == {a, b}
    assert lib.get_work(c)["author"] == "Different Author"
    assert len(lib.list_works()) == 3


def test_global_tag_rename_merge_and_delete(lib):
    a = lib.add_work(work("Book A", tags=["Fantasy", "Epic", "Keep"]))
    b = lib.add_work(work("Book B", tags=["fantasy", "EPIC"]))
    assert lib.rename_tag("Fantasy", "Epic") == 2
    assert lib.get_work(a)["tags"] == ["Epic", "Keep"]
    assert lib.get_work(b)["tags"] == ["Epic"]
    assert lib.delete_tag("EPIC") == 2
    assert lib.get_work(a)["tags"] == ["Keep"]
    assert lib.get_work(b)["tags"] == []
    assert lib.delete_tag("Missing") == 0


def test_monthly_activity_known_dates_and_rereads(lib):
    book = lib.add_work(work("Big Book", progress_unit="pages"))
    lib.record_progress_on(book, 20, date="2025-01-01")
    lib.record_progress_on(book, 40, date="2025-01-01")
    lib.record_progress_on(book, 100, date="2025-02-05")
    serial = lib.add_work(work("Web Serial", kind="web_novel",
                               progress_unit="chapters"))
    lib.record_progress_on(serial, 15, date="2025-02-05")
    lib.record_progress_on(serial, 30, date="2025-02-08")
    lib.set_status(book, "completed", date="2025-03-01")
    activity = lib.monthly_activity(2025)
    assert len(activity) == 12
    assert activity[0]["pages"] == 40
    assert activity[0]["reading_days"] == 1
    assert activity[0]["events"] == 2
    assert activity[1]["pages"] == 60
    assert activity[1]["chapters"] == 30
    assert activity[1]["reading_days"] == 2
    assert activity[2]["completed"] == 1
    assert sum(a["completed"] for a in lib.monthly_activity(2026)) == 0


def test_library_search_notes_description_and_isbn(lib):
    lib.add_work(work("A Book", notes="Unusual theory",
                      description="A strange journey", isbn="9781234567890"))
    assert len(lib.list_works(search="theory")) == 1
    assert len(lib.list_works(search="journey")) == 1
    assert len(lib.list_works(search="978123456")) == 1
    assert lib.list_works(search="%") == []


def test_gui_saved_view_applies_and_removes(app, lib, monkeypatch):
    lib.add_work(work("One", tags=["Favorite"], status="completed"))
    lib.add_work(work("Two", status="plan_to_read"))
    window = MainWindow(lib)
    try:
        window.filter_status.setCurrentIndex(
            window.filter_status.findData("completed")
        )
        monkeypatch.setattr(
            "booktracker.gui.QInputDialog.getText",
            lambda *args, **kwargs: ("Completed books", True)
        )
        window.save_current_view()
        assert len(lib.list_views()) == 1
        window.filter_status.setCurrentIndex(0)
        assert window.table.rowCount() == 2
        index = window.saved_view_combo.findData(lib.list_views()[0]["id"])
        window.saved_view_combo.setCurrentIndex(index)
        assert window.filter_status.currentData() == "completed"
        assert window.table.rowCount() == 1
        monkeypatch.setattr(
            "booktracker.gui.QMessageBox.question",
            lambda *args, **kwargs: __import__("PySide6").QtWidgets.QMessageBox.StandardButton.Yes
        )
        window.delete_current_view()
        assert lib.list_views() == []
    finally:
        window.close()


def test_gui_serial_progress_and_health(app, lib, monkeypatch):
    ident = lib.add_work(work(
        "Updating Story", author="Writer", kind="fanfiction",
        progress_unit="chapters", status="reading", total_units=100
    ))
    lib.record_progress_on(ident, 20, date="2026-03-01")
    window = MainWindow(lib)
    try:
        window.advance_chapters(5)
        assert lib.active_run(ident)["current_progress"] == 25
        window.advance_chapters(1)
        assert lib.active_run(ident)["current_progress"] == 26
        monkeypatch.setattr(
            "booktracker.gui.QMessageBox.question",
            lambda *args, **kwargs: __import__("PySide6").QtWidgets.QMessageBox.StandardButton.Yes
        )
        window.catch_up_to_published()
        assert lib.active_run(ident)["current_progress"] == 100
        window.navigate(6)
        assert window.duplicate_table.rowCount() == 0
        assert int(window.health_counts["missing_cover"].text()) == 1
        window.open_quality_filter("missing_cover")
        assert window.pages.currentIndex() == 0
        assert window.quality_filter.currentData() == "missing_cover"
    finally:
        window.close()


def test_gui_insights_monthly_csv(app, lib, tmp_path, monkeypatch):
    book = lib.add_work(work("Pages", progress_unit="pages"))
    lib.record_progress_on(book, 25, date="2026-04-20")
    window = MainWindow(lib)
    try:
        window.navigate(5)
        window.insights_year.setValue(2026)
        assert window.monthly_table.item(3, 2).text() == "25"
        output = tmp_path / "activity.csv"
        monkeypatch.setattr(
            "booktracker.gui.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(output), "CSV")
        )
        window.export_insights_csv()
        with output.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == 12
        assert rows[3]["Pages"] == "25"
    finally:
        window.close()


def test_saved_view_shelf_remaps_across_library_merge(lib, tmp_path):
    book = lib.add_work(work("The Way of Kings"))
    source_shelf = lib.create_shelf("Cosmere")
    lib.set_shelf_membership([book], source_shelf)
    lib.save_view("Cosmere shelf", {"shelf_id": source_shelf, "sort": "title"})
    file = tmp_path / "shelf-source.json"
    export_file(lib, file)

    dest = Library(tmp_path / "merge-dest.sqlite3")
    try:
        dest_shelf = dest.create_shelf("Cosmere")
        assert dest_shelf != source_shelf
        assert import_file(dest, file) == 1
        view = dest.list_views()[0]
        assert view["filters"]["shelf_id"] == dest_shelf
        assert [entry["title"] for entry in dest.list_works(shelf_id=dest_shelf)] == [
            "The Way of Kings"
        ]
    finally:
        dest.close()


def test_insights_counts_all_events_beyond_history_display_limit(lib):
    book = lib.add_work(work("Long-running story", kind="web_novel",
                             progress_unit="chapters"))
    # Fast fixture using internal rows to exercise aggregation above 10k events
    # without thousands of round-trip transactions.
    with lib.transaction():
        run = lib._begin_run(book, "2025-01-01")
        rows = [
            (f"evt-{i}", book, run, "2025-01-05", i, i + 1, "")
            for i in range(10003)
        ]
        lib.db.executemany(
            "INSERT INTO progress_events VALUES(?,?,?,?,?,?,?)", rows
        )
        lib.db.execute(
            "UPDATE reading_runs SET current_progress=? WHERE id=?",
            (10003, run)
        )
    result = lib.monthly_activity(2025)
    assert result[0]["events"] == 10003
    assert result[0]["chapters"] == 10003
    assert result[0]["reading_days"] == 1


def test_filter_quality_incomplete_and_rating_sort(lib):
    a = lib.add_work(work("Unfinished", rating=2))
    b = lib.add_work(work("Completed", rating=5, cover_url="x",
                           description="Synopsis", source_url="https://example.org/"))
    results = lib.list_works(quality="incomplete", sort="rating")
    assert [record["id"] for record in results] == [a]
    assert lib.list_works(sort="rating")[0]["id"] == b
