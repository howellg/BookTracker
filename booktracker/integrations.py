"""Portable backups, Goodreads imports, and optional online metadata lookup."""
from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from .database import WORK_FIELDS, Library
from .story_metadata import parse_story_html

USER_AGENT = "BookTracker/0.1 (personal reading-library metadata client)"
KNOWN_HOSTS = {
    "archiveofourown.org": ("AO3", "fanfiction", r"/works/(\d+)"),
    "www.archiveofourown.org": ("AO3", "fanfiction", r"/works/(\d+)"),
    "fanfiction.net": ("FanFiction.net", "fanfiction", r"/s/(\d+)"),
    "www.fanfiction.net": ("FanFiction.net", "fanfiction", r"/s/(\d+)"),
    "royalroad.com": ("Royal Road", "web_novel", r"/fiction/(\d+)"),
    "www.royalroad.com": ("Royal Road", "web_novel", r"/fiction/(\d+)"),
    "scribblehub.com": ("Scribble Hub", "web_novel", r"/series/(\d+)"),
    "www.scribblehub.com": ("Scribble Hub", "web_novel", r"/series/(\d+)"),
    "forums.spacebattles.com": ("SpaceBattles", "fanfiction", r"/threads/[^/]*?(\d+)"),
    "forums.sufficientvelocity.com": ("Sufficient Velocity", "fanfiction", r"/threads/[^/]*?(\d+)"),
}
CSV_FIELDS = ["id", *WORK_FIELDS, "progress"]


def source_from_url(url: str) -> dict:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ValueError("Only HTTPS URLs are supported for automatic imports.")
    host = (parsed.hostname or "").lower()
    if host not in KNOWN_HOSTS:
        raise ValueError("This site has no automatic importer yet. Use manual entry.")
    site, kind, pattern = KNOWN_HOSTS[host]
    match = re.search(pattern, parsed.path, flags=re.IGNORECASE)
    if not match:
        raise ValueError("Could not find a work ID in this URL.")
    return {"source_site": site, "source_id": match.group(1), "kind": kind,
            "source_url": f"https://{host}{parsed.path.rstrip('/')}"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                              "Accept": "text/html, application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Remote response is too large.")
        return data


def fetch_url_metadata(url: str) -> dict:
    """Best-effort lookup; a failed request never means the story URL is invalid."""
    info = source_from_url(url)
    try:
        raw = _get(info["source_url"]).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 525:
            detail = "The website's Cloudflare gateway reported an SSL handshake failure (HTTP 525)."
        elif exc.code in (403, 429, 520, 521, 522, 523, 524, 526):
            detail = f"The website could not serve this automated request (HTTP {exc.code})."
        else:
            raise ValueError(f"Story server returned HTTP {exc.code}.") from exc
        raise ValueError(
            detail + " Open the story in your browser, save it as HTML, "
            "then choose 'Import saved page'."
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(
            "The story website could not be reached or timed out. "
            "Open it in your browser, save the page as HTML, then use 'Import saved page'."
        ) from exc
    return parse_story_html(raw, info)


def fetch_saved_page_metadata(path: str | Path, story_url: str) -> dict:
    """Parse a browser-saved HTML document locally; never upload its contents."""
    source = source_from_url(story_url)
    path = Path(path)
    if path.stat().st_size > 8_000_000:
        raise ValueError("Saved HTML exceeds the 8 MB import limit.")
    return parse_story_html(path.read_text(encoding="utf-8-sig", errors="replace"), source)


OPEN_LIBRARY_FIELDS = (
    "key,title,author_name,first_publish_year,cover_i,isbn,number_of_pages_median,"
    "edition_count,subject,language,first_sentence,series,cover_edition_key"
)


def _openlibrary_lookup(query: str, *, mode: str, limit: int) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    if mode == "author":
        search = {"author": query}
    elif re.fullmatch(r"[\\dXx-]{10,17}", query):
        search = {"isbn": query.replace("-", "")}
    else:
        search = {"q": query}
    params = urllib.parse.urlencode({
        **search, "limit": limit, "fields": OPEN_LIBRARY_FIELDS,
    })
    try:
        payload = json.loads(_get("https://openlibrary.org/search.json?" + params))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"Open Library lookup failed: {exc}") from exc
    output = []
    for doc in payload.get("docs", []):
        if not doc.get("title") or not str(doc.get("key", "")).startswith("/works/"):
            continue
        identifiers = doc.get("isbn") or []
        cover = doc.get("cover_i")
        first_sentence = doc.get("first_sentence") or []
        if isinstance(first_sentence, str):
            first_sentence = [first_sentence]
        elif isinstance(first_sentence, dict):
            first_sentence = [first_sentence.get("value", "")]
        subjects = [
            str(v) for v in (doc.get("subject") or [])
            if isinstance(v, str) and len(v) <= 85
        ][:12]
        languages = doc.get("language") or []
        series = doc.get("series") or []
        output.append({
            "title": doc["title"],
            "author": ", ".join(doc.get("author_name") or []),
            "kind": "book", "progress_unit": "pages",
            "publication_status": "completed",
            "total_units": doc.get("number_of_pages_median") or 0,
            "edition_count": doc.get("edition_count") or 0,
            "isbn": next(iter(identifiers), ""),
            "source_site": "Open Library",
            "source_url": "https://openlibrary.org" + doc["key"],
            "source_id": doc["key"],
            "published_at": str(doc.get("first_publish_year") or ""),
            "cover_url": (
                f"https://covers.openlibrary.org/b/id/{cover}-M.jpg"
                if cover else
                (f"https://covers.openlibrary.org/b/olid/{doc['cover_edition_key']}-M.jpg?default=false"
                 if doc.get("cover_edition_key") else
                 (f"https://covers.openlibrary.org/b/isbn/{identifiers[0]}-M.jpg?default=false"
                  if identifiers else ""))
            ),
            "description": " ".join(str(x) for x in first_sentence[:2]).strip(),
            "language": languages[0] if languages else "",
            "tags": subjects,
            "series": series[0] if series else "",
        })
    return output


