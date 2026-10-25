"""Parsers for public story metadata and local saved browser pages.

This module never fetches a URL; it parses HTML supplied by the user or
retrieved through optional metadata lookup. Missing metadata stays blank.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _meta(soup, *keys: str) -> str:
    for key in keys:
        found = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if found and found.get("content"):
            return str(found["content"]).strip()
    return ""


def _tags(soup, selector: str, limit: int = 80) -> list[str]:
    result = []
    for el in soup.select(selector):
        tag = _text(el)
        if tag and tag not in result and len(tag) <= 180:
            result.append(tag)
        if len(result) >= limit:
            break
    return result


def _number(text: str) -> int:
    match = re.search(r"(\d[\d,]*)", text)
    return int(match.group(1).replace(",", "")) if match else 0


def _clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    return re.sub(
        r"\s*[-|]\s*(Archive of Our Own|FanFiction(?:\.Net)?|Royal Road|"
        r"Scribble Hub|SpaceBattles Forums|Sufficient Velocity).*$",
        "", title, flags=re.IGNORECASE,
    ).strip()


def _chapter_count(soup, site: str, story_id: str) -> int:
    """Only count scoped chapter rows/links or an explicitly labeled count.

    Do not infer totals from a generic page's many navigation links, from an
    arbitrary number in a title, or from a partially loaded/paginated thread.
    Return 0 (unknown) when a page cannot supply evidence.
    """
    if site == "FanFiction.net":
        header = soup.select_one("#profile_top")
        if header:
            match = re.search(
                r"\bChapters?:?\s*([\d,]+)\b",
                header.get_text(" ", strip=True), flags=re.IGNORECASE,
            )
            if match:
                return _number(match.group(1))
        return 0
    if site == "Royal Road":
        # Royal Road exposes full public chapter lists in a chapter table;
        # prefer stable data-url attributes, then scoped chapter anchors.
        paths = set()
        for elem in soup.select(
            "#chapters tr[data-url], table.chapter-list tr[data-url], "
            "#chapters a[href*='/chapter/'], .chapter-list a[href*='/chapter/']"
        ):
            url = elem.get("data-url") or elem.get("href") or ""
            match = re.search(
                r"/(?:fiction/\d+/)?chapter/(\d+)",
                str(url), flags=re.IGNORECASE,
            )
            if match:
                paths.add(match.group(1))
        return len(paths)
    if site == "Scribble Hub":
        ids = set()
        for elem in soup.select(
            ".chapter_list .chapter-title a[href], "
            ".chapter_list a[href], li.toc_w a[href], #chp_raw a[href]"
        ):
            href = elem.get("href") or ""
            match = re.search(r"/read/[^/]+/chapter/(\d+)/?", href)
            if match:
                ids.add(match.group(1))
            elif "/read/" in href or "/chapter/" in href:
                ids.add(href.split("#")[0].rstrip("/"))
        return len(ids)
    # SpaceBattles and Sufficient Velocity often paginate threadmarks.
    # Reading just the first threadmarks page must not be called a total.
    if site in ("SpaceBattles", "Sufficient Velocity"):
        elem = soup.select_one(".threadmark-count, [data-threadmark-count]")
        if elem:
            return _number(elem.get("data-threadmark-count") or _text(elem))
    return 0


def parse_story_html(raw_html: str, source: dict) -> dict:
    """Return metadata and canonical source ID from saved browser HTML."""
    soup = BeautifulSoup(raw_html, "html.parser")
    info = dict(source)
    site = info["source_site"]

    if site == "AO3":
        title = _text(soup.select_one("h2.title.heading"))
        title = title or _meta(soup, "og:title", "twitter:title") or _text(soup.title)
        title = _clean_title(title)
        author = _tags(soup, "h3.byline a[rel~=author], a[rel~=author]", limit=10)
        if not author:
            author = _tags(soup, "h3.byline a[href*='/users/']", limit=10)
        fandoms = _tags(soup, "dd.fandom.tags a.tag, dd.fandom a.tag")
        freeform = _tags(soup, "dd.freeform.tags a.tag, dd.freeform a.tag")
        relationship = _tags(soup, "dd.relationship.tags a.tag", limit=30)
        characters = _tags(soup, "dd.character.tags a.tag", limit=30)
        summary = _text(soup.select_one("div.summary blockquote, div.summary .userstuff"))
        stats = soup.select_one("dl.stats") or soup
        chapters = _text(stats.select_one("dd.chapters"))
        words = _text(stats.select_one("dd.words"))
        published = _text(stats.select_one("dd.published"))
        status_label = _text(stats.select_one("dt.status")).lower()
        chapter_count = _number(chapters.split("/")[0])
        info.update({
            "title": title,
            "author": ", ".join(author),
            "fandom": ", ".join(fandoms),
            "description": summary or _meta(soup, "og:description", "description"),
            "tags": list(dict.fromkeys(freeform + relationship + characters))[:80],
            "total_units": chapter_count,
            "word_count": _number(words),
            "progress_unit": "chapters",
            "publication_status": "completed" if "completed" in status_label else "ongoing",
            "published_at": published,
            "language": _text(soup.select_one("dd.language")),
        })
        if not chapters and not status_label:
            info["publication_status"] = "unknown"
    else:
        title = _meta(soup, "og:title", "twitter:title") or _text(soup.title)
        title = _clean_title(title)
        author = _meta(soup, "author", "article:author")
        if not author and site == "FanFiction.net":
            author = _text(soup.select_one("a[href^='/u/']"))
        if not author and site in ("Royal Road", "Scribble Hub"):
            author = _text(soup.select_one("h4 a[href*='/profile/'], a.author"))
        info.update({
            "title": title,
            "author": author,
            "description": _meta(soup, "og:description", "description"),
            "total_units": _chapter_count(soup, site, info["source_id"]),
            "progress_unit": "chapters",
        })
        cover = _meta(soup, "og:image")
        if cover.startswith("https://"):
            info["cover_url"] = cover

    if not info.get("title") or re.search(
        r"(just a moment|attention required|cloudflare|sign in|access denied|"
        r"error 525|bad gateway|security check)",
        info["title"], re.IGNORECASE,
    ):
        raise ValueError("The page contains no readable story metadata (it may be blocked or require login).")

    return info
