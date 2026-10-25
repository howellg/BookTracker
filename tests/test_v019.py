"""v0.19 pitch-safe playback, local playlists and global transports."""
from __future__ import annotations

import json
import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from test_v013 import candidate
from test_v015 import make_epub, with_book
from test_v018 import FakeMediaPlayer, FakeOutput

from booktracker.audio_player import AudioPlayerDialog
from booktracker.audiobooks import group_candidates
from booktracker.database import Library
from booktracker.gui import MainWindow
from booktracker.mpv_player import MpvPlayer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def data(tmp_path):
    db = Library(tmp_path / "app.sqlite3")
    work = db.add_work({"title": "The Clock", "author": "Reader", "kind": "fanfiction"})
    files = []
    for index in range(1, 4):
        path = tmp_path / f"The Clock - Part({index}).m4b"
        path.write_bytes(b"FAKE TEST SOURCE")
        files.append(candidate(path, title="The Clock", author="Reader", part=index))
    db.import_audio_files(files, {group_candidates(files)[0]["key"]: work})
    yield db, work, db.list_audio_files(work_id=work)
    db.close()


def dialog(data, tmp_path):
    _db, _work, files = data
    return AudioPlayerDialog(
        None, files, files[0],
        settings=QSettings(str(tmp_path/"ui.ini"), QSettings.Format.IniFormat),
        player=FakeMediaPlayer(), audio_output=FakeOutput()
    )


def test_queue_reorder_persist_and_restore(app, data, tmp_path):
    view = dialog(data, tmp_path)
    try:
        original = [item["id"] for item in view.files]
        assert len(original) == 3
        view.queue_list.setCurrentRow(2)
        view.move_queue(-1)
        assert [item["id"] for item in view.files] == [original[0],original[2],original[1]]
        assert view.current["id"] == original[0]
        settings=view.settings
        key=view._queue_key
        saved=json.loads(settings.value(key))
        assert saved["order"] == [original[0],original[2],original[1]]
        assert set(saved["known"])==set(original)
        view.queue_list.setCurrentRow(2)
        view.remove_from_queue()
        assert [item["id"] for item in view.files]==[original[0],original[2]]
        view.queue_list.setCurrentRow(0)
        view.remove_from_queue()
        assert len(view.files)==2  # Cannot remove active audio.
        view.restore_queue()
        assert len(view.files)==3
        assert [item["id"] for item in view.files] == original
    finally:
        view.close()


def test_queue_selected_next_preserves_safe_edition(app, data, tmp_path):
    view=dialog(data,tmp_path)
    try:
        original=[item["id"] for item in view.files]
        assert view.queue_selected_next(view.files[2])
        assert [item["id"] for item in view.files]==[
            original[0],original[2],original[1]
        ]
        assert not view.queue_selected_next(view.current)
        assert not view.queue_selected_next(dict(
            view.files[1],narrator="Different narrator"
        ))
        assert not view.queue_selected_next(dict(
            view.files[1],work_id="another-work"
        ))
        assert not view.queue_selected_next(dict(
            view.files[1],is_complete=1
        ))
    finally:
        view.close()


def test_queue_persists_after_reopen_and_no_db_writes(app, data, tmp_path):
    db,work,files=data
    first=dialog(data,tmp_path)
    first.queue_selected_next(files[2])
    first.queue_list.setCurrentRow(2)
    first.remove_from_queue()
    expected=[item["id"] for item in first.files]
    assert len(expected)==2
    first.close()
    second=dialog(data,tmp_path)
    try:
        assert [item["id"] for item in second.files]==expected
        second.restore_queue()
        assert len(second.files)==3
        second.speed.setCurrentIndex(second.speed.findData(2.0))
        assert second.player.rate==2.0  # Injected pitch-safe test double.
        assert not db.list_sessions(work_id=work)
        assert not db.history(work)
        assert "player_queue" not in str(db.export_data())
    finally:
        second.close()


def test_qt_fallback_forces_pitch_safe_normal_speed(app, data, tmp_path, monkeypatch):
    monkeypatch.setattr("booktracker.mpv_player.pitch_safe_backend",lambda:None)
    db,work,files=data
    view=AudioPlayerDialog(
        None,files,files[0],
        settings=QSettings(str(tmp_path/"fallback.ini"),QSettings.Format.IniFormat)
    )
    try:
        assert view._supports_pitch_safe_speed is False
        assert not view.speed.isEnabled()
        assert view.speed.currentData()==1.0
        view.change_speed()
        assert float(view.settings.value("player_speed"))==1.0
        assert not db.history(work)
    finally:
        view.close()


@pytest.mark.skipif(shutil.which("mpv") is None,reason="mpv not installed")
def test_mpv_explicit_pitch_compensation_and_private_ipc(app):
    engine=MpvPlayer()
    try:
        arguments=engine._process.arguments()
        assert "--no-config" in arguments
        assert "--audio-pitch-correction=yes" in arguments
        assert "--no-video" in arguments
        assert engine.socket_path.startswith(engine._tmp.name)
        engine.setPlaybackRate(1.75)
        assert engine._process.state().name == "Running"
    finally:
        directory=engine._tmp.name
        engine.shutdown()
    from pathlib import Path

    assert not Path(directory).exists()


def test_global_transport_binding_keeps_library_unchanged(app,data,tmp_path):
    db,work,files=data
    main=MainWindow(db)
    player=AudioPlayerDialog(
        None,files,files[0],
        settings=QSettings(str(tmp_path/"mini.ini"),QSettings.Format.IniFormat),
        player=FakeMediaPlayer(),audio_output=FakeOutput()
    )
    main.audio_page._player_dialog=player
    main.attach_audio_player(player)
    try:
        assert main.audio_toolbar.isVisibleTo(main) or not main.isVisible()
        main.refresh_audio_toolbar()
        assert "The Clock" in main.audio_mini_label.text()
        player.player.durationChanged.emit(180000)
        player.toggle_play()
        main.audio_transport("forward")
        assert player.player.position()==30_000
        main.audio_transport("back")
        assert player.player.position()==0
        main.audio_transport("next")
        assert player.current["id"]==files[1]["id"]
        main.audio_transport("previous")
        assert player.current["id"]==files[0]["id"]
        assert not db.list_sessions(work_id=work)
        assert not db.history(work)
    finally:
        player.close()
        main.audio_page._player_dialog=None
        main.detach_audio_player()
        main.close()


def test_epub_reader_audio_bridge(app,data,tmp_path,monkeypatch):
    db,work,_files=data
    epub = make_epub(tmp_path/"linked.epub")
    _work_epub,linked = with_book(db,epub)
    # A work without audio must keep the button disabled.
    from booktracker.reader_gui import EpubReader
    parent = MainWindow(db)
    calls=[]
    monkeypatch.setattr(parent,"play_audio_for_work",lambda key:calls.append(key))
    reader = EpubReader(parent,db,linked,
                        settings=QSettings(str(tmp_path/"reader.ini"),QSettings.Format.IniFormat))
    try:
        assert not reader.listen_button.isEnabled()
    finally:
        reader.close()
    # When an EPUB link is on an audiobook's work, button launches that work.
    db.add_local_file(work,epub)
    epub_file=next(row["id"] for row in db.list_local_files(work) if row["format"]=="epub")
    reading = EpubReader(parent,db,epub_file,
                         settings=QSettings(str(tmp_path/"reader-2.ini"),QSettings.Format.IniFormat))
    try:
        assert reading.listen_button.isEnabled()
        reading.listen_button.click()
        assert calls==[work]
    finally:
        reading.close()
        parent.close()
