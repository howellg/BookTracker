"""BookTracker v0.8: sessions, calendar, annotations and portable backups."""
from __future__ import annotations

import csv
import json
import os
from datetime import date

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from booktracker.database import Library
from booktracker.gui import AnnotationDialog, MainWindow, ReadingSessionDialog
from booktracker.integrations import export_file, import_file


@pytest.fixture()
def lib(tmp_path):
    database = Library(tmp_path / "reading.sqlite3")
    yield database
    database.close()


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    return app


def make_work(lib, title="The Way of Kings", **kwargs):
    return lib.add_work({"title": title, "author": "Brandon Sanderson", **kwargs})


def test_sessions_durations_modes_and_crud(lib):
    key = make_work(lib)
    early = lib.add_session(
        key, read_date="2025-09-01", minutes=90, note="Read chapter one"
    )
    later = lib.add_session(
        key, read_date="2025-09-02", minutes=120, mode="listening",
        note="Audiobook on the walk",
    )
    assert lib.get_session(early)["minutes"] == 90
    assert len(lib.list_sessions(work_id=key)) == 2
    assert lib.list_sessions(mode="listening")[0]["id"] == later
    assert lib.session_totals(year=2025) == {
        "reading_minutes": 90,
        "listening_minutes": 120,
        "sessions": 2,
    }
    lib.update_session(
        early, read_date="2025-09-03", minutes=45, mode="reading", note="Correction"
    )
    assert lib.get_session(early)["read_date"] == "2025-09-03"
    assert lib.get_session(early)["note"] == "Correction"
    assert lib.session_totals()["reading_minutes"] == 45
    lib.delete_session(later)
    assert lib.get_session(later) is None
    assert lib.session_totals()["sessions"] == 1
    assert lib.history(key) == []  # Time never fabricates progress events.


@pytest.mark.parametrize("minutes", [0, -1, 1441, "10.4", "abc", None])
def test_invalid_session_durations_are_rejected(lib, minutes):
    book = make_work(lib)
    with pytest.raises(ValueError):
        lib.add_session(book, read_date="2025-03-20", minutes=minutes)
    assert lib.list_sessions() == []


@pytest.mark.parametrize("bad_day", ["2025-02-30", "2024-13-01", "", "2024/01/01"])
def test_invalid_session_dates_are_rejected(lib, bad_day):
    book = make_work(lib)
    with pytest.raises(ValueError):
        lib.add_session(book, read_date=bad_day, minutes=30)


def test_session_invalid_book_and_mode_are_rejected(lib):
    with pytest.raises(ValueError):
        lib.add_session("missing-book", read_date="2025-01-01", minutes=30)
    book = make_work(lib)
    with pytest.raises(ValueError):
        lib.add_session(book, read_date="2025-01-01", minutes=30, mode="sleeping")


def test_daily_activity_union_and_time_tracking(lib):
    book = make_work(lib, progress_unit="pages")
    lib.record_progress_on(book, 50, date="2025-08-10")
    lib.record_progress_on(book, 75, date="2025-08-10")
    lib.add_session(book, read_date="2025-08-10", minutes=30)
    lib.add_session(book, read_date="2025-08-10", minutes=80, mode="listening")
    lib.add_session(book, read_date="2025-08-11", minutes=45)
    daily = lib.daily_activity(year=2025, month=8)
    assert list(daily) == ["2025-08-10", "2025-08-11"]
    assert daily["2025-08-10"] == {
        "date": "2025-08-10",
        "milestones": 2, "sessions": 2,
        "reading_minutes": 30, "listening_minutes": 80,
        "chapters": 0, "pages": 75,
    }
    assert daily["2025-08-11"]["sessions"] == 1
    assert lib.milestones_on("2025-08-10")[0]["new_progress"] == 75
    result = lib.monthly_activity(2025)
    assert result[7]["reading_days"] == 2
    assert result[7]["reading_minutes"] == 75
    assert result[7]["listening_minutes"] == 80
    assert result[7]["pages"] == 75
    assert result[7]["sessions"] == 3
    assert lib.goal_progress(2025)["pages"] == 75


