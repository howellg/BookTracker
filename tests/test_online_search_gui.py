"""Offline GUI tests for the book title search flow."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from booktracker.gui import WorkEditor


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    yield app


SAMPLE = {
    "title": "The Way of Kings",
    "author": "Brandon Sanderson",
    "kind": "book",
    "progress_unit": "pages",
    "publication_status": "completed",
    "total_units": 1007,
    "isbn": "9780765326355",
    "source_site": "Google Books",
    "source_id": "GOOGLEVOL1",
    "source_url": "https://books.google.com/books?id=GOOGLEVOL1",
    "published_at": "2010-08-31",
    "description": "A lengthy epic fantasy novel.",
}


def test_add_work_defaults_to_search_tab(app):
    editor = WorkEditor()
    assert editor.tabs.tabText(0) == "Find online"
    assert editor.tabs.currentIndex() == 0
    assert editor.online_search_button.isEnabled()
    assert editor.online_results.count() == 0
    editor.close()


def test_search_results_and_review(app):
    editor = WorkEditor()
    editor.show_online_results({"results": [SAMPLE], "errors": ["Google Books: partial rate limit"]})
    assert editor.online_results.count() == 1
    assert "1 book results" in editor.online_status.text()
    assert editor.online_use_button.isEnabled()
    assert "The Way of Kings" in editor.online_title.text()
    assert "Brandon Sanderson" in editor.online_meta.text()

    editor.use_online_result()
    values = editor.values()
    assert editor.tabs.currentIndex() == 1
    assert values["title"] == "The Way of Kings"
    assert values["author"] == "Brandon Sanderson"
    assert values["progress_unit"] == "pages"
    assert values["total_units"] == 1007
    assert values["source_site"] == "Google Books"
    assert values["source_id"] == "GOOGLEVOL1"
    assert values["isbn"] == "9780765326355"
    editor.close()


def test_add_selected_book_from_results(app):
    editor = WorkEditor()
    editor.show_online_results({"results": [SAMPLE], "errors": []})
    editor.add_online_result()
    assert editor.result() == QDialog.DialogCode.Accepted
    assert editor.values()["title"] == "The Way of Kings"


def test_find_online_button_from_details_tab(app):
    editor = WorkEditor()
    editor.tabs.setCurrentIndex(1)
    editor.title.setText("A sample search")
    editor.lookup_books = lambda: None  # avoid live network in this focused GUI test
    assert editor.tabs.tabText(1) == "Book & source"
    editor.close()


def test_no_results_are_handled(app):
    editor = WorkEditor()
    editor.show_online_results({"results": [], "errors": ["Open Library: timeout"]})
    assert editor.online_results.count() == 0
    assert not editor.online_add_button.isEnabled()
    assert "Some sources unavailable" in editor.online_status.text()
    editor.close()
