"""Bounded offline full-book search for sanitized EPUB spine text."""
from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Event

from .epub_reader import EpubBook


@dataclass(frozen=True)
class SearchHit:
    section_index: int
    title: str
    occurrence: int
    excerpt: str


@dataclass(frozen=True)
class SearchReport:
    hits: tuple[SearchHit, ...]
    sections_scanned: int
    truncated: bool
    cancelled: bool
    skipped_sections: int


def search_book(
    book: EpubBook,
    query: str,
    *,
    cancel: Event | None = None,
    max_hits: int = 200,
    max_sections: int = 2000,
    max_chars: int = 10_000_000,
) -> SearchReport:
    """Search EPUB text; cap work and hits without reading external resources.

    Positions are ordinal matches within a section (not actual chapter/page
    numbers). No search data, EPUB bytes or user history are persisted.
    """
    term = query.strip()
    if len(term) < 2 or len(term) > 80:
        raise ValueError("Search for 2–80 characters.")
    if max_hits < 1 or max_sections < 1 or max_chars < 1:
        raise ValueError("Search limits must be positive.")
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    hits: list[SearchHit] = []
    scanned = skipped = processed_chars = 0
    truncated = False
    cancelled = False
    for index in range(len(book)):
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        if scanned >= max_sections or processed_chars >= max_chars:
            truncated = True
            break
        scanned += 1
        try:
            data = book.section(index)
        except (ValueError, KeyError, OSError, RuntimeError, UnicodeError):
            skipped += 1
            continue
        body = data["text"]
        remaining = max_chars - processed_chars
        if len(body) > remaining:
            body = body[:remaining]
            truncated = True
        processed_chars += len(body)
        for count_in_section, match in enumerate(pattern.finditer(body), start=1):
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if count_in_section > 30:
                truncated = True
                break
            start, end = match.span()
            excerpt = " ".join(body[max(0, start - 48):min(len(body), end + 58)].split())
            hits.append(SearchHit(index, data["title"], count_in_section, excerpt))
            if len(hits) >= max_hits:
                truncated = True
                break
        if cancelled or len(hits) >= max_hits:
            break
    return SearchReport(tuple(hits), scanned, truncated, cancelled, skipped)
