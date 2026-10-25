"""v0.6 author explorer and reading milestone correction tests.

All tests are local/offscreen and do not access external websites.
"""
from __future__ import annotations

import csv
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from booktracker.database import Library
from booktracker.gui import MainWindow, MilestoneEditor, ProgressDialog


@pytest.fixture
def library(tmp_path):
    db = Library(tmp_path / "my-books.sqlite3")
    yield db
    db.close()


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_author_summary_exact_filter_and_counts(library):
    a = library.add_work({
        "title": "The Way of Kings", "author": "Brandon Sanderson",
        "status": "completed", "rating": 5, "favorite": True,
    })
    library.add_work({
        "title": "Words of Radiance", "author": "Brandon Sanderson",
        "status": "reading", "rating": 4,
    })
    library.add_work({"title": "Elantris", "author": "Brandon Sanderson",
                      "status": "plan_to_read"})
    library.add_work({"title": "My Fanfic", "author": "FanAuthor",
                      "kind": "fanfiction", "status": "completed"})
    authors = library.author_summary()
    assert authors[0]["author"] == "Brandon Sanderson"
    assert authors[0]["total"] == 3
    assert authors[0]["completed"] == 1
    assert authors[0]["active"] == 1
    assert authors[0]["favorites"] == 1
    assert authors[0]["average_rating"] == pytest.approx(4.5)
    assert [x["title"] for x in library.list_works(
        author="Brandon Sanderson", sort="title"
    )] == ["Elantris", "The Way of Kings", "Words of Radiance"]
    assert len(library.list_works(author="FanAuthor")) == 1
    assert library.get_work(a)["favorite"] == 1


def test_backdated_events_leave_latest_progress_and_reconcile_deltas(library):
    work = library.add_work({"title": "Long LitRPG", "kind": "web_novel",
                             "progress_unit": "chapters", "status": "reading"})
    library.record_progress_on(work, 150, date="2026-10-20", note="Caught up")
    library.record_progress_on(work, 70, date="2026-07-20", note="Older milestone")
    library.record_progress_on(work, 100, date="2026-09-15", note="Before latest")
    events = list(reversed(library.history(work)))
    assert [(e["old_progress"], e["new_progress"]) for e in events] == [
        (0, 70), (70, 100), (100, 150)
    ]
    assert library.active_run(work)["current_progress"] == 150
    assert library.get_work(work)["started_at"] == "2026-07-20"
    assert library.stats()["chapters_logged"] == 150
    assert library.goal_progress(2026)["chapters"] == 150


def test_edit_milestone_repairs_following_delta(library):
    work = library.add_work({"title": "Chaptered Story", "progress_unit": "chapters"})
    library.record_progress_on(work, 25, date="2024-01-01", note="Initial")
    library.record_progress_on(work, 50, date="2024-02-01")
    first, later = reversed(library.history(work))
    library.update_progress_event(first["id"], date="2024-01-15",
                                  progress=30, note="Corrected")
    events = list(reversed(library.history(work)))
    assert events[0]["new_progress"] == 30
    assert events[0]["note"] == "Corrected"
    assert events[0]["recorded_at"] == "2024-01-15"
    assert events[1]["old_progress"] == 30
    assert events[1]["new_progress"] == 50
    assert library.active_run(work)["current_progress"] == 50
    assert library.goal_progress(2024)["chapters"] == 50
    library.update_progress_event(later["id"], progress=55)
    assert library.active_run(work)["current_progress"] == 55


def test_deleting_early_event_rethreads_following_old_progress(library):
    work = library.add_work({"title": "Serialization", "progress_unit": "chapters"})
    for day, progress in [(1, 12), (2, 30), (3, 40)]:
        library.record_progress_on(work, progress, date=f"2024-06-0{day}")
    events = list(reversed(library.history(work)))
    library.delete_progress_event(events[1]["id"])
    remaining = list(reversed(library.history(work)))
    assert [(x["old_progress"], x["new_progress"]) for x in remaining] == [
        (0, 12), (12, 40)
    ]
    assert library.active_run(work)["current_progress"] == 40
    assert library.goal_progress(2024)["chapters"] == 40


