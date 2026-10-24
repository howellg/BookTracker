"""Select multiple works and paste offline title lists inside Add Work."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from booktracker.gui import WorkEditor


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def make_work(title, source_id, *, author="Brandon Sanderson", site="Open Library"):
    return {"title": title, "author": author, "source_site": site,
            "source_id": source_id, "progress_unit": "pages"}


def test_author_search_mode_and_select_all(app):
    dialog = WorkEditor()
    dialog.online_mode.setCurrentIndex(1)
    dialog.online_query.setText("Brandon Sanderson")
    dialog.show_online_results({"results": [
        make_work("The Way of Kings", "OL1"),
        make_work("Words of Radiance", "OL2"),
        make_work("Words of Radiance", "GOOGLE2", site="Google Books"),  # duplicate editions
        make_work("Tress of the Emerald Sea", "OL3"),
    ], "errors": []})
    assert dialog.online_results.count() == 3
    dialog.select_all_books()
    assert dialog.bulk_counter.text() == "3 selected"
    assert dialog.bulk_add_button.isEnabled()
    dialog.bulk_status_select.setCurrentIndex(0)
    dialog.add_selected_books()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.bulk_status == "completed"
    assert sorted(x["title"] for x in dialog.bulk_selection) == [
        "The Way of Kings", "Tress of the Emerald Sea", "Words of Radiance",
    ]


def test_check_one_then_clear_all(app):
    dialog = WorkEditor()
    dialog.show_online_results({"results": [
        make_work("The Final Empire", "OL1"),
        make_work("The Well of Ascension", "OL2"),
    ], "errors": []})
    assert not dialog.bulk_add_button.isEnabled()
    dialog.online_results.item(1).setCheckState(Qt.CheckState.Checked)
    assert dialog.bulk_counter.text() == "1 selected"
    dialog.clear_selected_books()
    assert dialog.bulk_counter.text() == "0 selected"
    assert not dialog.bulk_add_button.isEnabled()
    dialog.close()


def test_paste_title_list_with_bullets_deduplicates(app):
    dialog = WorkEditor()
    dialog.tabs.setCurrentIndex(1)
    assert dialog.tabs.tabText(1) == "Paste titles"
    dialog.pasted_author.setText("Brandon Sanderson")
    dialog.pasted_titles.setPlainText(
        "1. The Final Empire\n"
        "2) The Well of Ascension\n"
        "• The Hero of Ages\n"
        "- The Final Empire\n"
        "Words of Radiance\n"
    )
    dialog.add_pasted_titles()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert [w["title"] for w in dialog.bulk_selection] == [
        "The Final Empire", "The Well of Ascension",
        "The Hero of Ages", "Words of Radiance",
    ]
    assert all(w["author"] == "Brandon Sanderson" for w in dialog.bulk_selection)


def test_paste_requires_author_and_titles(app):
    dialog = WorkEditor()
    dialog.pasted_titles.setText("The Way of Kings")
    dialog.add_pasted_titles()
    assert not dialog.bulk_selection
    assert "Enter an author" in dialog.pasted_status_note.text()
    dialog.close()


def test_edit_dialog_disables_bulk_paste(app):
    dialog = WorkEditor(work={"id": "abc", "title": "Existing Book", "author": "Someone"})
    assert not dialog.tabs.isTabEnabled(1)
    dialog.close()


def test_bulk_can_opt_into_updating_existing_statuses(app):
    dialog = WorkEditor()
    dialog.online_mode.setCurrentIndex(1)
    dialog.show_online_results({"results": [make_work("The Way of Kings", "OL1")], "errors": []})
    dialog.select_all_books()
    dialog.bulk_update_checkbox.setChecked(True)
    dialog.add_selected_books()
    assert dialog.bulk_update_existing is True
    assert dialog.bulk_status == "completed"


def test_paste_option_can_update_existing_unread(app):
    dialog = WorkEditor()
    dialog.pasted_author.setText("Brandon Sanderson")
    dialog.pasted_titles.setText("The Way of Kings")
    dialog.pasted_update_checkbox.setChecked(True)
    dialog.add_pasted_titles()
    assert dialog.bulk_update_existing is True
