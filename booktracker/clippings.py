"""Parse English Kindle My Clippings.txt exports locally and defensively.

The importer does not infer completed books, reading dates, or progress.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_CLIPPINGS_BYTES = 20_000_000
MAX_CLIPPINGS_RECORDS = 100_000
SEPARATOR = re.compile(r"(?m)^\s*={6,}\s*$")
TYPE_RE = re.compile(r"\bYour\s+(Highlight|Note)\b", re.IGNORECASE)
PAGE_RE = re.compile(r"\bpage\s+(\d+)(?:\s*[-–]\s*\d+)?", re.IGNORECASE)
LOCATION_RE = re.compile(r"\blocation\s+(\d+)(?:\s*[-–]\s*\d+)?", re.IGNORECASE)
TITLE_AUTHOR_RE = re.compile(r"^(.+?)\s+\(([^()\n]+)\)\s*$")


def read_clippings(path: str | Path) -> dict:
    """Return records and a skipped count. Never open URLs or parse HTML."""
    source = Path(path).expanduser()
    if not source.is_file() or source.suffix.lower() != ".txt":
        raise ValueError("Choose a Kindle My Clippings.txt file.")
    if source.stat().st_size > MAX_CLIPPINGS_BYTES:
        raise ValueError("Clippings file exceeds the 20 MB safety limit.")
    raw = source.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        content = raw.decode("utf-16")
    else:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("cp1252")
    records = []
    skipped = 0
    for part in SEPARATOR.split(content):
        if not part.strip():
            continue
        if len(records) + skipped >= MAX_CLIPPINGS_RECORDS:
            raise ValueError("Clippings file contains too many entries.")
        lines = part.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines()
        if len(lines) < 3:
            skipped += 1
            continue
        title_line = lines[0].strip()
        if not title_line or len(title_line) > 500:
            skipped += 1
            continue
        metadata = lines[1].strip()
        # Kindle entries have one metadata line and a separator/blank line
        # before the highlighted passage. Preserve multiline passages.
        match = TYPE_RE.search(metadata)
        text = "\n".join(lines[2:]).strip()
        if not match or not text or len(text) > 10000:
            skipped += 1
            continue
        author_match = TITLE_AUTHOR_RE.fullmatch(title_line)
        if author_match:
            title, author = author_match.group(1).strip(), author_match.group(2).strip()
        else:
            title, author = title_line, ""
        if not title or len(author) > 350:
            skipped += 1
            continue
        page = PAGE_RE.search(metadata)
        loc = LOCATION_RE.search(metadata)
        location = int(page.group(1)) if page else int(loc.group(1)) if loc else None
        unit = "pages" if page else "kindle_location" if loc else ""
        if location is not None and not 0 <= location <= 10_000_000:
            skipped += 1
            continue
        records.append({
            "title": title, "author": author,
            "kind": "quote" if match.group(1).lower() == "highlight" else "note",
            "body": text, "location": location, "location_unit": unit,
        })
    if not records:
        raise ValueError(
            "No readable Kindle highlights or notes were found. "
            "The importer currently supports English-language My Clippings exports."
        )
    return {"records": records, "skipped": skipped}


def group_clippings(records: list[dict]) -> list[dict]:
    """Group by book and author; never merge books solely on a similar prefix."""
    groups = {}
    for record in records:
        key = (record["title"].strip().casefold(), record["author"].strip().casefold())
        group = groups.setdefault(key, {
            "key": key, "title": record["title"], "author": record["author"],
            "records": [],
        })
        group["records"].append(record)
    return list(groups.values())
