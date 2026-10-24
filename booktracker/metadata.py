"""Optional public-book metadata refresh without touching personal reading fields."""
from __future__ import annotations

import json
import re
import urllib.error

from .book_search import search_book_catalogs
from .database import _book_identity
from .integrations import _get

PUBLIC_METADATA = (
    "author", "description", "isbn", "published_at", "language",
    "total_units", "cover_url", "series", "tags", "publication_status",
)


def _openlibrary_details(work_id: str) -> dict:
    if not re.fullmatch(r"/works/OL\d+W", work_id):
        raise ValueError("Invalid Open Library work identifier.")
    url = f"https://openlibrary.org{work_id}.json"
    try:
        doc = json.loads(_get(url))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ValueError(f"Open Library details unavailable: {exc}") from exc

    description = doc.get("description") or ""
    if isinstance(description, dict):
        description = description.get("value") or ""
    cover = next(iter(doc.get("covers") or []), None)
    try:
        cover = int(cover) if cover is not None else None
    except (TypeError, ValueError):
        cover = None
    subjects = [tag for tag in doc.get("subjects") or [] if isinstance(tag, str)][:20]
    return {
        "description": str(description),
        "cover_url": f"https://covers.openlibrary.org/b/id/{cover}-M.jpg" if cover and cover > 0 else "",
        "published_at": doc.get("first_publish_date") or "",
        "publication_status": "completed",
        "tags": subjects,
    }


def missing_book_metadata(work: dict) -> dict:
    """Retrieve metadata to fill blank catalog fields, never overwrite personal notes.

    For Open Library works, try its work detail endpoint first. Search other
    public catalog metadata only when useful fields remain missing.
    """
    if work.get("kind") not in ("book", "other"):
        raise ValueError("This refresh supports public traditional-book metadata.")
    title = (work.get("title") or "").strip()
    if not title:
        raise ValueError("A title is required to refresh metadata.")

    enriched = {}
    warnings = []
    if work.get("source_site") == "Open Library" and work.get("source_id", "").startswith("/works/"):
        try:
            enriched.update(_openlibrary_details(work["source_id"]))
        except ValueError as exc:
            warnings.append(str(exc))

    important = ("description", "cover_url", "total_units", "isbn", "published_at")
    if any(not (work.get(f) or enriched.get(f)) for f in important):
        response = search_book_catalogs(title)
        choices = response["results"]
        target_title = _book_identity(title)
        target_author = _book_identity(work.get("author") or "")
        matching = [
            candidate for candidate in choices
            if _book_identity(candidate.get("title", "")) == target_title and
            (not target_author or _book_identity(candidate.get("author", "")) == target_author)
        ]
        matching.sort(key=lambda item: sum(
            bool(item.get(key)) for key in important
        ), reverse=True)
        if matching:
            for field in PUBLIC_METADATA:
                if not enriched.get(field) and matching[0].get(field):
                    enriched[field] = matching[0][field]
        warnings.extend(response["errors"])

    # Fill only truly missing public fields, preserving user-entered content.
    updates = {}
    for field in PUBLIC_METADATA:
        candidate = enriched.get(field)
        missing = not work.get(field) or (
            field == "publication_status" and work.get(field) == "unknown"
        )
        if missing and candidate not in (None, "", 0, []):
            updates[field] = candidate

    return {"updates": updates, "warnings": warnings}