def test_streak_respects_today_yesterday_and_gaps(lib):
    key = make_work(lib)
    for day in ("2025-06-05", "2025-06-06", "2025-06-07"):
        lib.add_session(key, read_date=day, minutes=15)
    assert lib.reading_streak(today=date(2025, 6, 7)) == 3
    assert lib.reading_streak(today=date(2025, 6, 8)) == 3
    assert lib.reading_streak(today=date(2025, 6, 9)) == 0
    lib.record_progress_on(key, 2, date="2025-06-08")
    assert lib.reading_streak(today="2025-06-09") == 4


def test_session_changes_do_not_alter_finished_book_history(lib):
    book = make_work(lib, status="completed", finished_at="2023-05-03")
    previous_runs = lib.runs(book)
    lib.add_session(book, read_date="2026-01-02", minutes=120, mode="listening")
    assert lib.get_work(book)["finished_at"] == "2023-05-03"
    assert lib.runs(book) == previous_runs
    assert lib.goal_progress(2026)["books"] == 0


def test_annotation_bookmarks_quotes_notes_and_editing(lib):
    key = make_work(lib)
    mark = lib.add_annotation(
        key, kind="bookmark", location=50, location_unit="pages"
    )
    quote = lib.add_annotation(
        key, kind="quote", body="Life before death.", location=120,
        location_unit="pages",
    )
    personal = lib.add_annotation(
        key, kind="note", body="Character growth is interesting."
    )
    assert len(lib.list_annotations(work_id=key)) == 3
    assert lib.get_annotation(mark)["location"] == 50
    assert lib.list_annotations(kind="quote")[0]["body"] == "Life before death."
    assert lib.list_annotations(search="CHARACTER")[0]["id"] == personal
    assert lib.list_annotations(search="life before")[0]["id"] == quote
    assert lib.list_annotations(search="100%") == []
    lib.update_annotation(
        quote, kind="quote", body="Journey before destination.",
        location=150, location_unit="pages",
    )
    assert lib.get_annotation(quote)["body"] == "Journey before destination."
    assert lib.get_annotation(quote)["location"] == 150
    lib.delete_annotation(mark)
    assert lib.get_annotation(mark) is None
    assert len(lib.list_annotations(work_id=key)) == 2


def test_annotation_validation_and_work_deletion_cascade(lib):
    key = make_work(lib)
    with pytest.raises(ValueError):
        lib.add_annotation(key, kind="quote", body="")
    with pytest.raises(ValueError):
        lib.add_annotation(key, kind="not-a-thing", body="Hello")
    with pytest.raises(ValueError):
        lib.add_annotation(key, kind="note", body="Hi", location=-1)
    with pytest.raises(ValueError):
        lib.add_annotation("not-a-work", kind="note", body="Hi")
    ident = lib.add_annotation(key, kind="note", body="My old note")
    lib.delete_work(key)
    assert lib.get_annotation(ident) is None


def test_sessions_follow_work_deletion(lib):
    key = make_work(lib)
    ident = lib.add_session(key, read_date="2025-01-01", minutes=30)
    lib.delete_work(key)
    assert lib.get_session(ident) is None
    assert lib.daily_activity() == {}


@pytest.mark.parametrize("extension", [".json", ".xml", ".btbackup"])
def test_v5_full_backup_preserves_sessions_and_annotations(lib, tmp_path, extension):
    key = make_work(lib)
    session = lib.add_session(
        key, read_date="2026-05-13", minutes=60, mode="listening", note="Chapter 3"
    )
    annotation = lib.add_annotation(
        key, kind="quote", body="A quotation, with punctuation.",
        location=3, location_unit="chapters"
    )
    destination = tmp_path / ("portable" + extension)
    export_file(lib, destination)
    new = Library(tmp_path / (extension.strip(".") + "-restored.db"))
    try:
        assert import_file(new, destination) == 1
        assert new.get_session(session)["minutes"] == 60
        assert new.get_annotation(annotation)["body"] == "A quotation, with punctuation."
        assert new.get_annotation(annotation)["location"] == 3
        assert import_file(new, destination) == 0
        assert len(new.list_sessions()) == 1
        assert len(new.list_annotations()) == 1
        new.update_annotation(
            annotation, kind="quote", body="Updated locally", location=None
        )
        assert import_file(new, destination) == 0
        assert new.get_annotation(annotation)["body"] == "Updated locally"
        assert import_file(new, destination, strategy="replace") == 1
        assert new.get_annotation(annotation)["body"] == "A quotation, with punctuation."
    finally:
        new.close()