def openlibrary_lookup(query: str) -> list[dict]:
    """Public Open Library title/ISBN search; no Goodreads API keys needed."""
    return _openlibrary_lookup(query, mode="title", limit=12)


def openlibrary_author_lookup(author: str) -> list[dict]:
    """Browse many distinct works by author from the public catalog."""
    return _openlibrary_lookup(author, mode="author", limit=100)


def export_file(library: Library, path: str | Path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        content = json.dumps(library.export_data(), indent=2, ensure_ascii=False)
        path.write_text(content + "\n", encoding="utf-8")
    elif suffix == ".xml":
        backup = library.export_data()
        root = ET.Element("booktracker", schema_version=str(backup["schema_version"]))
        ET.SubElement(root, "exported_at").text = backup["exported_at"]
        for name in ("works", "reading_runs", "progress_events"):
            parent = ET.SubElement(root, name)
            for entry in backup[name]:
                node = ET.SubElement(parent, "item")
                for key, value in entry.items():
                    field = ET.SubElement(node, key)
                    field.text = json.dumps(value, ensure_ascii=False)
        ET.indent(root)
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    elif suffix == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for book in library.list_works():
                item = dict(book)
                item["tags"] = ", ".join(book["tags"])
                writer.writerow(item)
    else:
        raise ValueError("Supported exports are .json, .xml, and .csv.")


def import_file(library: Library, path: str | Path, *, strategy="merge") -> int:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return library.import_data(json.loads(path.read_text(encoding="utf-8-sig")),
                                   strategy=strategy)
    if suffix == ".xml":
        root = ET.parse(path).getroot()
        if root.tag != "booktracker":
            raise ValueError("Not a BookTracker XML file.")
        data = {"format": "booktracker", "schema_version": int(root.get("schema_version", "0"))}
        for name in ("works", "reading_runs", "progress_events"):
            parent = root.find(name)
            if parent is None:
                raise ValueError(f"Missing {name} in XML.")
            data[name] = []
            for node in parent.findall("item"):
                data[name].append({
                    element.tag: json.loads(element.text or "null") for element in node
                })
        return library.import_data(data, strategy=strategy)
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "title" not in reader.fieldnames:
                raise ValueError("CSV must include a title column.")
            rows = list(reader)
        # Convert the flat catalog to a validated full-backup shape so CSV imports,
        # including replacements, are atomic and use the same deduplication logic.
        works = []
        for row in rows:
            if not row.get("title", "").strip():
                continue
            item = {k: row[k] for k in WORK_FIELDS if k in row}
            if "tags" in item:
                item["tags"] = [t.strip() for t in item["tags"].split(",") if t.strip()]
            item["id"] = row.get("id") or __import__("uuid").uuid4().hex
            works.append(item)
        return library.import_data({"format": "booktracker", "schema_version": 1,
                                    "works": works, "reading_runs": [],
                                    "progress_events": []}, strategy=strategy)
    raise ValueError("Supported imports are .json, .xml, and .csv.")


def import_goodreads(library: Library, path: str | Path) -> tuple[int, int]:
    """Read the standard Goodreads 'My Books' CSV export, without Goodreads API keys."""
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows or "Title" not in rows[0]:
        raise ValueError("This does not look like a Goodreads library export.")
    created, skipped = 0, 0
    for row in rows:
        title = row.get("Title", "").strip()
        if not title:
            continue
        shelf = row.get("Exclusive Shelf", "").strip().lower()
        status = {"read": "completed", "currently-reading": "reading",
                  "to-read": "plan_to_read"}.get(shelf, "plan_to_read")
        isbn = (row.get("ISBN13") or row.get("ISBN") or "").strip(' ="')
        if isbn.upper() == "NONE":
            isbn = ""
        data = {"title": title, "author": row.get("Author", ""),
                "kind": "book", "status": status, "publication_status": "completed",
                "source_site": "Goodreads",
                "source_id": row.get("Book Id", "").strip(),
                "isbn": isbn, "progress_unit": "pages",
                "total_units": row.get("Number of Pages") or 0,
                "rating": row.get("My Rating") if row.get("My Rating") not in ("", "0", None) else None,
                "notes": row.get("My Review") or "",
                "started_at": row.get("Date Added") or "",
                "finished_at": row.get("Date Read") or "",
                "published_at": row.get("Original Publication Year") or ""}
        if data["source_id"]:
            data["source_url"] = "https://www.goodreads.com/book/show/" + data["source_id"]
        if library.find_match(data):
            skipped += 1
            continue
        try:
            library.add_work(data)
            created += 1
        except (ValueError, Exception) as exc:
            # Skip malformed Goodreads rows, but surface a clear error elsewhere.
            raise ValueError(f"Could not import {title!r}: {exc}") from exc
    return created, skipped
