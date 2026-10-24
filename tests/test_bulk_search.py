"""Author-mode catalog search and richer normalized metadata."""
from __future__ import annotations

import json

from booktracker.book_search import cached_book_search, search_book_catalogs
from booktracker.integrations import openlibrary_author_lookup


def test_author_search_returns_many_results(monkeypatch):
    books = [
        {"title": f"Story {i:03}", "author": "Brandon Sanderson", "source_site": "Open Library",
         "source_id": f"/works/OL{i}W", "edition_count": 3}
        for i in range(72)
    ]
    books.append({"title": "Not by Sanderson", "author": "Other Writer",
                  "source_site": "Open Library", "source_id": "OL999W"})
    monkeypatch.setattr("booktracker.book_search.openlibrary_author_lookup", lambda q: books)
    monkeypatch.setattr("booktracker.book_search.google_books_author_lookup", lambda q: [])
    response = search_book_catalogs("Brandon Sanderson", mode="author")
    assert len(response["results"]) == 72
    assert response["results"][0]["title"] == "Story 000"
    assert response["results"][-1]["title"] == "Story 071"
    assert not response["errors"]


def test_author_search_one_provider_fails(monkeypatch):
    monkeypatch.setattr("booktracker.book_search.openlibrary_author_lookup",
                        lambda q: [{"title": "The Final Empire", "author": "Brandon Sanderson",
                                    "source_site": "Open Library", "source_id": "OL123W"}])
    def fail(_q):
        raise ValueError("429 Too Many Requests")
    monkeypatch.setattr("booktracker.book_search.google_books_author_lookup", fail)
    result = search_book_catalogs("Brandon Sanderson", mode="author")
    assert result["results"][0]["title"] == "The Final Empire"
    assert "429" in result["errors"][0]


def test_mode_cache_keys_distinct(monkeypatch, tmp_path):
    from booktracker import book_search
    calls = []

    def search(query, *, mode="title"):
        calls.append(mode)
        return {"results": [{"title": "A Book", "source_site": "Open Library",
                              "source_id": "OL1W", "author": "Brandon Sanderson"}], "errors": []}

    monkeypatch.setattr(book_search, "search_book_catalogs", search)
    assert cached_book_search("Brandon Sanderson", cache_root=tmp_path)["cached"] is False
    assert cached_book_search("Brandon Sanderson", mode="author", cache_root=tmp_path)["cached"] is False
    assert cached_book_search("Brandon Sanderson", mode="author", cache_root=tmp_path)["cached"] is True
    assert calls == ["title", "author"]


def test_openlibrary_author_query_and_metadata(monkeypatch):
    calls = []
    def mocked_get(url):
        calls.append(url)
        return json.dumps({"docs": [
            {"key": "/works/OL99W", "title": "The Way of Kings",
             "author_name": ["Brandon Sanderson"], "cover_i": 9876,
             "isbn": ["9781234567890"], "number_of_pages_median": 1007,
             "first_publish_year": 2010, "subject": ["Epic fantasy", "Magic"],
             "first_sentence": ["It was dark."], "language": ["eng"],
             "edition_count": 78},
        ]}).encode()

    monkeypatch.setattr("booktracker.integrations._get", mocked_get)
    results = openlibrary_author_lookup("Brandon Sanderson")
    assert len(results) == 1
    assert results[0]["total_units"] == 1007
    assert results[0]["tags"] == ["Epic fantasy", "Magic"]
    assert results[0]["published_at"] == "2010"
    assert results[0]["description"] == "It was dark."
    assert results[0]["cover_url"] == "https://covers.openlibrary.org/b/id/9876-M.jpg"
    assert "author=Brandon+Sanderson" in calls[0]
    assert "limit=100" in calls[0]


def test_openlibrary_no_cover_i_uses_isbn(monkeypatch):
    def mock_get(_url):
        return json.dumps({"docs": [{
            "key": "/works/OL1W", "title": "Book", "author_name": ["Someone"],
            "isbn": ["9781234567890"],
        }]}).encode()
    monkeypatch.setattr("booktracker.integrations._get", mock_get)
    assert "b/isbn/9781234567890" in openlibrary_author_lookup("Someone")[0]["cover_url"]