def test_history_filtered_by_note_author_and_year(library):
    work = library.add_work({"title": "The Way of Kings", "author": "Brandon Sanderson"})
    library.record_progress_on(work, 40, date="2025-03-01", note="First arc")
    library.record_progress_on(work, 120, date="2026-03-02", note="Second arc")
    assert len(library.history_filtered(search="brandon")) == 2
    assert len(library.history_filtered(search="First")) == 1
    assert len(library.history_filtered(search="Way of Kings", year=2025)) == 1
    assert len(library.history_filtered(search="%")) == 0
    assert library.history_years() == [2026, 2025]
    with pytest.raises(ValueError):
        library.history_filtered(year=1800)


def test_dates_and_progress_validate_before_changes(library):
    work = library.add_work({"title": "Percentage", "progress_unit": "percent"})
    with pytest.raises(ValueError):
        library.record_progress_on(work, 101, date="2025-01-01")
    with pytest.raises(ValueError):
        library.record_progress_on(work, 25, date="not-a-date")
    assert library.history(work) == []
    assert library.runs(work) == []


def test_history_and_author_gui_navigation(app, library):
    work = library.add_work({
        "title": "The Way of Kings", "author": "Brandon Sanderson",
        "series": "Stormlight Archive", "kind": "book",
    })
    library.record_progress_on(work, 30, date="2025-04-01", note="First")
    library.record_progress_on(work, 90, date="2026-04-01", note="Second")
    window = MainWindow(library)
    try:
        assert len(window.nav_buttons) == 9
        window.navigate(4)
        assert window.authors_table.rowCount() == 1
        assert window.authors_table.item(0, 0).text() == "Brandon Sanderson"
        window.open_author_from_table(window.authors_table.item(0, 0))
        assert window.pages.currentIndex() == 0
        assert window.author_filter.currentData() == "Brandon Sanderson"
        assert window.table.rowCount() == 1
        window.navigate(1)
        assert window.history_table.rowCount() == 2
        window.history_year.setCurrentIndex(
            window.history_year.findData(2025)
        )
        assert window.history_table.rowCount() == 1
        window.history_search.setText("First")
        assert window.history_table.rowCount() == 1
        window.history_search.setText("Second")
        assert window.history_table.rowCount() == 0
        window.history_year.setCurrentIndex(0)
        assert window.history_table.rowCount() == 1
    finally:
        window.close()


def test_progress_and_milestone_dialog_date_controls(app, library):
    book = library.add_work({"title": "Mistborn", "progress_unit": "pages"})
    dialog = ProgressDialog(None, library.get_work(book))
    assert not dialog.when.isEnabled()
    dialog.use_historical_date.setChecked(True)
    assert dialog.when.isEnabled()
    dialog.when.setDate(QDate(2024, 6, 12))
    assert dialog.when.date().toString("yyyy-MM-dd") == "2024-06-12"
    dialog.close()
    library.record_progress_on(book, 80, date="2024-06-12", note="First")
    event = library.get_progress_event(library.history(book)[0]["id"])
    editor = MilestoneEditor(None, event)
    assert editor.progress.value() == 80
    assert editor.date.date().toString("yyyy-MM-dd") == "2024-06-12"
    editor.progress.setValue(85)
    assert editor.progress.value() == 85
    editor.close()


def test_history_export_is_filtered_and_csv_formula_safe(app, library, tmp_path, monkeypatch):
    work = library.add_work({"title": "Example", "author": "Writer"})
    library.record_progress_on(work, 10, date="2025-01-01", note="=HYPERLINK(1)")
    library.record_progress_on(work, 20, date="2026-01-01", note="Normal")
    window = MainWindow(library)
    path = tmp_path / "history.csv"
    try:
        window.navigate(1)
        window.history_year.setCurrentIndex(
            window.history_year.findData(2025)
        )
        monkeypatch.setattr(
            "booktracker.gui.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(path), "CSV files (*.csv)")
        )
        window.export_history_csv()
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["Work"] == "Example"
        assert rows[0]["Note"] == "'=HYPERLINK(1)"
    finally:
        window.close()
