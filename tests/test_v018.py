"""v0.18 local M4B player, safe resume and deliberate listening-session logging."""
from __future__ import annotations

import os
import uuid

import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QDialog
from test_v013 import candidate

from booktracker.audio_gui import AudioLibraryPage
from booktracker.audio_player import AudioPlayerDialog, AudioResumeStore, playlist_for_file, time_label
from booktracker.audiobooks import group_candidates
from booktracker.database import Library

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(tmp_path):
    db = Library(tmp_path / "library.sqlite3")
    yield db
    db.close()


@pytest.fixture
def audio(library, tmp_path):
    work = library.add_work({
        "title": "A Flower for the Soul", "author": "TheBlack'sResurgence",
        "kind": "fanfiction", "progress_unit": "chapters", "status": "plan_to_read"
    })
    paths = [tmp_path / f"A Flower for the Soul - Part({i}).m4b" for i in (1, 2)]
    for path in paths:
        path.write_bytes(b"FAKE M4B TEST FIXTURE")
    records = [candidate(path, title="A Flower for the Soul",
                         author="TheBlack'sResurgence", part=i)
               for i, path in enumerate(paths, start=1)]
    library.import_audio_files(records, {group_candidates(records)[0]["key"]: work})
    files = library.list_audio_files(work_id=work)
    assert len(files) == 2
    return work, files, paths


def test_resume_store_is_device_local_and_rejects_stale_file(tmp_path):
    path = tmp_path / "Narration.m4b"
    path.write_bytes(b"First media file")
    settings = QSettings(str(tmp_path / "device.ini"), QSettings.Format.IniFormat)
    file_id = str(uuid.uuid4())
    store = AudioResumeStore(settings)
    store.save(file_id, path, 125_000)
    settings.sync()
    reopened = AudioResumeStore(QSettings(str(tmp_path / "device.ini"), QSettings.Format.IniFormat))
    assert reopened.position(file_id, path) == 125_000
    assert reopened.position(file_id, path, duration_ms=30_000) == 29_000
    assert reopened.position(str(uuid.uuid4()), path) == 0
    with pytest.raises(ValueError):
        reopened.save(file_id, path, -1)
    with pytest.raises(ValueError):
        reopened.save(file_id, path, 9999999999)
    with pytest.raises(ValueError):
        reopened.save("../../../bad", path, 2_000)
    path.write_bytes(b"Replaced media file, not same size")
    assert reopened.position(file_id, path) == 0
    reopened.clear(file_id)
    assert reopened.position(file_id, path) == 0


def test_resume_is_not_part_of_portable_library_data(library, audio, tmp_path):
    work, files, paths = audio
    settings = QSettings(str(tmp_path / "local.ini"), QSettings.Format.IniFormat)
    AudioResumeStore(settings).save(files[0]["id"], paths[0], 650_000)
    data = library.export_data()
    assert "player_resume" not in str(data)
    assert library.list_sessions(work_id=work) == []
    assert library.history(work) == []


def test_playlist_is_stable_separate_from_full_editions_and_missing(audio):
    _work, items, _ = audio
    item = items[0]
    complete = dict(item, id=str(uuid.uuid4()), filename="Complete.m4b",
                    is_complete=1, part_number=0)
    other_narrator = dict(items[1], id=str(uuid.uuid4()), narrator="Other")
    other_work = dict(items[1], id=str(uuid.uuid4()), work_id=str(uuid.uuid4()))
    missing = dict(items[1], id=str(uuid.uuid4()), available=False)
    standalone = dict(items[1], id=str(uuid.uuid4()), part_number=0, is_complete=0)
    files = [items[1], complete, other_narrator, other_work, missing, standalone, items[0]]
    assert [f["id"] for f in playlist_for_file(files, item)] == [items[0]["id"], items[1]["id"]]
    assert [f["id"] for f in playlist_for_file(files, complete)] == [complete["id"]]
    with pytest.raises(ValueError):
        playlist_for_file(files, missing)
    assert time_label(3_700_000, 3_800_000) == "1:01:40 / 1:03:20"


class FakeOutput(QObject):
    def __init__(self):
        super().__init__()
        self.volume = 0.0

    def setVolume(self, value):
        self.volume = value


class FakeMediaPlayer(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object)

    def __init__(self):
        super().__init__()
        self._duration = 180_000
        self._position = 0
        self._status = QMediaPlayer.MediaStatus.LoadedMedia
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.source = None
        self.rate = 1.0

    def setAudioOutput(self, target):
        self.output = target

    def setSource(self, source):
        self.source = source
        self._position = 0

    def stop(self):
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.playbackStateChanged.emit(self._state)

    def play(self):
        self._state = QMediaPlayer.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self._state)

    def pause(self):
        self._state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def duration(self):
        return self._duration

    def position(self):
        return self._position

    def setPosition(self, value):
        self._position = value
        self.positionChanged.emit(value)

    def setPlaybackRate(self, value):
        self.rate = value

    def errorString(self):
        return "Mock media failure"


