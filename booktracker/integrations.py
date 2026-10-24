"""Portable backups, Goodreads imports, and optional online metadata lookup."""
from __future__ import annotations

import csv
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from .database import WORK_FIELDS, Library

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


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.title = ""
        self.in_title = False
        self.json_ld = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            value = attrs.get("content", "")
            if key and value:
                self.meta.setdefault(key, value)
        elif tag == "title":
            self.in_title = True
        elif tag == "script" and attrs.get("type") == "application/ld+json":
            self.json_ld.append("")

    def handle_data(self, data):
        if self.in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False


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
    """Best-effort: sites may reject requests or change their HTML at any time."""
    info = source_from_url(url)
    try:
        raw = _get(info["source_url"]).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"Could not access this story: {exc}") from exc
    parser = MetaParser()
    parser.feed(raw)
    title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title
    title = html.unescape(re.sub(r"\s+", " ", title)).strip()
    title = re.sub(r"\s*[-|]\s*(Archive of Our Own|FanFiction|Royal Road|Scribble Hub|SpaceBattles Forums).*$",
                   "", title, flags=re.IGNORECASE).strip()
    if not title:
        raise ValueError("No title could be extracted; enter this work manually.")
    info["title"] = title
    info["description"] = html.unescape(re.sub(r"<[^>]+>", "", parser.meta.get(
        "og:description") or parser.meta.get("description") or "")).strip()
    info["author"] = parser.meta.get("author", "").strip()
    image = parser.meta.get("og:image", "")
    if image.startswith("https://"):
        info["cover_url"] = image
    # AO3 pages usually provide the byline; avoid guessing if unavailable.
    if info["source_site"] == "AO3" and not info["author"]:
        match = re.search(r'<a[^>]*rel=["\']author["\'][^>]*>(.*?)</a>', raw, re.DOTALL | re.IGNORECASE)
        if match:
            info["author"] = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    if info["source_site"] == "FanFiction.net" and not info["author"]:
        match = re.search(r'<a[^>]+href=["\']/u/\d+[^>]*>(.*?)</a>', raw, re.DOTALL | re.IGNORECASE)
        if match:
            info["author"] = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    return info


def openlibrary_lookup(query: str) -> list[dict]:
    """Public Open Library search (not a Goodreads API). Returns up to 10 candidates."""
    query = query.strip()
    if not query:
        return []
    if re.fullmatch(r"[\dXx-]{10,17}", query):
        params = urllib.parse.urlencode({"isbn": query.replace("-", ""), "limit": 10})
    else:
        params = urllib.parse.urlencode({"q": query, "limit": 10})
    try:
        payload = json.loads(_get("https://openlibrary.org/search.json?" + params))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"Open Library lookup failed: {exc}") from exc
    output = []
    for doc in payload.get("docs", []):
        if not doc.get("title"):
            continue
        isbn = next(iter(doc.get("isbn") or []), "")
        cover = doc.get("cover_i")
        output.append({
            "title": doc["title"],
            "author": ", ".join(doc.get("author_name", [])[:3]),
            "kind": "book", "progress_unit": "pages",
            "total_units": doc.get("number_of_pages_median") or 0,
            "isbn": isbn,
            "source_site": "Open Library",
            "source_url": "https://openlibrary.org" + doc["key"],
            "source_id": doc["key"],
            "published_at": str(doc.get("first_publish_year") or ""),
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover}-L.jpg" if cover else "",
        })
    return output


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
