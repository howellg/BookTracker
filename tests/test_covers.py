"""Persistent cover cache and GUI integration without external HTTP requests."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from booktracker.covers import CoverCache, _cache_file, cover_url_for
from booktracker.database import Library
from booktracker.gui import MainWindow


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def make_cached_image(cache, url):
    image = QPixmap(65, 90)
    image.fill(QColor("cyan"))
    cache.folder.mkdir(parents=True, exist_ok=True)
    assert image.save(str(_cache_file(cache.folder, url)), "PNG")


def test_only_valid_cover_hosts_and_isbn_fallback():
    from booktracker.covers import is_allowed_cover
    assert is_allowed_cover("https://covers.openlibrary.org/b/id/123-M.jpg")
    assert not is_allowed_cover("http://covers.openlibrary.org/b/id/123-M.jpg")
    assert not is_allowed_cover("https://example.com/cover.jpg")
    url = cover_url_for({"isbn": "9781234567890"})
    assert "9781234567890" in url and url.startswith("https://")


def test_cover_cache_loads_persisted_thumbnail_without_network(app, tmp_path):
    url = "https://covers.openlibrary.org/b/id/9876-M.jpg"
    cache = CoverCache(folder=tmp_path)
    make_cached_image(cache, url)
    pixmap = cache.get(url)
    assert pixmap is not None and not pixmap.isNull()
    cache2 = CoverCache(folder=tmp_path)
    assert cache2.get(url) is not None
    assert not cache2.pending


def test_cover_shows_in_library_table_and_details(app, tmp_path, monkeypatch):
    from booktracker import gui
    original = gui.CoverCache
    url = "https://covers.openlibrary.org/b/id/9876-M.jpg"
    monkeypatch.setattr(gui, "CoverCache",
                        lambda parent=None: original(parent, folder=tmp_path / "covers"))
    maker = original(folder=tmp_path / "covers")
    make_cached_image(maker, url)

    db = Library(tmp_path / "library.sqlite3")
    db.add_work({"title": "The Way of Kings", "author": "Brandon Sanderson",
                 "cover_url": url})
    window = MainWindow(db)
    try:
        assert window.table.rowCount() == 1
        title_cell = window.table.item(0, 0)
        assert not title_cell.icon().isNull()
        window.table.selectRow(0)
        assert window.current_cover_url == url
        assert window.current_cover_label.pixmap() is not None
        assert not window.current_cover_label.pixmap().isNull()
    finally:
        window.close()
