"""Bulk import is safe, atomic and leaves unknown completion dates unknown."""
from __future__ import annotations

import pytest

from booktracker.database import Library


def sample(title, source_id, *, site="Open Library", **extras):
    return {
        "title": title,
        "author": "Brandon Sanderson",
        "kind": "book", "progress_unit": "pages", "publication_status": "completed",
        "source_site": site, "source_id": source_id,
        "source_url": f"https://openlibrary.org/works/{source_id}",
        **extras,
    }


def test_bulk_add_skips_existing_and_other_editions(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    try:
        original_id = library.add_work(sample("The Way of Kings", "OL1W", status="plan_to_read"))
        items = [
            sample("Way of Kings", "G-001", site="Google Books"),  # same work, different catalog
            sample("The Final Empire", "OL2W", total_units=541, cover_url="https://covers.openlibrary.org/b/id/10-M.jpg"),
            sample("The Hero of Ages", "OL3W"),
            sample("Hero of Ages", "G-003", site="Google Books"),  # duplicate within the batch
        ]
        result = library.bulk_add_works(items, status="completed")
        assert result["added"] == 2
        assert result["skipped"] == 2
        assert len(library.list_works()) == 3
        assert library.get_work(original_id)["status"] == "plan_to_read"
        added = [library.get_work(i) for i in result["added_ids"]]
        assert all(b["status"] == "completed" for b in added)
        assert all(b["finished_at"] == "" for b in added)
        assert all(library.runs(b["id"]) == [] for b in added)
        assert any(b["total_units"] == 541 and b["cover_url"].endswith("10-M.jpg") for b in added)
        assert library.stats()["completed_works"] == 2

        again = library.bulk_add_works(items, status="completed")
        assert again["added"] == 0
        assert again["skipped"] == 4
    finally:
        library.close()


def test_batch_invalid_row_rolls_back_everything(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    try:
        with pytest.raises(ValueError):
            library.bulk_add_works([
                sample("Words of Radiance", "OL4W"),
                sample("", "OL5W"),
            ])
        assert library.list_works() == []
    finally:
        library.close()


def test_bulk_unread_keeps_plan_to_read(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    try:
        result = library.bulk_add_works([
            sample("The Way of Kings", "OL1W"),
            sample("Words of Radiance", "OL2W"),
        ], status="plan_to_read")
        assert result["added"] == 2
        assert all(b["status"] == "plan_to_read" for b in library.list_works())
    finally:
        library.close()


def test_bulk_limit(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    try:
        with pytest.raises(ValueError, match="500"):
            library.bulk_add_works([sample("Example", f"OL{i}W") for i in range(501)])
    finally:
        library.close()


def test_opt_in_updates_existing_unread_without_losing_notes_or_dates(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    try:
        work_id = library.add_work(sample(
            "The Way of Kings", "OL1W", status="plan_to_read",
            notes="I read this years ago", rating=5,
        ))
        result = library.bulk_add_works(
            [sample("Way of Kings", "Google-1", site="Google Books")],
            status="completed", update_existing_status=True,
        )
        assert result["added"] == 0
        assert result["updated"] == 1
        assert result["skipped"] == 0
        work = library.get_work(work_id)
        assert work["status"] == "completed"
        assert work["notes"] == "I read this years ago"
        assert work["rating"] == 5
        assert work["finished_at"] == ""
        assert not library.runs(work_id)
    finally:
        library.close()


def test_opt_in_does_not_change_active_reading(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    try:
        work_id = library.add_work(sample("The Way of Kings", "OL1W", status="reading"))
        library.set_progress(work_id, 100)
        result = library.bulk_add_works(
            [sample("Way of Kings", "OL2W")],
            status="completed", update_existing_status=True,
        )
        assert result["updated"] == 0
        assert result["skipped"] == 1
        assert library.get_work(work_id)["status"] == "reading"
        assert library.active_run(work_id)["current_progress"] == 100
    finally:
        library.close()
