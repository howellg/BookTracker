"""Offline EPUB metadata import. Does not extract or execute ebook contents."""
from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .integrations import source_from_url

CONTAINER_NS = {"ocf": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = {"opf": "http://www.idpf.org/2007/opf",
          "dc": "http://purl.org/dc/elements/1.1/"}
MAX_EPUB_BYTES = 250_000_000
MAX_XML_BYTES = 3_000_000
MAX_COVER_BYTES = 5_000_000
MAX_ZIP_MEMBERS = 10_000
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _safe_member(filename: str) -> str:
    name = posixpath.normpath(filename)
    if name.startswith(("../", "/")) or name in ("", ".."):
        raise ValueError("EPUB contains an unsafe internal path.")
    return name


def _read_small(archive: zipfile.ZipFile, member: str, maximum=MAX_XML_BYTES) -> bytes:
    info = archive.getinfo(_safe_member(member))
    if info.file_size > maximum:
        raise ValueError("EPUB metadata or cover is too large.")
    with archive.open(info) as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("EPUB content exceeds size limit.")
    return data


def _first(metadata: ET.Element, tag: str) -> str:
    node = metadata.find("dc:" + tag, OPF_NS)
    return (node.text or "").strip() if node is not None else ""


def _all(metadata: ET.Element, tag: str) -> list[str]:
    return list(dict.fromkeys(
        (node.text or "").strip()
        for node in metadata.findall("dc:" + tag, OPF_NS)
        if (node.text or "").strip()
    ))


def _isbn(identifiers: list[str]) -> str:
    for raw in identifiers:
        candidate = re.sub(r"[^0-9Xx]", "", raw)
        if len(candidate) in (10, 13) and re.fullmatch(r"[0-9]{13}|[0-9]{9}[0-9Xx]", candidate):
            return candidate.upper()
    return ""


def _cover_member(package: ET.Element, metadata: ET.Element, base: str) -> str | None:
    manifest = package.find("opf:manifest", OPF_NS)
    if manifest is None:
        return None
    cover_id = ""
    for meta in metadata.findall("opf:meta", OPF_NS):
        if meta.get("name", "").lower() == "cover":
            cover_id = meta.get("content") or ""
    for item in manifest.findall("opf:item", OPF_NS):
        properties = set(item.get("properties", "").split())
        href = item.get("href") or ""
        if ("cover-image" in properties or item.get("id") == cover_id
                or (not cover_id and item.get("id", "").lower() in ("cover", "cover-image"))):
            extension = Path(posixpath.basename(href.split("?", 1)[0])).suffix.lower()
            if href and extension in COVER_EXTENSIONS:
                return _safe_member(posixpath.join(base, href.split("#", 1)[0].split("?", 1)[0]))
    return None


def read_epub_metadata(path: str | Path, *, cover_dir: str | Path | None = None) -> dict:
    """Read a local EPUB's title, authors, subjects, description, IDs, and cover.

    Optional cover_dir stores a fingerprint-named cover locally. Work data
    stores a relocatable booktracker-cover token, not a machine path.
    No external requests or ebook content extraction occur.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("Choose a readable .epub file.")
    if source.stat().st_size > MAX_EPUB_BYTES:
        raise ValueError("EPUB exceeds the 250 MB metadata import limit.")
    if source.suffix.lower() != ".epub":
        raise ValueError("Only .epub files are supported.")
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.namelist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise ValueError("EPUB has too many entries.")
            container = ET.fromstring(_read_small(archive, "META-INF/container.xml"))
            rootfile = container.find(".//ocf:rootfile", CONTAINER_NS)
            if rootfile is None or not rootfile.get("full-path"):
                raise ValueError("EPUB has no OPF package.")
            opf_path = _safe_member(rootfile.get("full-path"))
            package = ET.fromstring(_read_small(archive, opf_path))
            metadata = package.find("opf:metadata", OPF_NS)
            if metadata is None:
                raise ValueError("EPUB package has no metadata.")
            title = _first(metadata, "title")
            if not title:
                raise ValueError("EPUB metadata has no book title.")
            creators = _all(metadata, "creator")
            subjects = _all(metadata, "subject")[:80]
            identifiers = _all(metadata, "identifier")
            description = BeautifulSoup(
                _first(metadata, "description"), "html.parser"
            ).get_text(" ", strip=True)
            series = ""
            source_url = _first(metadata, "source")
            for node in metadata.findall("opf:meta", OPF_NS):
                if node.get("name") == "calibre:series":
                    series = node.get("content") or ""
                if node.get("property") == "belongs-to-collection" and not series:
                    series = (node.text or "").strip()
            work = {
                "title": title, "author": ", ".join(creators), "isbn": _isbn(identifiers),
                "kind": "book", "progress_unit": "pages",
                "publication_status": "unknown", "published_at": _first(metadata, "date"),
                "description": description, "language": _first(metadata, "language"),
                "series": series, "tags": subjects,
            }
            if source_url.startswith("https://"):
                try:
                    recognized = source_from_url(source_url)
                    work.update(recognized)
                    work["progress_unit"] = "chapters"
                except ValueError:
                    # Don't include arbitrary URLs as clickable work sources.
                    pass
            cover = _cover_member(package, metadata, posixpath.dirname(opf_path))
            if cover and cover_dir:
                try:
                    data = _read_small(archive, cover, maximum=MAX_COVER_BYTES)
                    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
                    from PySide6.QtGui import QImage

                    image = QImage.fromData(data)
                    if not image.isNull() and image.width() <= 6000 and image.height() <= 6000:
                        image = image.scaled(
                            720, 1080, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        encoded = QByteArray()
                        output = QBuffer(encoded)
                        output.open(QIODevice.OpenModeFlag.WriteOnly)
                        if not image.save(output, "PNG"):
                            raise ValueError("Could not encode EPUB cover.")
                        output.close()
                        png = bytes(encoded)
                        if len(png) <= MAX_COVER_BYTES:
                            digest = hashlib.sha256(png).hexdigest()
                            filename = f"{digest}.png"
                            destination = Path(cover_dir) / filename
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            if not destination.exists():
                                destination.write_bytes(png)
                            work["cover_url"] = "booktracker-cover:" + filename
                except (KeyError, OSError, ValueError):
                    # A malformed cover must not prevent importing a valid book.
                    pass
            return work
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        raise ValueError("This EPUB has invalid or unreadable metadata.") from exc
