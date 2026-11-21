"""v0.26 pre-release stability: startup guard, WAL backups, shutdown."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date

import pytest
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from booktracker.backups import automatic_backup
from booktracker.database import Library
from booktracker.maintenance import inspect_sqlite


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    yield library
    library.close()


def test_daily_backup_verified_and_retains_existing_backup(db,tmp_path):
    key = db.add_work({"title": "Recovery-safe series"})
    db.record_progress_on(key, 10, date="2026-11-20")
    folder=tmp_path/"backups"
    path=automatic_backup(db,destination=folder)
    assert path is not None
    assert path.is_file()
    assert inspect_sqlite(path)["ok"]
    original=path.read_bytes()
    assert automatic_backup(db,destination=folder) is None
    assert path.read_bytes()==original
    assert not list(folder.glob(".booktracker-backup-*.sqlite3"))
    # Changes after a daily backup should never silently overwrite that snapshot.
    db.record_progress_on(key, 12, date="2026-11-20")
    assert automatic_backup(db,destination=folder) is None
    assert path.read_bytes()==original


def test_corrupted_daily_backup_is_reported_not_silently_ignored(db,tmp_path):
    folder=tmp_path/"backups"
    created=automatic_backup(db,destination=folder)
    assert created.is_file()
    created.write_bytes(b"invalid database backup")
    with pytest.raises(ValueError,match="failed its integrity"):
        automatic_backup(db,destination=folder)
    assert created.read_bytes()==b"invalid database backup"
    assert not list(folder.glob(".booktracker-backup-*.sqlite3"))


def test_daily_backup_retention_keeps_newest(db,tmp_path,monkeypatch):
    from booktracker import backups

    folder=tmp_path/"archive"
    folder.mkdir()
    old=[]
    for i in range(1,6):
        path=folder/f"BookTracker-2025-02-{i:02}.sqlite3"
        path.write_bytes(b"old snapshot")
        old.append(path)

    class NewDate(date):
        @classmethod
        def today(cls):
            return cls(2026,11,20)

    monkeypatch.setattr(backups,"date",NewDate)
    created=automatic_backup(db,destination=folder,keep=2)
    assert created.is_file()
    kept=sorted(folder.glob("BookTracker-????-??-??.sqlite3"))
    assert kept==[old[-1],created]
    assert inspect_sqlite(created)["ok"]


def test_database_preflight_refuses_corrupt_library_without_changes(
    app,tmp_path,monkeypatch
):
    from booktracker.gui import run

    bad=tmp_path/"broken.sqlite3"
    bad.write_bytes(b"corrupted SQLite" * 50)
    before=bad.read_bytes()
    messages=[]
    monkeypatch.setattr(
        QMessageBox,"critical",lambda *_args:messages.append("error")
    )
    assert run(bad)==2
    assert messages == ["error"]
    assert bad.read_bytes()==before
    assert not (tmp_path/"broken.sqlite3.booktracker.lock").exists()


def test_running_gui_guard_rejects_second_instance(app,tmp_path):
    file=tmp_path/"library.sqlite3"
    library=Library(file)
    library.add_work({"title":"Protected live"})
    library.close()
    guard=QLockFile(str(file.resolve())+".booktracker.lock")
    assert guard.tryLock(0)
    # The second process must be a separate OS PID. Locking from the same
    # process does not reliably model this on every Qt runtime.
    script=(
        "import sys\n"
        "from PySide6.QtWidgets import QMessageBox\n"
        "from booktracker.gui import run\n"
        "QMessageBox.warning=lambda *_args: None\n"
        "raise SystemExit(run(sys.argv[1]))\n"
    )
    try:
        result=subprocess.run(
            [sys.executable,"-c",script,str(file)],
            env={**os.environ,"QT_QPA_PLATFORM":"offscreen"},
            capture_output=True,text=True,check=False,timeout=12
        )
        assert result.returncode==3,(result.stdout,result.stderr)
        assert inspect_sqlite(file)["ok"]
    finally:
        guard.unlock()


def test_shutdown_refuses_to_close_library_while_audio_player_is_alive(
    app,db
):
    from booktracker.gui import MainWindow

    work=db.add_work({"title":"Keep the library open"})
    window=MainWindow(db)
    class BusyAudio:
        def close(self):
            return False
    window.audio_page._player_dialog=BusyAudio()
    assert window.close() is False
    assert db.get_work(work)
    window.audio_page._player_dialog=None
    assert window.close() is True


def test_shutdown_bounds_wait_for_background_import(app,db):
    from booktracker.gui import MainWindow

    work=db.add_work({"title":"Pending metadata read"})
    window=MainWindow(db)
    class BusyWorker:
        interrupted=False
        def isRunning(self):
            return True
        def requestInterruption(self):
            self.interrupted=True
        def wait(self,timeout):
            assert timeout==15000
            return False
    worker=BusyWorker()
    window.metadata_worker=worker
    assert window.close() is False
    assert worker.interrupted
    assert db.get_work(work)
    window.metadata_worker=None
    assert window.close() is True