def test_backup_v4_compatibility(lib, tmp_path):
    key = make_work(lib)
    lib.add_session(key, read_date="2026-01-02", minutes=25)
    payload = lib.export_data()
    payload["schema_version"] = 4
    payload.pop("reading_sessions")
    payload.pop("annotations")
    dest = Library(tmp_path / "legacy.db")
    try:
        assert dest.import_data(payload) == 1
        assert dest.list_sessions() == []
        assert dest.list_annotations() == []
    finally:
        dest.close()


def test_invalid_v5_import_rolls_back_all_records(lib, tmp_path):
    key = make_work(lib)
    snapshot = lib.export_data()
    payload = json.loads(json.dumps(snapshot))
    payload["reading_sessions"] = [
        {"id": "fake", "work_id": key, "read_date": "2025-03-01",
         "minutes": 9999, "mode": "reading"}
    ]
    payload["annotations"] = []
    with pytest.raises(ValueError):
        lib.import_data(payload, strategy="replace")
    assert lib.get_work(key) is not None
    assert lib.list_sessions() == []


def test_gui_calendar_and_annotations_screen(app, lib):
    key = make_work(lib, progress_unit="pages")
    session = lib.add_session(
        key, read_date="2025-04-01", minutes=40, note="Morning"
    )
    note = lib.add_annotation(
        key, kind="quote", body="Always another secret.",
        location=123, location_unit="pages"
    )
    lib.record_progress_on(key, 100, date="2025-04-01")
    window = MainWindow(lib)
    try:
        assert len(window.nav_buttons) == 12
        window.navigate(7)
        window.calendar.setSelectedDate(QDate(2025, 4, 1))
        assert window.calendar_table.rowCount() == 2
        assert "40 min reading" in window.calendar_day_summary.text()
        assert window.calendar_month_summary.text().startswith("1 active")
        window.calendar_table.selectRow(0)
        assert window.selected_calendar_record()[1] == session
        window.navigate(8)
        assert window.journal_table.rowCount() == 1
        window.journal_table.selectRow(0)
        assert window.selected_annotation_id() == note
        assert window.annotation_detail.toPlainText() == "Always another secret."
        window.open_annotation_book()
        assert window.pages.currentIndex() == 0
        assert window.selected_work()["id"] == key
        window.show_work_annotations()
        assert window.pages.currentIndex() == 8
        assert window.journal_work.currentData() == key
    finally:
        window.close()


def test_gui_dialogs_durations_and_positions(app, lib):
    key = make_work(lib, progress_unit="pages")
    dialog = ReadingSessionDialog(None, lib, work_id=key, day="2025-02-10")
    assert dialog.book.currentData() == key
    assert dialog.date.date().toString("yyyy-MM-dd") == "2025-02-10"
    dialog.minutes.setValue(95)
    dialog.mode.setCurrentIndex(1)
    assert dialog.values()["minutes"] == 95
    assert dialog.values()["mode"] == "listening"
    dialog.close()
    note = AnnotationDialog(None, lib, work_id=key)
    assert note.book.currentData() == key
    note.has_location.setChecked(True)
    note.unit.setCurrentIndex(note.unit.findData("pages"))
    note.location.setValue(19)
    assert note.values()["location"] == 19
    assert note.values()["location_unit"] == "pages"
    note.close()


def test_gui_exports_sessions_csv_and_notes_md(app, lib, tmp_path, monkeypatch):
    key = make_work(lib, title="=Danger")
    lib.add_session(
        key, read_date="2025-01-03", minutes=44, note="=HYPERLINK(A1)"
    )
    lib.add_annotation(
        key, kind="note", body="Reflections on the first chapter",
        location=1, location_unit="chapters"
    )
    window = MainWindow(lib)
    paths = [
        (str(tmp_path / "times.csv"), "CSV"),
        (str(tmp_path / "quotes.md"), "Markdown"),
    ]
    monkeypatch.setattr(
        "booktracker.gui.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: paths.pop(0),
    )
    try:
        window.export_time_log()
        window.export_annotations_md()
        with (tmp_path / "times.csv").open(
            encoding="utf-8-sig", newline=""
        ) as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["Book"] == "'=Danger"
        assert rows[0]["Note"] == "'=HYPERLINK(A1)"
        exported = (tmp_path / "quotes.md").read_text()
        assert "Reflections on the first chapter" in exported
        assert "Chapter 1" in exported
    finally:
        window.close()