def test_audio_player_resume_seek_speed_parts_and_no_auto_session(
        app, library, audio, tmp_path, monkeypatch):
    work, files, paths = audio
    settings = QSettings(str(tmp_path / "play.ini"), QSettings.Format.IniFormat)
    store = AudioResumeStore(settings)
    store.save(files[0]["id"], paths[0], 90_000)
    media = FakeMediaPlayer()
    output = FakeOutput()
    view = AudioPlayerDialog(None, files, files[0], settings=settings,
                             player=media, audio_output=output)
    try:
        assert view.current["id"] == files[0]["id"]
        media.durationChanged.emit(180_000)
        assert media.position() == 90_000
        view.toggle_play()
        assert view.play_button.text() == "Pause"
        view.jump(30_000)
        assert media.position() == 120_000
        view.speed.setCurrentIndex(view.speed.findData(1.5))
        assert media.rate == 1.5
        view.volume.setValue(40)
        assert output.volume == 0.4
        monkeypatch.setattr(
            "booktracker.audio_player.probe_m4b",
            lambda _p: {
                "probe_status": "ok",
                "chapters": [{"start_ms": 48_000, "end_ms": 82_000, "title": "Arrival"}]
            }
        )
        view.load_chapters()
        assert view.chapters.count() == 2
        view.chapters.setCurrentIndex(1)
        view.seek_chapter()
        assert media.position() == 48_000
        view.change_part(1)
        assert view.current["id"] == files[1]["id"]
        assert store.position(files[0]["id"], paths[0]) == 48_000
        media.durationChanged.emit(180_000)
        media.setPosition(60_000)
        view.close()
        assert store.position(files[1]["id"], paths[1]) == 60_000
        assert library.list_sessions(work_id=work) == []
        assert library.history(work) == []
    finally:
        view.close()


def test_player_end_clears_resume_without_rewriting(app, library, audio, tmp_path):
    work, files, paths = audio
    settings = QSettings(str(tmp_path / "finish.ini"), QSettings.Format.IniFormat)
    media = FakeMediaPlayer()
    view = AudioPlayerDialog(None, files, files[0], settings=settings,
                             player=media, audio_output=FakeOutput())
    try:
        media.durationChanged.emit(180_000)
        media.setPosition(175_000)
        view.save_position()
        assert AudioResumeStore(settings).position(files[0]["id"], paths[0]) == 175_000
        media._status = QMediaPlayer.MediaStatus.EndOfMedia
        view.on_media_status(media._status)
        view.save_position()
        assert AudioResumeStore(settings).position(files[0]["id"], paths[0]) == 0
    finally:
        view.close()
    assert library.list_sessions(work_id=work) == []


def test_optional_auto_next_and_manual_restart_do_not_log_time(
        app, library, audio, tmp_path):
    work, files, paths = audio
    settings = QSettings(str(tmp_path / "automatic.ini"), QSettings.Format.IniFormat)
    media = FakeMediaPlayer()
    view = AudioPlayerDialog(None, files, files[0], settings=settings,
                             player=media, audio_output=FakeOutput())
    try:
        view.auto_next.setChecked(True)
        assert settings.value("player_auto_next", type=bool)
        media.durationChanged.emit(180_000)
        media.setPosition(110_000)
        view.save_position()
        assert AudioResumeStore(settings).position(files[0]["id"], paths[0]) == 110_000
        media._status = QMediaPlayer.MediaStatus.EndOfMedia
        view.on_media_status(media._status)
        app.processEvents()
        assert view.current["id"] == files[1]["id"]
        assert view.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        assert AudioResumeStore(settings).position(files[0]["id"], paths[0]) == 0
        media._status = QMediaPlayer.MediaStatus.LoadedMedia
        media.setPosition(23_000)
        view.save_position()
        assert AudioResumeStore(settings).position(files[1]["id"], paths[1]) == 23_000
        view.restart_part()
        assert media.position() == 0
        assert AudioResumeStore(settings).position(files[1]["id"], paths[1]) == 0
    finally:
        view.close()
    assert library.list_sessions(work_id=work) == []
    assert library.history(work) == []


def test_audio_page_manual_session_is_explicit(app, library, audio, monkeypatch):
    work, files, _ = audio
    page = AudioLibraryPage(None, library)
    assert page.current_group()["work_id"] == work
    assert page.files.columnCount() == 9
    assert page.current_file()["id"] == files[0]["id"]
    assert library.list_sessions() == []
    from booktracker.gui import ReadingSessionDialog

    def accept_dialog(dialog):
        assert dialog.mode.currentData() == "listening"
        assert dialog.book.currentData() == work
        dialog.minutes.setValue(42)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReadingSessionDialog, "exec", accept_dialog)
    page.log_listening_session()
    sessions = library.list_sessions(work_id=work)
    assert len(sessions) == 1
    assert sessions[0]["minutes"] == 42
    assert sessions[0]["mode"] == "listening"
    assert library.history(work) == []
    page.close()


def test_player_window_is_nonmodal_and_timer_remains_available(
        app, library, audio, monkeypatch):
    from types import SimpleNamespace

    from booktracker import audio_player

    work, _, _ = audio
    page = AudioLibraryPage(None, library)
    windows = []

    class StubDialog(QDialog):
        def __init__(self, parent, files, selected):
            super().__init__(parent)
            assert selected["work_id"] == work
            assert len(files) == 2
            windows.append(self)

    monkeypatch.setattr(audio_player, "AudioPlayerDialog", StubDialog)
    calls = []
    page.main = SimpleNamespace(
        open_reading_timer=lambda **kw: calls.append(kw)
    )
    try:
        page.play_in_app()
        assert len(windows) == 1
        assert windows[0].isVisible()
        assert not windows[0].isModal()
        page.play_in_app()
        assert len(windows) == 1  # Existing window is reused.
        page.start_listening_timer()
        assert calls == [{"work_id": work, "mode": "listening"}]
        assert library.list_sessions(work_id=work) == []
        windows[0].close()
        app.processEvents()
        assert page._player_dialog is None
    finally:
        page.close()


def test_listening_timer_preselects_mode(app, library, audio):
    from booktracker.gui import ReadingTimerDialog

    work, _, _ = audio
    dialog = ReadingTimerDialog(None, library, work_id=work, mode="listening")
    try:
        assert dialog.book.currentData() == work
        assert dialog.mode.currentData() == "listening"
        assert dialog.elapsed_ms() == 0
        assert library.list_sessions() == []
    finally:
        dialog.close()
