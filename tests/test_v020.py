"""v0.20 manually linked text/audio positions and long-form playback controls."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from test_v015 import make_epub
from test_v018 import FakeMediaPlayer, FakeOutput

from booktracker.audio_player import AudioPlayerDialog, parse_timestamp
from booktracker.audio_text_links import AudioTextAnchors
from booktracker.gui import MainWindow
from booktracker.reader_gui import EpubReader

pytest_plugins = ("test_v019",)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def linked(app, data, tmp_path):
    db, work, audios = data
    epub_path = make_epub(tmp_path / "Book.epub")
    epub_id = db.add_local_file(work, epub_path)
    epub_id = epub_id if isinstance(epub_id, str) else next(
        row["id"] for row in db.list_local_files(work) if row["format"] == "epub"
    )
    settings = QSettings(
        str(tmp_path / "anchors.ini"), QSettings.Format.IniFormat
    )
    yield db, work, epub_id, epub_path, audios, settings


def test_timestamp_parser_accepts_hhmmss_and_validates():
    assert parse_timestamp("30") == 30_000
    assert parse_timestamp("01:02") == 62_000
    assert parse_timestamp("2:03:04") == 7_384_000
    assert parse_timestamp(" 03:15 ") == 195_000
    for value in ("", ":", "1:60", "1:60:00", "1:01:65",
                  "-1", "1.5", "2:20:30:40", "hello", " 1 :15",
                  "999:00:00", "１:02"):
        with pytest.raises(ValueError):
            parse_timestamp(value)


def test_audio_text_anchor_persists_locally_and_bounds(linked):
    db, work, epub_id, epub_path, audio, settings = linked
    store = AudioTextAnchors(settings)
    created = store.add(
        work, epub_id, epub_path, audio[0],
        section_href="OEBPS/ch1.xhtml", section_title="Opening scene",
        scroll_ratio=2590, position_ms=3_600_500,
        audio_files=audio
    )
    reopened = AudioTextAnchors(
        QSettings(settings.fileName(), QSettings.Format.IniFormat)
    )
    assert reopened.list_links(work, epub_id, epub_path, audio)[0]["id"] == created["id"]
    assert reopened.list_links(work, epub_id, epub_path, audio)[0]["position_ms"] == 3_600_500
    assert not db.list_sessions(work_id=work)
    assert not db.history(work)
    assert "audio_text_anchors" not in str(db.export_data())
    for kwargs in ({"position_ms": -1}, {"scroll_ratio": 10001},
                   {"section_href": ""}):
        parameters = {
            "section_href": "OEBPS/ch1.xhtml", "section_title": "Opening",
            "scroll_ratio": 800, "position_ms": 15_000
        }
        parameters.update(kwargs)
        with pytest.raises(ValueError):
            store.add(work, epub_id, epub_path, audio[0], **parameters)
    assert store.remove(work, epub_id, created["id"])
    assert not store.remove(work, epub_id, created["id"])
    assert reopened.list_links(work, epub_id, epub_path, audio) == []


def test_audio_text_anchor_invalidates_changed_audio_or_epub(linked):
    _db, work, epub_id, epub_path, files, settings = linked
    store = AudioTextAnchors(settings)
    store.add(
        work, epub_id, epub_path, files[0],
        section_href="OEBPS/ch1.xhtml", section_title="Opening",
        scroll_ratio=0, position_ms=1000,
    )
    assert store.list_links(work, epub_id, epub_path, files)
    audio_path = Path(files[0]["path"])
    audio_path.write_bytes(b"changed audio file")
    assert store.list_links(work, epub_id, epub_path, files) == []
    # A separately added entry becomes invalid if the EPUB itself is replaced.
    store.add(
        work, epub_id, epub_path, files[1],
        section_href="OEBPS/ch1.xhtml", section_title="Opening",
        scroll_ratio=100, position_ms=2000,
    )
    assert store.list_links(work, epub_id, epub_path, files)
    epub_path.write_bytes(b"changed epub")
    assert store.list_links(work, epub_id, epub_path, files) == []


def test_audio_text_anchor_refuses_wrong_work_and_missing_file(linked):
    _db, work, epub_id, epub_path, files, settings = linked
    store = AudioTextAnchors(settings)
    bad = dict(files[0], work_id=str(uuid4()))
    with pytest.raises(ValueError):
        store.add(
            work, epub_id, epub_path, bad,
            section_href="a.xhtml", section_title="A",
            scroll_ratio=0, position_ms=0
        )
    with pytest.raises(ValueError):
        store.add(
            work, epub_id, epub_path, dict(files[0], available=False),
            section_href="a.xhtml", section_title="A",
            scroll_ratio=0, position_ms=0
        )


def test_audio_text_anchor_rejects_corrupted_settings(linked):
    _db, work, epub_id, epub_path, files, settings = linked
    store = AudioTextAnchors(settings)
    key = store._key(work, epub_id)
    settings.setValue(key, '{"bad":1}')
    assert store.list_links(work, epub_id, epub_path, files) == []
    settings.setValue(key, "x" * 150001)
    assert store.list_links(work, epub_id, epub_path, files) == []
    settings.setValue(key, json.dumps([
        None, False, [], "weird", {}, {"audio_id": []},
        {"audio_id": {}, "epub_stamp": store._epub_stamp(epub_path)}
    ]))
    assert store.list_links(work, epub_id, epub_path, files) == []


def test_player_precise_seek_and_rejects_bad_target(app, data, tmp_path):
    _db, _work, audio = data
    mock = FakeMediaPlayer()
    view = AudioPlayerDialog(
        None, audio, audio[0],
        settings=QSettings(str(tmp_path / "seek.ini"), QSettings.Format.IniFormat),
        player=mock, audio_output=FakeOutput()
    )
    try:
        mock.durationChanged.emit(180_000)
        view.goto_time.setText("1:35")
        view.jump_to_timestamp()
        assert mock.position() == 95_000
        view.goto_time.setText("not a time")
        view.jump_to_timestamp()
        assert mock.position() == 95_000
        view.goto_time.setText("9:00")
        view.jump_to_timestamp()
        assert mock.position() == 95_000
        with pytest.raises(ValueError):
            view.seek_to_position(-1)
        view.jump(-300_000)
        assert mock.position() == 0
    finally:
        view.close()


def test_sleep_timer_pauses_without_logging_and_can_cancel(app, data, tmp_path, monkeypatch):
    db, work, audio = data
    clock = [1000.0]
    monkeypatch.setattr("booktracker.audio_player.monotonic", lambda:clock[0])
    media=FakeMediaPlayer()
    view=AudioPlayerDialog(
        None, audio, audio[0],
        settings=QSettings(str(tmp_path / "sleep.ini"), QSettings.Format.IniFormat),
        player=media, audio_output=FakeOutput()
    )
    try:
        view.toggle_play()
        view.sleep.setCurrentIndex(view.sleep.findData(10))
        assert view.sleep_label.text() == "Pauses in 10:00"
        clock[0] += 599
        view.update_sleep()
        assert view.sleep_label.text() == "Pauses in 0:01"
        assert view.play_button.text()=="Pause"
        clock[0] += 1
        view.update_sleep()
        assert view.play_button.text()=="Play"
        assert view.sleep.currentData() == 0
        assert "finished" in view.sleep_label.text()
        view.toggle_play()
        view.sleep.setCurrentIndex(view.sleep.findData(30))
        view.sleep.setCurrentIndex(0)
        assert view._sleep_deadline is None
        clock[0] += 4000
        view.update_sleep()
        assert view.play_button.text()=="Pause"
    finally:
        view.close()
    assert not db.list_sessions(work_id=work)
    assert not db.history(work)


def test_sleep_timer_close_cleans_up(app, data, tmp_path):
    _db,_work,audio=data
    view=AudioPlayerDialog(
        None,audio,audio[0],
        settings=QSettings(str(tmp_path/"close.ini"),QSettings.Format.IniFormat),
        player=FakeMediaPlayer(),audio_output=FakeOutput()
    )
    view.sleep.setCurrentIndex(view.sleep.findData(15))
    assert view._sleep_tick.isActive()
    view.close()
    assert not view._sleep_tick.isActive()


def test_app_keyboard_media_bindings_no_effect_without_player(app,linked):
    db,work,_epub_id,_epub_path,_audio,_settings=linked
    main=MainWindow(db)
    try:
        shortcuts={item.shortcut().toString() for item in main._audio_shortcuts}
        for key in ("Ctrl+Alt+Space", "Ctrl+Alt+J", "Ctrl+Alt+L",
                    "Ctrl+Alt+PgUp", "Ctrl+Alt+PgDown"):
            assert key in shortcuts
        for action in main._audio_shortcuts:
            assert action.shortcutContext() == Qt.ShortcutContext.ApplicationShortcut
            action.trigger()  # No player: no accidental book progress.
        assert not db.history(work)
    finally:
        main.close()


def test_reader_explicit_anchor_ui_opens_both_locations(app,linked,monkeypatch):
    db,work,epub_id,_epub_path,audio,settings=linked
    parent=MainWindow(db)
    mock=FakeMediaPlayer()
    player=AudioPlayerDialog(
        None,audio,audio[0],
        settings=settings,player=mock,audio_output=FakeOutput()
    )
    parent.audio_page._player_dialog=player
    reader=EpubReader(parent,db,epub_id,settings=settings)
    try:
        assert reader.audio_links_list.count()==0
        assert len(reader.book.spine)>=2
        reader.load_section(1)
        mock.durationChanged.emit(180_000)
        mock.setPosition(75_000)
        reader.add_audio_text_link()
        assert reader.audio_links_list.count()==1
        assert reader.sidebar_tabs.currentIndex()==3
        assert not db.history(work)
        reader.load_section(0)
        mock.setPosition(1000)
        reader.audio_links_list.setCurrentRow(0)
        reader.open_audio_text_link()
        app.processEvents()
        assert reader.index==1
        assert mock.position()==75_000
        monkeypatch.setattr(
            QMessageBox,"question",lambda *_args,**_kw:QMessageBox.StandardButton.Yes
        )
        reader.remove_audio_text_link()
        assert reader.audio_links_list.count()==0
        assert not db.list_sessions(work_id=work)
    finally:
        reader.close()
        player.close()
        parent.audio_page._player_dialog=None
        parent.close()
