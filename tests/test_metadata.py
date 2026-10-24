"""Metadata refresh adds only missing public information."""
from __future__ import annotations

import json

from booktracker.metadata import missing_book_metadata


def test_openlibrary_work_details_no_overwrite_personal_data(monkeypatch):
    book = {
        "id": "abc", "title": "The Way of Kings", "author": "Brandon Sanderson",
        "kind": "book", "source_site": "Open Library", "source_id": "/works/OL153W",
        "description": "", "cover_url": "", "published_at": "",
        "tags": ["Already added tag"], "total_units": 1007, "isbn": "1234567890",
        "rating": 5.0, "notes": "Already read this", "status": "completed",
    }
    calls = []
    def mocked_get(url):
        calls.append(url)
        return json.dumps({
            "description": {"type": "/type/text", "value": "Epic fantasy!"},
            "subjects": ["Adventure", "Magic"], "covers": [1234],
            "first_publish_date": "2010",
        }).encode()
    monkeypatch.setattr("booktracker.metadata._get", mocked_get)
    monkeypatch.setattr("booktracker.metadata.search_book_catalogs",
                        lambda title: {"results": [], "errors": []})
    result = missing_book_metadata(book)
    assert result["updates"]["description"] == "Epic fantasy!"
    assert result["updates"]["cover_url"].endswith("1234-M.jpg")
    assert result["updates"]["published_at"] == "2010"
    assert "tags" not in result["updates"]
    assert "rating" not in result["updates"]
    assert "notes" not in result["updates"]
    assert calls == ["https://openlibrary.org/works/OL153W.json"]


def test_refresh_not_for_fanfiction():
    import pytest
    with pytest.raises(ValueError, match="traditional-book"):
        missing_book_metadata({"title": "Story", "kind": "fanfiction"})
