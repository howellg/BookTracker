"""Offline catalog-search tests: no public API calls in CI."""
from __future__ import annotations

import json

import pytest

from booktracker.book_search import (
    _relevance,
    google_books_lookup,
    parse_google_books,
    search_book_catalogs,
)


def test_google_books_normalization():
    data = {"items": [
        {"id": "ABC123", "volumeInfo": {
            "title": "The Way of Kings",
            "subtitle": "The Stormlight Archive, Book One",
            "authors": ["Brandon Sanderson"],
            "publishedDate": "2010-08-31",
            "pageCount": 1007,
            "description": "<p>Epic <strong>fantasy</strong> novel.</p>",
            "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0765326353"},
                                    {"type": "ISBN_13", "identifier": "9780765326355"}],
            "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=ABC"},
            "language": "en", "publisher": "Tor Books",
        }},
        {"volumeInfo": {"title": "Missing ID should be ignored"}},
    ]}
    result = parse_google_books(data)
    assert len(result) == 1
    work = result[0]
    assert work["title"] == "The Way of Kings: The Stormlight Archive, Book One"
    assert work["author"] == "Brandon Sanderson"
    assert work["isbn"] == "9780765326355"
    assert work["total_units"] == 1007
    assert work["description"] == "Epic fantasy novel."
    assert work["cover_url"].startswith("https://books.google.com/")
    assert work["source_site"] == "Google Books"
    assert work["source_id"] == "ABC123"
    assert work["source_url"] == "https://books.google.com/books?id=ABC123"
    assert work["publication_status"] == "completed"


def test_search_ranking_prefer_close_title_and_editions():
    other = {"title": "Way of Kings", "author": "Other", "edition_count": 1}
    sanderson = {"title": "The Way of Kings", "author": "Brandon Sanderson", "edition_count": 90}
    unrelated = {"title": "A Completely Different Title", "author": "Someone"}
    assert _relevance("Way of Kings", sanderson) > _relevance("Way of Kings", other)
    assert _relevance("Way of Kings", other) > _relevance("Way of Kings", unrelated)


def test_catalog_search_two_sources(monkeypatch):
    first = {"title": "The Way of Kings", "author": "Brandon Sanderson",
             "source_site": "Open Library", "source_id": "/works/OL123",
             "edition_count": 90}
    second = {"title": "Way of Kings", "author": "Other",
              "source_site": "Google Books", "source_id": "ID123"}
    monkeypatch.setattr("booktracker.book_search.openlibrary_lookup",
                        lambda q: [first, first, {"title": "Totally Different", "source_site": "Open Library",
                                                  "source_id": "noise"}])
    monkeypatch.setattr("booktracker.book_search.google_books_lookup", lambda q: [second])
    result = search_book_catalogs("Way of Kings")
    assert result["errors"] == []
    assert [r["source_id"] for r in result["results"]] == ["/works/OL123", "ID123"]


def test_fallback_if_google_books_rate_limited(monkeypatch):
    monkeypatch.setattr("booktracker.book_search.openlibrary_lookup",
                        lambda q: [{"title": "Way of Kings", "source_site": "Open Library",
                                    "source_id": "OL1"}])

    def rate_limited(q):
        raise ValueError("Google Books could not be reached: HTTP Error 429")

    monkeypatch.setattr("booktracker.book_search.google_books_lookup", rate_limited)
    result = search_book_catalogs("Way of Kings")
    assert len(result["results"]) == 1
    assert "429" in result["errors"][0]


def test_fallback_if_open_library_offline(monkeypatch):
    def offline(q):
        raise ValueError("Network timeout")
    monkeypatch.setattr("booktracker.book_search.openlibrary_lookup", offline)
    monkeypatch.setattr("booktracker.book_search.google_books_lookup",
                        lambda q: [{"title": "The Way of Kings", "source_site": "Google Books",
                                    "source_id": "ID1"}])
    result = search_book_catalogs("Way of Kings")
    assert len(result["results"]) == 1
    assert len(result["errors"]) == 1


def test_empty_results_and_errors(monkeypatch):
    monkeypatch.setattr("booktracker.book_search.openlibrary_lookup", lambda q: [])
    monkeypatch.setattr("booktracker.book_search.google_books_lookup", lambda q: [])
    assert search_book_catalogs("unlikely search") == {"results": [], "errors": []}


def test_query_validation():
    with pytest.raises(ValueError, match="Enter a title"):
        search_book_catalogs("   ")
    with pytest.raises(ValueError, match="under 160"):
        search_book_catalogs("x" * 200)


def test_google_books_url_encoded_and_isbn(monkeypatch):
    requests = []

    def fake_get(url):
        requests.append(url)
        return json.dumps({"items": [
            {"id": "xyz", "volumeInfo": {"title": "Example Book",
                                        "authors": ["J. Doe"],
                                        "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0123456789"}]}}
        ]}).encode()
    monkeypatch.setattr("booktracker.book_search._get", fake_get)
    result = google_books_lookup("Way of Kings")
    assert len(result) == 1
    assert result[0]["isbn"] == "0123456789"
    assert "Way+of+Kings" in requests[0]
    assert "maxResults=12" in requests[0]
    assert "printType=books" in requests[0]


def test_local_search_cache_reuses_fresh_results(monkeypatch, tmp_path):
    from booktracker.book_search import cached_book_search

    calls = []
    expected = {"results": [
        {"title": "The Way of Kings", "source_site": "Open Library",
         "source_id": "OL-WAY"}
    ], "errors": []}

    def remote_search(query):
        calls.append(query)
        return expected

    monkeypatch.setattr("booktracker.book_search.search_book_catalogs", remote_search)
    first = cached_book_search("The Way of Kings", cache_root=tmp_path)
    second = cached_book_search("the way of kings", cache_root=tmp_path)
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1
    assert second["results"] == expected["results"]


def test_expired_search_cache_fallback_on_catalog_outage(monkeypatch, tmp_path):
    import os
    import time

    from booktracker.book_search import cached_book_search

    book = {"title": "The Way of Kings", "source_site": "Open Library", "source_id": "OL-WAY"}
    monkeypatch.setattr("booktracker.book_search.search_book_catalogs",
                        lambda query: {"results": [book], "errors": []})
    assert cached_book_search("Way of Kings", cache_root=tmp_path)["results"]
    path = next(tmp_path.glob("*.json"))
    yesterday = time.time() - 90000
    os.utime(path, (yesterday, yesterday))

    monkeypatch.setattr(
        "booktracker.book_search.search_book_catalogs",
        lambda query: {"results": [], "errors": ["Open Library: 503", "Google Books: 429"]},
    )
    result = cached_book_search("Way of Kings", cache_root=tmp_path)
    assert result["cached"] is True
    assert result["results"] == [book]
    assert any("saved search" in s for s in result["errors"])


def test_no_cache_on_empty_search(monkeypatch, tmp_path):
    from booktracker.book_search import cached_book_search

    monkeypatch.setattr("booktracker.book_search.search_book_catalogs",
                        lambda query: {"results": [], "errors": []})
    assert cached_book_search("Unknown book", cache_root=tmp_path)["results"] == []
    assert list(tmp_path.glob("*.json")) == []
