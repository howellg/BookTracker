"""v0.24 recent audiobook continuity, safe player switching, global speed."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox
from test_v018 import FakeMediaPlayer, FakeOutput

from booktracker.audio_gui import AudioLibraryPage
from booktracker.audio_player import AudioPlayerDialog
from booktracker.gui import MainWindow
from booktracker.recent_audio import KEY, MAX_RECENT, RecentAudioStore

pytest_plugins = ("test_v019",)


@pytest.fixture
def recent(tmp_path):
    settings = QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat)
    return RecentAudioStore(settings)


def test_recent_store_bounds_and_validates_source(recent, data):
    db, work, files = data
    assert recent.most_recent(files) is None
    assert recent.mark_opened(files[0])
    assert recent.mark_opened(files[1])
    assert [row["id"] for row in recent.available(files)] == [
        files[1]["id"], files[0]["id"]
    ]
    assert recent.mark_opened(files[0])
    assert [row["id"] for row in recent.available(files)] == [
        files[0]["id"], files[1]["id"]
    ]
    assert recent.most_recent(files)["id"] == files[0]["id"]
    assert not db.history(work)
    assert "recent_audio_files" not in str(db.export_data())
    for _ in range(MAX_RECENT + 5):
        assert recent.mark_opened(dict(files[0], id=str(uuid4())))
    assert len(recent._load()) == MAX_RECENT
    assert not recent.mark_opened(dict(files[0], available=False))
    recent.clear()
    assert recent.available(files) == []


def test_recent_excludes_moved_missing_and_unlinked_files(recent, data):
    _db, _work, files = data
    first=files[0]
    second=files[1]
    recent.mark_opened(first)
    recent.mark_opened(second)
    Path(second["path"]).write_bytes(b"file changed")
    assert [row["id"] for row in recent.available(files)] == [first["id"]]
    assert recent.available([files[2]]) == []
    Path(first["path"]).unlink()
    assert recent.most_recent(files) is None


def test_corrupted_recent_json_does_not_crash(recent, data):
    _db,_work,files=data
    recent.settings.setValue(KEY,"{this is not JSON")
    assert recent.available(files)==[]
    recent.settings.setValue(KEY,"x"*50_001)
    assert recent.available(files)==[]
    recent.settings.setValue(KEY,json.dumps([{"id":"bad","stamp":["x",5,6]}]))
    assert recent.available(files)==[]
    recent.settings.setValue(KEY,json.dumps([{"id":str(uuid4()),"stamp":[
        "path",False,1
    ]}]))
    assert recent.available(files)==[]


def test_player_records_recent_without_sessions(app,data,tmp_path,monkeypatch):
    from booktracker import audio_player

    db,work,files=data
    settings=QSettings(str(tmp_path/"ui.ini"),QSettings.Format.IniFormat)
    recent=RecentAudioStore(settings)
    monkeypatch.setattr(
        audio_player,"RecentAudioStore",lambda settings=None:recent
    )
    player=AudioPlayerDialog(
        None,files,files[0],settings=settings,
        player=FakeMediaPlayer(),audio_output=FakeOutput()
    )
    try:
        assert recent.most_recent(files)["id"] == files[0]["id"]
        player.change_part(1)
        assert recent.most_recent(files)["id"] == files[1]["id"]
        assert not db.list_sessions(work_id=work)
        assert not db.history(work)
    finally:
        player.close()


def test_audio_page_switches_books_only_with_confirmation(
    app,data,tmp_path,monkeypatch
):
    from booktracker import audio_player

    db,_work,files=data
    page=AudioLibraryPage(None,db)
    openings=[]
    class FakeDialog(QDialog):
        def __init__(self,parent,other_files,selected):
            super().__init__(parent)
            self.current=selected
            openings.append(self)

    monkeypatch.setattr(audio_player,"AudioPlayerDialog",FakeDialog)
    try:
        page.launch_audio_file(files[0])
        assert len(openings)==1
        page.launch_audio_file(files[0])
        assert len(openings)==1
        monkeypatch.setattr(
            QMessageBox,"question",lambda *_a,**_kw:QMessageBox.StandardButton.No
        )
        page.launch_audio_file(files[1])
        assert page._player_dialog is openings[0]
        monkeypatch.setattr(
            QMessageBox,"question",lambda *_a,**_kw:QMessageBox.StandardButton.Yes
        )
        page.launch_audio_file(files[1])
        assert len(openings)==2
        assert page._player_dialog is openings[1]
        assert not openings[0].isVisible()
        assert openings[1].isVisible()
    finally:
        if page._player_dialog is not None:
            page._player_dialog.close()
        page.close()


def test_audio_page_can_resume_and_choose_recent(app,data,tmp_path,monkeypatch):
    from booktracker import audio_player

    db,work,files=data
    page=AudioLibraryPage(None,db)
    page.recent_store=RecentAudioStore(
        QSettings(str(tmp_path/"recent.ini"),QSettings.Format.IniFormat)
    )
    seen=[]
    class FakeDialog(QDialog):
        def __init__(self,parent,other_files,selected):
            super().__init__(parent)
            self.current=selected
            seen.append(selected["id"])

    monkeypatch.setattr(audio_player,"AudioPlayerDialog",FakeDialog)
    try:
        page.resume_recent()
        assert "No previously opened" in page.scan_message.text()
        page.recent_store.mark_opened(files[0])
        page.recent_store.mark_opened(files[1])
        page.resume_recent()
        assert seen==[files[1]["id"]]
        page._player_dialog.close()
        monkeypatch.setattr(
            "booktracker.audio_gui.QInputDialog.getItem",
            lambda *_args,**_kw:(
                (f"{files[0]['work_title']} — {files[0]['filename']} "
                 f"({files[0]['narrator'] or 'Narrator unspecified'})"),True
            )
        )
        page.choose_recent()
        assert seen[-1]==files[0]["id"]
        assert not db.list_sessions(work_id=work)
    finally:
        if page._player_dialog is not None:
            page._player_dialog.close()
        page.close()


def test_main_mini_speed_controls_pitch_safe_player(app,data,tmp_path):
    db,work,files=data
    main=MainWindow(db)
    player=AudioPlayerDialog(
        None,files,files[0],
        settings=QSettings(str(tmp_path/"speed.ini"),QSettings.Format.IniFormat),
        player=FakeMediaPlayer(),audio_output=FakeOutput()
    )
    main.audio_page._player_dialog=player
    main.attach_audio_player(player)
    try:
        assert main.audio_mini_speed.isEnabled()
        index=main.audio_mini_speed.findData(1.75)
        main.audio_mini_speed.setCurrentIndex(index)
        assert player.speed.currentData()==1.75
        assert player.player.rate==1.75
        player.speed.setCurrentIndex(player.speed.findData(1.25))
        assert main.audio_mini_speed.currentData()==1.25
        player._supports_pitch_safe_speed=False
        main.refresh_audio_toolbar()
        assert not main.audio_mini_speed.isEnabled()
        main.audio_mini_speed.setCurrentIndex(main.audio_mini_speed.findData(2.5))
        assert player.speed.currentData()==1.25
        assert not db.history(work)
    finally:
        player.close()
        main.audio_page._player_dialog=None
        main.detach_audio_player()
        main.close()
