"""v0.25: offline recovery diagnostics, backup checksums, sync compatibility."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from booktracker import sync
from booktracker.database import Library
from booktracker.maintenance import (
    format_report,
    inspect_backup,
    inspect_sqlite,
    snapshot_sqlite,
)
from booktracker.portable_backup import export_portable, import_portable, verify_portable


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def stable_export(library):
    value = library.export_data()
    value.pop("exported_at", None)
    return value


@pytest.fixture
def library(tmp_path):
    db = Library(tmp_path / "safe ? # library.sqlite3")
    work = db.add_work({"title": "Release safety", "author": "Test Author"})
    db.record_progress_on(work, 7, date="2026-11-19")
    db.add_session(work, read_date="2026-11-19", minutes=25, mode="reading")
    yield db
    db.close()


def test_readonly_database_doctor_checks_integrity_and_foreign_keys(library):
    before = stable_export(library)
    report = inspect_sqlite(library.path)
    assert report["ok"], report
    assert report["integrity"] == ["ok"]
    assert report["foreign_key_issues"] == []
    assert report["works"] == 1
    assert report["schema_version"] == 11
    assert report["full_check"]
    assert json.loads(format_report(report))["ok"]
    assert stable_export(library) == before


def test_readonly_doctor_rejects_invalid_or_missing_sqlite(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    assert not inspect_sqlite(missing)["ok"]
    assert not missing.exists()
    invalid = tmp_path / "not-sqlite.sqlite3"
    invalid.write_bytes(b"garbage" * 25)
    assert not inspect_sqlite(invalid)["ok"]
    assert not inspect_backup(tmp_path/"backup.zip")["ok"]


def test_snapshot_creation_is_atomic_and_never_overwrites(tmp_path, library):
    source_before = stable_export(library)
    destination = tmp_path / "new" / "safe snapshot.sqlite3"
    result = snapshot_sqlite(library.path, destination)
    assert result["ok"]
    assert result["works"] == 1
    assert inspect_backup(destination)["ok"]
    with pytest.raises(FileExistsError):
        snapshot_sqlite(library.path, destination)
    assert stable_export(library) == source_before
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT COUNT(*) FROM progress_events").fetchone()[0] == 1


def test_snapshot_failure_does_not_create_target_or_source(tmp_path, library):
    target = tmp_path / "never.sqlite3"
    missing = tmp_path / "not-here.sqlite3"
    with pytest.raises(FileNotFoundError):
        snapshot_sqlite(missing, target)
    assert not missing.exists()
    assert not target.exists()
    with pytest.raises(ValueError):
        snapshot_sqlite(library.path, library.path)


def test_portable_backup_roundtrip_checksum_and_legacy_compatibility(library, tmp_path):
    path = tmp_path / "release.btbackup"
    export_portable(library, path)
    report = verify_portable(path)
    assert report["ok"], report
    assert report["works"] == 1
    assert report["manifest_checksum"] == "verified"
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        assert "integrity.json" in members
        assert len(set(members)) == len(members)
        integrity = json.loads(archive.read("integrity.json"))
        assert integrity["library_sha256"] == hashlib.sha256(
            archive.read("library.json")
        ).hexdigest()
        legacy = tmp_path / "legacy.btbackup"
        with zipfile.ZipFile(legacy, "w") as output:
            output.writestr("library.json", archive.read("library.json"))
    assert verify_portable(legacy)["ok"]
    restored = Library(tmp_path / "imported.sqlite3")
    try:
        assert import_portable(restored, path) == 1
        assert len(restored.history()) == 1
        assert len(restored.list_sessions()) == 1
    finally:
        restored.close()


def test_corrupt_manifest_detected_before_restore_changes(tmp_path, library):
    path = tmp_path / "valid.btbackup"
    export_portable(library, path)
    with zipfile.ZipFile(path) as source:
        entries = [(item.filename, source.read(item)) for item in source.infolist()]
    corrupt = tmp_path / "corrupt.btbackup"
    with zipfile.ZipFile(corrupt, "w") as out:
        for name, body in entries:
            out.writestr(name, body + b" " if name == "library.json" else body)
    assert not verify_portable(corrupt)["ok"]
    assert "checksum" in verify_portable(corrupt)["error"]
    other = Library(tmp_path / "untouched.sqlite3")
    try:
        key = other.add_work({"title": "Keep me"})
        original = stable_export(other)
        with pytest.raises(ValueError, match="checksum"):
            import_portable(other, corrupt, strategy="replace")
        assert other.get_work(key)
        assert stable_export(other) == original
    finally:
        other.close()


def test_portable_rejects_duplicate_members_and_unexpected_paths(tmp_path, library):
    path = tmp_path / "original.btbackup"
    export_portable(library, path)
    with zipfile.ZipFile(path) as reader:
        content = reader.read("library.json")
    duplicate = tmp_path / "duplicate.btbackup"
    with zipfile.ZipFile(duplicate, "w") as out:
        out.writestr("library.json", content)
        with pytest.warns(UserWarning, match="Duplicate name"):
            out.writestr("library.json", content)
    assert "duplicate" in verify_portable(duplicate)["error"]
    forged = tmp_path / "traversal.btbackup"
    with zipfile.ZipFile(forged, "w") as out:
        out.writestr("library.json", content)
        out.writestr("../outside.txt", b"malicious")
    assert "unexpected" in verify_portable(forged)["error"]
    assert not (tmp_path/"outside.txt").exists()


def test_sync_protocol_continues_to_import_and_filter(tmp_path):
    share = tmp_path / "share"
    first = Library(tmp_path/"one"/"a.sqlite3")
    second = Library(tmp_path/"two"/"b.sqlite3")
    try:
        work = first.add_work({"title": "Synced story"})
        first.record_progress_on(work, 4, date="2026-11-19")
        archive = sync.publish(first, share)
        # Additional sync envelope is required and its hash validated.
        assert verify_portable(archive)["ok"]
        peer = sync.read_snapshot(archive)
        review = sync.plan(second, peer)
        assert sync.apply(second, review)["new_works"] == 1
        assert second.get_work(work)
        assert len(second.history(work)) == 1
    finally:
        first.close()
        second.close()


def test_cli_supports_headless_version_doctor_backup_verify(tmp_path, library):
    def call(*args):
        env = dict(os.environ)
        env.pop("QT_QPA_PLATFORM", None)
        return subprocess.run(
            [sys.executable, "-m", "booktracker", *map(str,args)],
            env=env, text=True, capture_output=True, timeout=15, check=False,
        )
    about = call("--version")
    assert about.returncode == 0
    from booktracker import __version__

    assert f"BookTracker {__version__}" in about.stdout
    result = call("--database", library.path, "--doctor")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"]
    target = tmp_path / "CLI-snapshot.sqlite3"
    saved = call("--database", library.path, "--snapshot", target)
    assert saved.returncode == 0
    assert target.is_file()
    assert json.loads(saved.stdout)["ok"]
    checked = call("--verify-backup", target)
    assert checked.returncode == 0
    bad = call("--verify-backup", tmp_path/"missing.btbackup")
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["ok"] is False


def test_gui_integrity_and_verification_are_readonly(app, tmp_path, monkeypatch):
    from booktracker.gui import MainWindow

    library = Library(tmp_path/"gui.sqlite3")
    library.add_work({"title": "Leave unchanged"})
    before = stable_export(library)
    main = MainWindow(library)
    try:
        calls = []
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda *_args: calls.append("pass"),
        )
        monkeypatch.setattr(
            "booktracker.gui.QFileDialog.getOpenFileName",
            lambda *_args: (str(library.path), "SQLite database (*.sqlite3)"),
        )
        main.show_integrity_result()
        main.verify_backup_file()
        assert calls == ["pass", "pass"]
        assert stable_export(library) == before
        file_menu = main.file_menu
        actions = {item.text(): item.shortcut().toString()
                   for item in file_menu.actions()}
        assert "Verify backup file" in actions
        assert actions["Audiobooks"] == "Ctrl+Alt+A"
    finally:
        main.close()
        library.close()
