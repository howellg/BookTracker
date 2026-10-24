"""Online title/author/ISBN lookup across public book catalogs.

This runs in a background Qt worker, never on the GUI thread. Catalogs are
queried independently so a provider outage does not prevent other results.
No Goodreads API keys or account credentials are required.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup
from platformdirs import user_cache_dir

from .integrations import _get, openlibrary_author_lookup, openlibrary_lookup

MAX_QUERY_LENGTH = 160
MAX_PER_PROVIDER = 12


def _normalize(text: str) -> str:
    words = re.findall(r"[^\W_]+", str(text).casefold(), flags=re.UNICODE)
    if words and words[0] in ("a", "an", "the"):
        words = words[1:]
    return " ".join(words)


def _relevance(query: str, result: dict) -> float:
    sought = _normalize(query)
    title = _normalize(result.get("title", ""))
    author = _normalize(result.get("author", ""))
    isbn = re.sub(r"[^0-9X]", "", str(result.get("isbn") or "").upper())
    if not sought or not title:
        return 0
    # Popularity is a *small* tie-breaker for identical titles, useful for
    # "Way of Kings" where multiple unrelated works share similar names.
    editions = min(10, max(0, int(result.get("edition_count") or 0)) ** 0.5)
    if sought == title or sought == isbn.casefold():
        return 100 + editions
    if title.startswith(sought):
        return 90 + editions
    if sought in title:
        return 80 + editions
    if sought in author:
        return 72 + editions
    return SequenceMatcher(None, sought, title).ratio() * 70


def _google_isbn(identifiers: list[dict]) -> str:
    for kind in ("ISBN_13", "ISBN_10"):
        for entry in identifiers:
            if entry.get("type") == kind:
                value = str(entry.get("identifier", "")).strip()
                if value:
                    return value
    return ""


def _google_cover(image_links: dict) -> str:
    url = image_links.get("thumbnail") or image_links.get("smallThumbnail") or ""
    parsed = urllib.parse.urlsplit(url)
    # Google Books often returns http cover URLs. We only fetch from Google.
    if parsed.hostname in ("books.google.com", "books.googleusercontent.com"):
        return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    return ""


def parse_google_books(payload: dict) -> list[dict]:
    """Normalize Google Books volume resources into BookTracker work fields."""
    results = []
    for volume in payload.get("items", []):
        info = volume.get("volumeInfo", {})
        title = info.get("title")
        volume_id = volume.get("id")
        if not title or not volume_id:
            continue
        subtitle = info.get("subtitle") or ""
        description = info.get("description") or ""
        if "<" in description:
            description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        results.append({
            "title": str(title) + (": " + str(subtitle) if subtitle and
                                    subtitle.casefold() not in str(title).casefold() else ""),
            "author": ", ".join(info.get("authors") or []),
            "kind": "book",
            "progress_unit": "pages",
            "publication_status": "completed",
            "total_units": max(0, int(info.get("pageCount") or 0)),
            "published_at": info.get("publishedDate") or "",
            "description": unescape(description),
            "language": info.get("language") or "",
            "isbn": _google_isbn(info.get("industryIdentifiers") or []),
            "source_site": "Google Books",
            "source_id": str(volume_id),
            "source_url": "https://books.google.com/books?id=" + urllib.parse.quote(
                str(volume_id), safe=""
            ),
            "cover_url": _google_cover(info.get("imageLinks") or {}),
            "tags": [str(x) for x in (info.get("categories") or [])][:12],
            "publisher": info.get("publisher") or "",  # display-only
            "edition_label": info.get("publishedDate") or "",
        })
    return results


def google_books_lookup(query: str) -> list[dict]:
    """Free unauthenticated public Google Books search."""
    params = urllib.parse.urlencode({
        "q": query, "maxResults": MAX_PER_PROVIDER,
        "printType": "books", "orderBy": "relevance",
    })
    try:
        payload = json.loads(_get("https://www.googleapis.com/books/v1/volumes?" + params))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ValueError(f"Google Books could not be reached: {exc}") from exc
    return parse_google_books(payload)


def google_books_author_lookup(author: str) -> list[dict]:
    """Author-specific catalog query, fetching at most 40 results per request."""
    params = urllib.parse.urlencode({
        "q": f"inauthor:{author}", "maxResults": 40,
        "printType": "books", "orderBy": "relevance",
    })
    try:
        payload = json.loads(_get("https://www.googleapis.com/books/v1/volumes?" + params))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ValueError(f"Google Books author lookup failed: {exc}") from exc
    return parse_google_books(payload)


def _author_matches(author_query: str, result: dict) -> bool:
    query_tokens = set(_normalize(author_query).split())
    candidate_tokens = set(_normalize(result.get("author") or "").split())
    # Reject biographies written by other people or unrelated search results.
    return bool(query_tokens) and query_tokens.issubset(candidate_tokens)


def search_book_catalogs(query: str, *, mode: str = "title") -> dict:
    """Search both catalogs; return partial results and provider warnings.

    Result format: {"results": [...], "errors": [...]}.
    Duplicate provider IDs are omitted. Multiple editions may remain visible
    because users may care about page counts, cover art, and ISBN differences.
    """
    query = query.strip()
    if not query:
        raise ValueError("Enter a title, author, or ISBN to search.")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search terms must be under {MAX_QUERY_LENGTH} characters.")

    if mode not in ("title", "author"):
        raise ValueError("Choose Title/ISBN or Author search mode.")
    provider_calls = (
        ("Open Library", openlibrary_author_lookup if mode == "author" else openlibrary_lookup),
        ("Google Books", google_books_author_lookup if mode == "author" else google_books_lookup),
    )
    output = []
    errors = []
    # Independent calls: a failure on one source still returns results from the other.
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [(name, pool.submit(func, query)) for name, func in provider_calls]
        for provider, future in pending:
            try:
                candidates = future.result()
                for entry in candidates:
                    if isinstance(entry, dict) and entry.get("title"):
                        output.append(entry)
            except (ValueError, OSError, TimeoutError) as exc:
                errors.append(f"{provider}: {exc}")

    seen = set()
    unique = []
    for entry in output:
        key = (entry.get("source_site"), entry.get("source_id"))
        if key in seen:
            continue
        if mode == "author":
            if not _author_matches(query, entry):
                continue
        elif _relevance(query, entry) < 45:
            continue
        seen.add(key)
        unique.append(entry)
    if mode == "author":
        # Searching an author's back catalog is about browsing, not relevance:
        # prefer titled alphabetical results and retain up to 140 matches.
        unique.sort(key=lambda x: (_normalize(x["title"]), -(x.get("edition_count") or 0)))
    else:
        unique.sort(key=lambda x: _relevance(query, x), reverse=True)
    return {"results": unique[:140 if mode == "author" else 24], "errors": errors}


def cached_book_search(
    query: str, *, mode: str = "title", cache_root: str | Path | None = None,
    freshness_seconds: int = 12 * 3600
) -> dict:
    """Search with a local, bounded-lifetime cache to avoid unnecessary API traffic.

    New searches still go to the catalogs. Previously successful searches can
    be reused for 12 hours; expired entries are used only if all catalogs fail.
    This cache stores only public book metadata, never reading history.
    """
    query = query.strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        # Raise the same validation errors as the uncached search.
        return search_book_catalogs(query, mode=mode)

    if mode not in ("title", "author"):
        raise ValueError("Invalid search mode.")
    cache_dir = (
        Path(cache_root)
        if cache_root is not None
        else Path(user_cache_dir("BookTracker", "BookTracker")) / "book-search"
    )
    cache_key = hashlib.sha256(f"{mode}:{_normalize(query)}".encode()).hexdigest()
    path = cache_dir / f"{cache_key}.json"
    cached = None
    is_fresh = False

    try:
        if path.exists() and path.stat().st_size <= 1_500_000:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (record.get("schema") == 1 and isinstance(record.get("results"), list)
                    and record["results"]):
                cached = record["results"]
                is_fresh = (time.time() - path.stat().st_mtime) <= freshness_seconds
    except (OSError, ValueError, TypeError):
        cached = None

    if cached is not None and is_fresh:
        return {"results": cached, "errors": [], "cached": True}

    response = (
        search_book_catalogs(query) if mode == "title"
        else search_book_catalogs(query, mode="author")
    )
    if response["results"]:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            destination = path.with_suffix(".tmp")
            destination.write_text(
                json.dumps({"schema": 1, "results": response["results"]},
                           ensure_ascii=False), encoding="utf-8"
            )
            os.replace(destination, path)
        except OSError:
            # Search should succeed even on a read-only filesystem.
            pass
        return {**response, "cached": False}

    if cached is not None and response["errors"]:
        return {
            "results": cached,
            "errors": response["errors"] + [
                "Catalog search temporarily unavailable; showing a previously saved search."
            ],
            "cached": True,
        }
    return {**response, "cached": False}
