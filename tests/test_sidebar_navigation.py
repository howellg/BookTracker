"""v0.14.1: sidebar selection follows page ID, not sidebar display order."""
from __future__ import annotations

import os

import pytest

from booktracker.database import Library

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    current = QApplication.instance() or QApplication([])
    assert current is not None
    return current


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from booktracker.gui import MainWindow

    # Keep tests independent of the user's QSettings and local database.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = Library(tmp_path / "test.sqlite3")
    main = MainWindow(lib)
    main.resize(960, 630)
    main.show()
    app.processEvents()
    yield main
    main.close()
    lib.close()


def assert_active_button_matches_page(main, expected):
    assert main.pages.currentIndex() == expected
    checked = [nav for nav in main.nav_buttons if nav.isChecked()]
    assert len(checked) == 1
    assert checked[0].property("page_index") == expected


def test_every_sidebar_button_click_opens_and_stays_on_its_page(window, app):
    # The v0.14 regression only affected entries after Dashboard because
    # Device sync was moved up visually without changing QStackedWidget order.
    assert window.nav_buttons[3].text().endswith("Device sync")
    assert window.nav_buttons[3].property("page_index") == 13
    assert window.nav_buttons[4].text().endswith("Series")
    assert window.nav_buttons[4].property("page_index") == 3

    for nav in window.nav_buttons:
        expected = nav.property("page_index")
        nav.click()
        app.processEvents()
        assert_active_button_matches_page(window, expected)
        # Clicking the selected checkable navigation button again should
        # not make it appear unselected.
        nav.click()
        app.processEvents()
        assert_active_button_matches_page(window, expected)

    # Navigating backwards exercises the delayed-following-click symptom.
    for nav in reversed(window.nav_buttons):
        nav.click()
        app.processEvents()
        assert_active_button_matches_page(window, nav.property("page_index"))


def test_programmatic_navigation_and_subsequent_clicks_keep_highlight(window, app):
    for index in (13, 3, 4, 10, 12, 0, 11, 8, 13):
        window.navigate(index)
        app.processEvents()
        assert_active_button_matches_page(window, index)

    series_nav = next(
        nav for nav in window.nav_buttons if nav.property("page_index") == 3
    )
    series_nav.click()
    app.processEvents()
    assert_active_button_matches_page(window, 3)
