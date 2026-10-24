"""Simple offscreen GUI regression tests for the work editor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from booktracker.gui import WorkEditor


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt


def test_url_recognition_and_manual_save_after_lookup_failure(app):
    editor = WorkEditor()
    editor.source_url.setText("https://archiveofourown.org/works/94243666/chapters/251533321")
    editor.title.setText("My manually entered story title")
    data = editor.values()
    assert data["title"] == "My manually entered story title"
    assert data["source_site"] == "AO3"
    assert data["source_id"] == "94243666"
    assert data["kind"] == "fanfiction"
    editor.close()


def test_imported_comma_tags_and_metadata_roundtrip(app):
    editor = WorkEditor()
    editor.fill_metadata({
        "title": "Example Story", "author": "Test Author",
        "source_site": "AO3",
        "source_url": "https://archiveofourown.org/works/94243666",
        "source_id": "94243666", "kind": "fanfiction",
        "tags": ["Time Travel", "No beta, we die like heroes"],
        "word_count": 123456, "total_units": 45, "publication_status": "ongoing",
        "fandom": "Harry Potter",
    })
    data = editor.values()
    assert data["tags"] == ["Time Travel", "No beta, we die like heroes"]
    assert data["word_count"] == 123456
    assert data["total_units"] == 45
    assert data["source_id"] == "94243666"
    assert data["publication_status"] == "ongoing"
    editor.close()


def test_changing_supported_source_url_updates_id(app):
    editor = WorkEditor()
    editor.source_url.setText("https://archiveofourown.org/works/11111")
    editor.source_url.setText("https://archiveofourown.org/works/22222")
    assert editor.values()["source_id"] == "22222"
    editor.source_url.setText("https://example.org/other")
    assert editor.values()["source_id"] == ""
    editor.close()