def test_backup_merge_remaps_sessions_and_notes_to_matching_existing_book(tmp_path):
    source = Library(tmp_path / "source.db")
    original = source.add_work({
        "title": "Original Name", "author": "Writer", "isbn": "9781234567890"
    })
    session = source.add_session(
        original, read_date="2025-06-01", minutes=45, note="Old device"
    )
    annotation = source.add_annotation(
        original, kind="note", body="Private observation"
    )
    portable = tmp_path / "merged.btbackup"
    export_file(source, portable)
    source.close()
    dest = Library(tmp_path / "destination.db")
    try:
        local = dest.add_work({
            "title": "Corrected Name", "author": "Writer",
            "isbn": "9781234567890", "notes": "Do not overwrite my notes"
        })
        assert import_file(dest, portable) == 0
        assert len(dest.list_works()) == 1
        assert dest.get_session(session)["work_id"] == local
        assert dest.get_annotation(annotation)["work_id"] == local
        assert dest.get_work(local)["notes"] == "Do not overwrite my notes"
    finally:
        dest.close()


def test_utc_event_timestamp_converts_to_central_calendar_day(lib):
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("Local timezone override requires time.tzset")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Chicago"
    time.tzset()
    try:
        key = make_work(lib, progress_unit="pages")
        # 1 January UTC is still 31 December the evening before in Chicago.
        lib.record_progress_on(
            key, 35, date="2026-01-01T01:00:00+00:00"
        )
        assert lib.activity_day("2026-01-01T01:00:00+00:00") == "2025-12-31"
        assert lib.daily_activity(year=2025, month=12)["2025-12-31"]["pages"] == 35
        assert lib.milestones_on("2025-12-31")[0]["new_progress"] == 35
        assert lib.milestones_on("2026-01-01") == []
        assert lib.monthly_activity(2025)[11]["pages"] == 35
        assert lib.goal_progress(2025)["pages"] == 35
        assert lib.goal_progress(2026)["pages"] == 0
        assert len(lib.history_filtered(year=2025)) == 1
        assert len(lib.history_filtered(year=2026)) == 0
        assert lib.history_years() == [2025]
        lib.set_status(key, "completed", date="2026-01-01T02:00:00+00:00")
        assert lib.goal_progress(2025)["books"] == 1
        assert lib.goal_progress(2026)["books"] == 0
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_explicit_historical_dates_do_not_shift_in_timezone(lib):
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("Local timezone override requires time.tzset")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Chicago"
    time.tzset()
    try:
        key = make_work(lib, progress_unit="chapters")
        lib.record_progress_on(key, 10, date="2026-01-01")
        assert lib.activity_day("2026-01-01") == "2026-01-01"
        assert "2026-01-01" in lib.daily_activity(year=2026, month=1)
        assert lib.history_years() == [2026]
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_import_fails_on_orphan_annotation_without_deleting_original(lib):
    key = make_work(lib)
    backup = lib.export_data()
    backup["annotations"] = [{
        "id": "madeup-id", "work_id": "unknown-id", "kind": "bookmark",
        "body": "", "location": None, "location_unit": ""
    }]
    with pytest.raises(ValueError, match="Orphaned book annotation"):
        lib.import_data(backup, strategy="replace")
    assert lib.get_work(key) is not None


def test_calendar_ui_reflects_reading_and_listening_minutes(app, lib):
    book = make_work(lib)
    lib.add_session(book, read_date="2025-12-01", minutes=60)
    lib.add_session(
        book, read_date="2025-12-01", minutes=50, mode="listening"
    )
    lib.add_session(book, read_date="2025-12-02", minutes=30)
    window = MainWindow(lib)
    try:
        window.navigate(7)
        window.calendar.setSelectedDate(QDate(2025, 12, 1))
        assert window.calendar_table.rowCount() == 2
        assert "60 min reading" in window.calendar_day_summary.text()
        assert "50 min listening" in window.calendar_day_summary.text()
        assert "2 active days" in window.calendar_month_summary.text()
        window.calendar.setSelectedDate(QDate(2025, 12, 2))
        assert window.calendar_table.rowCount() == 1
    finally:
        window.close()


