"""Saved-page parsing and network-block handling."""
from __future__ import annotations

import urllib.error

import pytest

from booktracker.integrations import (
    fetch_saved_page_metadata,
    fetch_url_metadata,
    source_from_url,
)
from booktracker.story_metadata import parse_story_html

STORY_URL = "https://archiveofourown.org/works/94243666/chapters/251533321"
AO3_HTML = """<!DOCTYPE html><html><head><title>Example Story - Archive of Our Own</title></head>
<body><div id="workskin">
<h2 class="title heading">Example Story</h2>
<h3 class="byline heading">by <a rel="author" href="/users/ExampleAuthor">ExampleAuthor</a></h3>
<dl class="work meta group">
<dt class="fandom tags">Fandom:</dt><dd class="fandom tags"><a class="tag">Harry Potter</a></dd>
<dt class="freeform tags">Additional Tags:</dt>
<dd class="freeform tags"><a class="tag">Time Travel</a><a class="tag">Adventure</a></dd>
<div class="summary module"><h3>Summary:</h3><blockquote class="userstuff">A wizard's journey.</blockquote></div>
<dl class="stats">
<dt class="published">Published:</dt><dd class="published">2026-05-01</dd>
<dt class="words">Words:</dt><dd class="words">369,554</dd>
<dt class="chapters">Chapters:</dt><dd class="chapters">38/?</dd>
<dt class="status">Updated:</dt><dd class="status">2026-10-20</dd>
</dl></dl></div></body></html>
"""


def test_ao3_saved_page_parses_work_metadata(tmp_path):
    file = tmp_path / "story.html"
    file.write_text(AO3_HTML, encoding="utf-8")
    result = fetch_saved_page_metadata(file, STORY_URL)
    assert result["source_site"] == "AO3"
    assert result["source_id"] == "94243666"
    assert result["source_url"] == STORY_URL
    assert result["kind"] == "fanfiction"
    assert result["title"] == "Example Story"
    assert result["author"] == "ExampleAuthor"
    assert result["fandom"] == "Harry Potter"
    assert result["tags"] == ["Time Travel", "Adventure"]
    assert result["total_units"] == 38
    assert result["word_count"] == 369554
    assert result["publication_status"] == "ongoing"
    assert result["published_at"] == "2026-05-01"
    assert result["description"] == "A wizard's journey."


def test_ao3_completed_work():
    html = AO3_HTML.replace("38/?", "38/38").replace("Updated:", "Completed:")
    result = parse_story_html(html, source_from_url(STORY_URL))
    assert result["publication_status"] == "completed"


def test_invalid_saved_page_not_imported(tmp_path):
    file = tmp_path / "blocked.html"
    file.write_text("<title>Just a moment...</title><h1>Cloudflare</h1>")
    with pytest.raises(ValueError, match="no readable story metadata"):
        fetch_saved_page_metadata(file, STORY_URL)


def test_ao3_http525_explains_browser_fallback(monkeypatch):
    def fake_get(_url):
        raise urllib.error.HTTPError(_url, 525, "Cloudflare", None, None)

    monkeypatch.setattr("booktracker.integrations._get", fake_get)
    with pytest.raises(ValueError, match=r"HTTP 525.*Import saved page"):
        fetch_url_metadata(STORY_URL)


def test_saved_html_does_not_fetch_network(monkeypatch, tmp_path):
    file = tmp_path / "story.html"
    file.write_text(AO3_HTML)
    def no_network(_url):
        raise AssertionError("Reading a saved page must not contact any site.")
    monkeypatch.setattr("booktracker.integrations._get", no_network)
    assert fetch_saved_page_metadata(file, STORY_URL)["title"] == "Example Story"


def test_generic_page_metadata():
    html = """<html><head><meta property="og:title" content="An Example Story - Royal Road">
    <meta name="author" content="My Author">
    <meta name="description" content="A sample web novel">
    </head></html>"""
    source = source_from_url("https://www.royalroad.com/fiction/25000/an-example")
    result = parse_story_html(html, source)
    assert result["title"] == "An Example Story"
    assert result["author"] == "My Author"
    assert result["description"] == "A sample web novel"
    assert result["kind"] == "web_novel"
