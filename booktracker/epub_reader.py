"""Secure, offline EPUB spine, text and image extraction for the built-in reader.

The EPUB is never extracted to disk. Every ZIP member path is normalized and
bounded; remote resources, active HTML, inline styling, and scriptable URI
schemes are omitted before rendering in QTextBrowser.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .epub_import import (
    CONTAINER_NS,
    MAX_EPUB_BYTES,
    MAX_ZIP_MEMBERS,
    OPF_NS,
    _read_small,
    _safe_member,
)

MAX_SECTION_BYTES = 3_000_000
MAX_IMAGE_BYTES = 3_000_000
MAX_TOTAL_BYTES = 450_000_000
MAX_SPINE = 5000
MAX_IMAGES_PER_SECTION = 24
ALLOWED_TAGS = frozenset({
    "p","div","span","br","hr","h1","h2","h3","h4","h5","h6",
    "em","strong","b","i","u","s","blockquote","pre","code",
    "ol","ul","li","dl","dt","dd","sub","sup","table","thead",
    "tbody","tr","td","th","img","a",
})
DROP_TAGS = frozenset({
    "script","style","iframe","frame","frameset","object","embed","form",
    "input","button","select","textarea","video","audio","source","svg",
    "math","canvas","link","meta",
})
IMAGE_MIME = frozenset({"image/jpeg","image/png","image/gif","image/webp"})


def member_path(parent: str, name: str) -> str:
    """Join EPUB-internal relative references; reject path and URL escapes."""
    candidate = unquote(str(name or ""))
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise ValueError("Unsafe EPUB resource reference.")
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or candidate.startswith(("/", "//")):
        raise ValueError("External or absolute resource references are blocked.")
    joined = _safe_member(posixpath.join(posixpath.dirname(parent), parsed.path))
    if joined.startswith("..") or joined == ".":
        raise ValueError("EPUB path escapes its archive.")
    return joined


@dataclass(frozen=True)
class SpineItem:
    href: str
    title: str
    ordinal: int


class EpubBook:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve(strict=True)
        if self.path.suffix.casefold() != ".epub" or not self.path.is_file():
            raise ValueError("Choose an existing EPUB file.")
        if self.path.stat().st_size > MAX_EPUB_BYTES:
            raise ValueError("EPUB exceeds the supported 250 MB file limit.")
        with zipfile.ZipFile(self.path) as archive:
            if len(archive.infolist()) > MAX_ZIP_MEMBERS:
                raise ValueError("EPUB has too many internal files.")
            total = 0
            names=set()
            for info in archive.infolist():
                if info.filename in names:
                    raise ValueError("EPUB has duplicate internal file paths.")
                names.add(info.filename)
                _safe_member(info.filename)
                if info.file_size < 0:
                    raise ValueError("Invalid EPUB member size.")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("EPUB expands beyond the safety limit.")
            root = ET.fromstring(_read_small(archive, "META-INF/container.xml"))
            ref = root.find(".//ocf:rootfile", CONTAINER_NS)
            if ref is None or not ref.get("full-path"):
                raise ValueError("EPUB has no OPF document.")
            self.opf = _safe_member(ref.get("full-path"))
            package = ET.fromstring(_read_small(archive, self.opf))
            metadata = package.find("opf:metadata", OPF_NS)
            title_node = metadata.find("dc:title", OPF_NS) if metadata is not None else None
            self.title = (title_node.text or "").strip() if title_node is not None else ""
            manifest = package.find("opf:manifest", OPF_NS)
            spine = package.find("opf:spine", OPF_NS)
            if manifest is None or spine is None:
                raise ValueError("EPUB is missing its manifest or spine.")
            items = {}
            for item in manifest.findall("opf:item", OPF_NS):
                uid, href = item.get("id"), item.get("href")
                if uid and href:
                    try:
                        internal = member_path(self.opf, href)
                    except ValueError:
                        continue
                    items[uid] = {
                        "href": internal, "media_type": item.get("media-type") or "",
                        "properties": item.get("properties") or "",
                    }
            ordered = []
            for itemref in spine.findall("opf:itemref", OPF_NS):
                if itemref.get("linear") == "no":
                    continue
                item = items.get(itemref.get("idref") or "")
                if not item or item["media_type"] not in (
                    "application/xhtml+xml", "text/html"
                ):
                    continue
                if item["href"] not in archive.namelist():
                    continue
                ordered.append(SpineItem(item["href"], f"Section {len(ordered)+1}",
                                         len(ordered)))
                if len(ordered) > MAX_SPINE:
                    raise ValueError("EPUB has too many reading sections.")
            if not ordered:
                raise ValueError("EPUB has no readable spine sections.")
            self.spine = ordered
            self._manifest = {v["href"]: v for v in items.values()}
            self._titles = self._table_of_contents(archive,package,items)
        self.spine = [
            SpineItem(item.href,self._titles.get(i,item.title),item.ordinal)
            for i,item in enumerate(self.spine)
        ]

    def _table_of_contents(self,archive,package,items):
        """Read EPUB3 nav or EPUB2 NCX labels; never visit external links."""
        titles={}
        paths={item.href:i for i,item in enumerate(self.spine)}
        navigation=next(
            (item for item in items.values()
             if "nav" in item["properties"].split()),None
        )
        if navigation and navigation["href"] in archive.namelist():
            try:
                contents=BeautifulSoup(
                    _read_small(archive,navigation["href"]),"html.parser"
                )
                navigations=contents.find_all("nav")
                toc_nav=next(
                    (nav for nav in navigations
                     if "toc" in str(nav.attrs.get("epub:type") or
                                     nav.attrs.get("type") or "").split()),
                    navigations[0] if navigations else None
                )
                if toc_nav:
                    for link in toc_nav.find_all("a",href=True)[:MAX_SPINE*3]:
                        try:
                            href=member_path(navigation["href"],link["href"])
                        except ValueError:
                            continue
                        index=paths.get(href)
                        label=link.get_text(" ",strip=True)[:140]
                        if index is not None and label:
                            titles.setdefault(index,label)
            except (KeyError,ValueError):
                pass
        if not titles:
            spine=package.find("opf:spine",OPF_NS)
            ncx_id=spine.get("toc") if spine is not None else None
            ncx=items.get(ncx_id or "") if ncx_id else None
            if ncx and ncx["href"] in archive.namelist():
                try:
                    contents=ET.fromstring(_read_small(archive,ncx["href"]))
                    ns={"n":"http://www.daisy.org/z3986/2005/ncx/"}
                    for point in contents.findall(".//n:navPoint",ns)[:MAX_SPINE*3]:
                        node=point.find("n:content",ns)
                        text_node=point.find("n:navLabel/n:text",ns)
                        if node is None or text_node is None:
                            continue
                        try:
                            href=member_path(ncx["href"],node.get("src") or "")
                        except ValueError:
                            continue
                        index=paths.get(href)
                        label=(text_node.text or "").strip()[:140]
                        if index is not None and label:
                            titles.setdefault(index,label)
                except (ET.ParseError,KeyError,ValueError):
                    pass
        return titles

    def __len__(self):
        return len(self.spine)

    def _read(self, href: str, limit=MAX_SECTION_BYTES) -> bytes:
        with zipfile.ZipFile(self.path) as archive:
            return _read_small(archive, href, maximum=limit)

    def section(self, index: int) -> dict:
        if not 0 <= index < len(self.spine):
            raise IndexError("EPUB section outside the spine.")
        entry = self.spine[index]
        soup = BeautifulSoup(self._read(entry.href), "html.parser")
        for element in tuple(soup.find_all(DROP_TAGS)):
            element.decompose()
        body = soup.body or soup
        # Prevent hidden SVG/XML constructs, JavaScript / file URIs and
        # arbitrary inline styles from reaching the Qt HTML widget.
        image_paths = {}
        counter = 0
        for tag in list(body.find_all(True)):
            if tag.name not in ALLOWED_TAGS:
                tag.unwrap()
                continue
            old = dict(tag.attrs)
            tag.attrs.clear()
            if tag.name == "img" and counter < MAX_IMAGES_PER_SECTION:
                source = old.get("src") or ""
                try:
                    internal = member_path(entry.href, source)
                    info = self._manifest.get(internal)
                    if not info or info["media_type"] not in IMAGE_MIME:
                        raise ValueError("Unsupported image.")
                    token = f"epub-image:///{index}/{counter}"
                    tag.attrs["src"] = token
                    tag.attrs["alt"] = str(old.get("alt") or "")[:160]
                    image_paths[token] = internal
                    counter += 1
                except (ValueError, TypeError):
                    tag.decompose()
            elif tag.name == "img":
                tag.decompose()
            elif tag.name == "a":
                # QTextBrowser remains offline and cannot open external links.
                # In-section anchors are displayed as text, not followed.
                pass
        heading = body.find(re.compile(r"^h[1-6]$"))
        label = heading.get_text(" ", strip=True)[:140] if heading else ""
        title = (label or self._titles.get(index) or f"Section {index+1}").strip()
        self._titles[index] = title
        return {"html": str(body), "title": title, "images": image_paths,
                "text": body.get_text(" ", strip=True)[:200000]}

    def image(self, href: str) -> bytes:
        if href not in self._manifest:
            raise ValueError("Image isn't part of this EPUB.")
        if self._manifest[href]["media_type"] not in IMAGE_MIME:
            raise ValueError("Unsupported embedded image.")
        return self._read(href, MAX_IMAGE_BYTES)

    def toc(self):
        return [
            self._titles.get(i) or item.title for i,item in enumerate(self.spine)
        ]
