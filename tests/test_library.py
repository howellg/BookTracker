"""Database and interchange regression tests; no GUI display required."""
import csv

import pytest

from booktracker.database import Library
from booktracker.integrations import (
    export_file,
    import_file,
    import_goodreads,
    source_from_url,
)


@pytest.fixture
def library(tmp_path):
    db = Library(tmp_path / "booktracker.sqlite3")
    yield db
    db.close()


def sample(**changes):
    data = {
        "title": "The Wandering Inn", "author": "pirateaba", "kind": "web_novel",
        "status": "plan_to_read", "publication_status": "ongoing",
        "source_site": "Royal Road", "source_id": "123",
        "source_url": "https://www.royalroad.com/fiction/123/example",
        "total_units": 500, "progress_unit": "chapters",
        "tags": ["LitRPG", "Fantasy"],
    }
    return {**data, **changes}


def test_add_search_edit_delete(library):
    work_id = library.add_work(sample())
    work = library.get_work(work_id)
    assert work["tags"] == ["LitRPG", "Fantasy"]
    assert len(library.list_works("wandering")) == 1
    assert len(library.list_works("pirateaba")) == 1
    assert len(library.list_works("litrpg")) == 1
    assert len(library.list_works(status="completed")) == 0
    library.update_work(work_id, {"title": "The Wandering Inn, Vol. 1", "favorite": True})
    assert len(library.list_works(favorites=True)) == 1
    library.delete_work(work_id)
    assert library.list_works() == []


def test_progress_and_caught_up(library):
    work_id = library.add_work(sample())
    library.set_progress(work_id, 120, date="2026-01-01")
    library.set_progress(work_id, 155, date="2026-01-10", note="Great arc")
    library.set_status(work_id, "caught_up")
    assert library.get_work(work_id)["status"] == "caught_up"
    assert library.active_run(work_id)["current_progress"] == 155
    assert [(e["old_progress"], e["new_progress"]) for e in library.history(work_id)] == [
        (120, 155), (0, 120)
    ]
    assert library.stats()["chapters_logged"] == 155
    library.set_status(work_id, "completed")
    assert library.active_run(work_id) is None
    assert library.stats()["completed_reads"] == 1


def test_reread_preserves_original_history(library):
    work_id = library.add_work(sample())
    library.set_progress(work_id, 500)
    library.set_status(work_id, "completed")
    library.start_reading(work_id, reread=True)
    library.set_progress(work_id, 8)
    assert library.active_run(work_id)["current_progress"] == 8
    assert len(library.runs(work_id)) == 2
    assert len(library.history(work_id)) == 2
    assert library.stats()["completed_reads"] == 1


def test_percent_limits(library):
    work_id = library.add_work(sample(progress_unit="percent"))
    with pytest.raises(ValueError):
        library.set_progress(work_id, 101)
    with pytest.raises(ValueError):
        library.set_progress(work_id, -1)
    library.set_progress(work_id, 90)
    assert library.list_works()[0]["progress"] == 90


@pytest.mark.parametrize("suffix", [".json", ".xml"])
def test_full_backup_round_trip_and_idempotent_merge(library, tmp_path, suffix):
    work_id = library.add_work(sample())
    library.set_progress(work_id, 30, date="2026-03-10")
    library.set_status(work_id, "completed", date="2026-03-12")
    library.start_reading(work_id, reread=True, date="2026-03-20")
    library.set_progress(work_id, 9, date="2026-03-22")
    backup = tmp_path / ("backup" + suffix)
    export_file(library, backup)
    restored = Library(tmp_path / ("restored" + suffix + ".sqlite3"))
    try:
        assert import_file(restored, backup) == 1
        assert restored.get_work(work_id)["title"] == "The Wandering Inn"
        assert len(restored.runs(work_id)) == 2
        assert len(restored.history(work_id)) == 2
        assert import_file(restored, backup) == 0
        assert len(restored.history(work_id)) == 2
        assert import_file(restored, backup, strategy="replace") == 1
        assert len(restored.history(work_id)) == 2
    finally:
        restored.close()


def test_bad_backup_does_not_delete_library(library):
    work_id = library.add_work(sample())
    with pytest.raises(ValueError):
        library.import_data({"format": "booktracker", "schema_version": 99}, strategy="replace")
    assert library.get_work(work_id) is not None


def test_csv_catalog(library, tmp_path):
    library.add_work(sample())
    path = tmp_path / "catalog.csv"
    export_file(library, path)
    second = Library(tmp_path / "second.sqlite3")
    try:
        assert import_file(second, path) == 1
        assert import_file(second, path) == 0
        assert second.list_works()[0]["tags"] == ["LitRPG", "Fantasy"]
    finally:
        second.close()


def test_goodreads_csv_import(library, tmp_path):
    path = tmp_path / "goodreads.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, ["Book Id", "Title", "Author", "Exclusive Shelf",
                                    "ISBN13", "Number of Pages", "My Rating", "Date Read"])
        writer.writeheader()
        writer.writerow({"Book Id": "42", "Title": "The Hobbit", "Author": "J. R. R. Tolkien",
                         "Exclusive Shelf": "read", "ISBN13": '="9781234567890"',
                         "Number of Pages": "310", "My Rating": "5",
                         "Date Read": "2026/03/01"})
    assert import_goodreads(library, path) == (1, 0)
    assert import_goodreads(library, path) == (0, 1)
    work = library.list_works()[0]
    assert work["status"] == "completed"
    assert work["isbn"] == "9781234567890"


def test_source_recognition_and_https_guard():
    assert source_from_url("https://archiveofourown.org/works/12345")["source_id"] == "12345"
    assert source_from_url("https://www.royalroad.com/fiction/90000/title")["kind"] == "web_novel"
    with pytest.raises(ValueError):
        source_from_url("http://example.com/fiction/123")
    with pytest.raises(ValueError):
        source_from_url("https://example.com/fiction/123")


def test_failed_import_rolls_back(library):
    work_id = library.add_work(sample())
    data = library.export_data()
    data["works"].append({**data["works"][0], "id": "new-id", "title": "Other",
                          "source_site": "", "source_id": "", "source_url": "", "isbn": ""})
    data["progress_events"].append({"id": "bad", "work_id": "missing", "run_id": "missing"})
    with pytest.raises(ValueError):
        library.import_data(data, strategy="replace")
    assert library.get_work(work_id) is not None
    assert len(library.list_works()) == 1
