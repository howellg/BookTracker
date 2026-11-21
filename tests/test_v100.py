"""v1.0 acceptance: stable version, disposable self-test and help menu."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from booktracker import __version__
from booktracker.database import Library
from booktracker.maintenance import run_self_test


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_release_version_matches_metadata_and_documentation():
    root = Path(__file__).resolve().parent.parent
    assert __version__ == "1.0.0"
    config = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "1\.0\.0"$', config, flags=re.MULTILINE)
    assert "BookTracker 1.0.0" in (root / "README.md").read_text(encoding="utf-8")
    assert (root / "LICENSE").is_file()
    assert (root / "CHANGELOG.md").is_file()
    assert (root / "docs" / "RECOVERY.md").is_file()
    assert (root / "docs" / "RELEASE_CHECKLIST.md").is_file()
    assert "1.0.0" in (root / "CHANGELOG.md").read_text(encoding="utf-8")


def test_disposable_self_test_exercises_all_core_workflows():
    results = run_self_test()
    assert results["ok"], results
    assert results["kind"] == "self-test"
    assert len(results["checks"]) == 5
    assert results["checks"] == [
        "create-and-edit-test-library",
        "atomic-sqlite-snapshot",
        "portable-export-integrity",
        "portable-restore-roundtrip",
        "device-sync-roundtrip",
    ]


def test_self_test_does_not_open_or_edit_user_library(tmp_path):
    current = Library(tmp_path / "existing.sqlite3")
    current.add_work({"title": "Do not touch this work"})
    current.close()
    before = (tmp_path / "existing.sqlite3").read_bytes()
    assert run_self_test()["ok"]
    assert (tmp_path / "existing.sqlite3").read_bytes() == before


def test_self_test_failure_is_visible_and_non_destructive(monkeypatch):
    from booktracker import database

    def fail(*args, **kwargs):
        raise RuntimeError("Deliberate self-test failure")

    monkeypatch.setattr(database, "Library", fail)
    result = run_self_test()
    assert not result["ok"]
    assert result["checks"] == []
    assert "Deliberate self-test failure" in result["error"]
    assert "not accessed" in result["note"]


def test_headless_cli_selftest_ignores_configured_database(tmp_path):
    nonexistent = tmp_path / "untouched.sqlite3"
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    completed = subprocess.run(
        [sys.executable, "-m", "booktracker", "--database", str(nonexistent),
         "--self-test"],
        env=env, text=True, capture_output=True, timeout=20, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"]
    assert not nonexistent.exists()
    about = subprocess.run(
        [sys.executable, "-m", "booktracker", "--version"],
        env=env, text=True, capture_output=True, timeout=15, check=False,
    )
    assert about.returncode == 0
    assert "BookTracker 1.0.0" in about.stdout


def test_gui_help_menu_about_and_selftest_are_readonly(app, tmp_path, monkeypatch):
    from booktracker.gui import MainWindow

    library = Library(tmp_path / "help.sqlite3")
    work = library.add_work({"title": "Keep this book"})
    main = MainWindow(library)
    dialogs = []
    try:
        menu_titles = [action.text() for action in main.help_menu.actions()]
        assert "Run offline self-test" in menu_titles
        assert "About BookTracker" in menu_titles
        monkeypatch.setattr(
            QMessageBox, "about",
            lambda _parent, _title, body: dialogs.append(("about", body)),
        )
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda _parent, _title, body: dialogs.append(("info", body)),
        )
        main.show_about()
        main.run_release_self_test()
        assert len(dialogs) == 2
        assert dialogs[0][0] == "about"
        assert "BookTracker 1.0.0" in dialogs[0][1]
        assert "local-first" in dialogs[0][1]
        assert dialogs[1][0] == "info"
        assert "Offline self-test" in dialogs[1][1]
        assert library.get_work(work)
        assert not library.history(work)
        assert not library.list_sessions(work_id=work)
    finally:
        main.close()
        library.close()


def test_recovery_docs_do_not_claim_native_installers():
    root = Path(__file__).resolve().parent.parent
    overview = (root / "README.md").read_text(encoding="utf-8")
    assert "built-in local EPUB reader and M4B player" in overview
    assert "not an e-reader" not in overview
    assert "Stand-alone signed Windows/macOS installers" not in overview
    assert "Standalone signed Windows/macOS installers" in overview
    recovery = (root / "docs" / "RECOVERY.md").read_text(encoding="utf-8")
    assert "-wal" in recovery
    assert "--verify-backup" in recovery
    assert "imported_covers/" in recovery
