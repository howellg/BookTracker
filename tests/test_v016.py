"""v0.16 reader quality-of-life: bounded full-book search and UI navigation."""
from __future__ import annotations

import hashlib
from threading import Event

import pytest
from test_v015 import make_epub, with_book

from booktracker.database import Library
from booktracker.epub_reader import EpubBook
from booktracker.epub_search import search_book


@pytest.fixture
def db(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    yield library
    library.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])



def test_book_search_scans_every_section_case_insensitively(tmp_path):
    path = make_epub(tmp_path / "search.epub", [
        "<h1>Part One</h1><p>The Secret passage is here; secret again.</p>",
        "<h2>Part Two</h2><p>Nothing in this room.</p>",
        "<h1>Part Three</h1><p>Another SECRET is under the tree.</p>",
    ])
    report = search_book(EpubBook(path), "secret")
    assert [(h.section_index, h.occurrence) for h in report.hits] == [
        (0, 1), (0, 2), (2, 1)
    ]
    assert "Secret passage" in report.hits[0].excerpt
    assert report.sections_scanned == 3
    assert not report.truncated and not report.cancelled
    assert not report.skipped_sections


def test_book_search_literal_terms_and_bounds(tmp_path):
    path = make_epub(tmp_path / "literal.epub", [
        "<p>Special a.b? token a.b? again xxxxxxxxxxxxxx</p>",
        "<p>Last a.b? token</p>",
    ])
    book = EpubBook(path)
    assert len(search_book(book, "a.b?").hits) == 3
    assert len(search_book(book, "a.b?", max_hits=2).hits) == 2
    assert search_book(book, "a.b?", max_hits=2).truncated
    limited = search_book(book, "a.b?", max_sections=1)
    assert limited.sections_scanned == 1
    assert limited.truncated
    chars = search_book(book, "nonexistent", max_chars=8)
    assert chars.truncated
    assert chars.sections_scanned == 1
    for bad in ("", "s", "A" * 81):
        with pytest.raises(ValueError):
            search_book(book, bad)


def test_book_search_cancellation_and_no_mutation(tmp_path):
    path = make_epub(tmp_path / "unchanged.epub")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    event = Event()
    event.set()
    report = search_book(EpubBook(path), "chapter", cancel=event)
    assert report.cancelled and not report.hits
    assert report.sections_scanned == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_book_search_limits_repeated_results_per_section(tmp_path):
    path = make_epub(tmp_path / "lots.epub", [
        "<p>" + "dragon " * 60 + "</p>",
        "<p>dragon on the last page</p>",
    ])
    report = search_book(EpubBook(path), "dragon")
    assert len(report.hits) == 31
    assert report.truncated
    assert report.hits[-1].section_index == 1


def test_gui_contents_filter_keeps_selected_section(app, db, tmp_path):
    from PySide6.QtCore import QSettings

    from booktracker.reader_gui import EpubReader

    work, file_id = with_book(db, make_epub(tmp_path / "toc.epub"))
    reader = EpubReader(
        None, db, file_id,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    try:
        reader.step(1)
        reader.step(1)  # Discover heading when EPUB has no explicit nav TOC.
        reader.step(-1)
        reader.toc_filter.setText("Conclusion")
        assert reader.sections.item(0).isHidden()
        assert reader.sections.item(1).isHidden()
        assert not reader.sections.item(2).isHidden()
        assert reader.index == 1
        reader.toc_filter.clear()
        assert all(not reader.sections.item(i).isHidden()
                   for i in range(reader.sections.count()))
        assert not db.history(work)
    finally:
        reader.close()


def test_gui_full_book_search_results_and_jump(app, db, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtTest import QTest

    from booktracker.reader_gui import EpubReader

    work, file_id = with_book(db, make_epub(tmp_path / "gui-search.epub", [
        "<h1>Start</h1><p>A hidden DRAGON guards a dragon egg.</p>",
        "<h2>Middle</h2><p>No clues.</p>",
        "<h1>End</h1><p>The dragon flies away.</p>",
    ]))
    reader = EpubReader(
        None, db, file_id,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    try:
        reader.search.setText("dragon")
        reader.find_in_book()
        for _ in range(200):
            app.processEvents()
            if reader._search_thread is None:
                break
            QTest.qWait(10)
        assert reader._search_thread is None
        assert reader.book_matches.count() == 3
        assert "3 matches" in reader.message.text()
        reader.jump_to_search_hit(reader.book_matches.item(2))
        QTest.qWait(90)
        app.processEvents()
        assert reader.index == 2
        assert reader.viewer.textCursor().selectedText().casefold() == "dragon"
        assert db.history(work) == []
        assert db.active_run(work) is None
    finally:
        reader.close()


def test_gui_search_rejects_too_short_query(app, db, tmp_path):
    from booktracker.reader_gui import EpubReader

    _, file_id = with_book(db, make_epub(tmp_path / "short.epub"))
    reader = EpubReader(None, db, file_id)
    try:
        reader.search.setText("z")
        reader.find_in_book()
        assert reader._search_thread is None
        assert "2–80" in reader.message.text()
    finally:
        reader.close()