def test_upgrade_from_v4_database_preserves_books_and_saved_views(tmp_path):
    import sqlite3

    path = tmp_path / "v4-library.sqlite3"
    original = Library(path)
    key = original.add_work({
        "title": "Older Book", "author": "Existing Writer",
        "status": "reading", "notes": "Do not lose this.",
    })
    view = original.save_view("Previous view", {"status": "reading"})
    original.close()

    # Simulate a real pre-0.8 schema with the two new tables absent.
    with sqlite3.connect(path) as old:
        old.execute("DROP TABLE annotations")
        old.execute("DROP TABLE reading_sessions")
        old.execute("PRAGMA user_version=4")

    reopened = Library(path)
    try:
        assert reopened.db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert reopened.get_work(key)["notes"] == "Do not lose this."
        assert reopened.list_views()[0]["id"] == view
        reopened.add_session(
            key, read_date="2026-01-01", minutes=30
        )
        reopened.add_annotation(
            key, kind="note", body="New annotation after migration"
        )
        assert len(reopened.list_annotations(work_id=key)) == 1
    finally:
        reopened.close()


def test_new_annotation_has_editable_position_and_correct_book_unit(app, lib):
    book = make_work(lib, progress_unit="chapters")
    lib.record_progress_on(book, 19, date="2026-09-02")
    dialog = AnnotationDialog(None, lib, work_id=book)
    try:
        assert dialog.has_location.isChecked()
        assert dialog.location.isEnabled()
        assert dialog.unit.isEnabled()
        assert dialog.location.value() == 19
        assert dialog.unit.currentData() == "chapters"
        dialog.kind.setCurrentIndex(dialog.kind.findData("quote"))
        dialog.body.setPlainText("This is a test.")
        dialog.location.setValue(27)
        assert dialog.values() == {
            "kind": "quote", "location": 27, "location_unit": "chapters",
            "body": "This is a test.",
        }
    finally:
        dialog.close()


def test_annotation_position_can_be_omitted_and_reenabled(app, lib):
    book = make_work(lib, progress_unit="pages")
    dialog = AnnotationDialog(None, lib, work_id=book)
    try:
        assert dialog.location.isEnabled() and dialog.unit.isEnabled()
        assert dialog.unit.currentData() == "pages"
        dialog.has_location.setChecked(False)
        assert not dialog.location.isEnabled() and not dialog.unit.isEnabled()
        assert dialog.values()["location"] is None
        assert dialog.values()["location_unit"] == ""
        dialog.has_location.setChecked(True)
        assert dialog.location.isEnabled() and dialog.unit.isEnabled()
        dialog.location.setValue(85)
        assert dialog.values()["location"] == 85
        assert dialog.values()["location_unit"] == "pages"
    finally:
        dialog.close()


def test_edit_annotation_without_position_stays_optional(app, lib):
    book = make_work(lib, progress_unit="pages")
    record = lib.add_annotation(book, kind="note", body="No location known.")
    current = lib.get_annotation(record)
    dialog = AnnotationDialog(None, lib, existing=current)
    try:
        assert not dialog.has_location.isChecked()
        assert not dialog.location.isEnabled()
        assert not dialog.unit.isEnabled()
        assert dialog.values()["location"] is None
        dialog.has_location.setChecked(True)
        dialog.location.setValue(84)
        lib.update_annotation(record, **dialog.values())
        assert lib.get_annotation(record)["location"] == 84
        assert lib.get_annotation(record)["location_unit"] == "pages"
    finally:
        dialog.close()


def test_new_annotation_changes_default_unit_when_book_changes(app, lib):
    chapters = make_work(lib, title="Chapter Work", progress_unit="chapters")
    pages = make_work(lib, title="Page Work", progress_unit="pages")
    dialog = AnnotationDialog(None, lib, work_id=chapters)
    try:
        assert dialog.unit.currentData() == "chapters"
        index = dialog.book.findData(pages)
        assert index >= 0
        dialog.book.setCurrentIndex(index)
        assert dialog.unit.currentData() == "pages"
        assert dialog.location.isEnabled()
        assert dialog.unit.isEnabled()
        assert dialog.location.value() == 1
    finally:
        dialog.close()


def test_percent_book_location_defaults_without_exceeding_bounds(app, lib):
    book = make_work(lib, title="Progress Percent", progress_unit="percent")
    lib.record_progress_on(book, 66, date="2026-09-02")
    dialog = AnnotationDialog(None, lib, work_id=book)
    try:
        assert dialog.unit.currentData() == "percent"
        assert dialog.location.value() == 66
        assert dialog.location.isEnabled()
        assert dialog.values()["location"] == 66
    finally:
        dialog.close()
