"""Regression coverage for v0.23 local audiobook bookmarks."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QInputDialog
from test_v018 import FakeMediaPlayer, FakeOutput

from booktracker.audio_bookmarks import AudioBookmarkStore, bookmarks_markdown
from booktracker.audio_player import AudioPlayerDialog

pytest_plugins = ("test_v019",)


@pytest.fixture
def store(tmp_path):
    return AudioBookmarkStore(
        QSettings(str(tmp_path / "bookmarks.ini"), QSettings.Format.IniFormat)
    )


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "A Book.m4b"
    path.write_bytes(b"one M4B placeholder")
    return {"id": str(uuid4()), "path": str(path), "filename": path.name}


def test_named_bookmark_lifecycle_and_sorted_display(store, audio_file):
    first = store.add(audio_file, 123_000, "Second chapter")
    second = store.add(audio_file, 10_000, "The beginning")
    rows = store.list(audio_file)
    assert [m["id"] for m in rows] == [second["id"], first["id"]]
    assert rows[0]["title"] == "The beginning"
    assert store.rename(audio_file, first["id"], "Edited name")
    assert store.list(audio_file)[1]["title"] == "Edited name"
    assert not store.rename(audio_file, str(uuid4()), "Nonexistent")
    assert store.remove(audio_file, second["id"])
    assert not store.remove(audio_file, second["id"])
    assert [m["id"] for m in store.list(audio_file)] == [first["id"]]


def test_bookmarks_are_stale_if_file_moves_or_changes(store, audio_file):
    store.add(audio_file, 9_000, "Old chapter")
    assert len(store.list(audio_file)) == 1
    Path(audio_file["path"]).write_bytes(b"replaced audio")
    assert store.list(audio_file) == []
    assert len(store.list(dict(audio_file, id=str(uuid4())))) == 0
    store.add(audio_file, 50_000, "New chapter")
    moved = Path(audio_file["path"]).rename(
        Path(audio_file["path"]).with_name("Elsewhere.m4b")
    )
    assert store.list(dict(audio_file, path=str(moved))) == []


def test_bookmark_validation_and_caps(store, audio_file):
    for value in (-1, 604_800_001, True, 4.5, "30000"):
        with pytest.raises(ValueError):
            store.add(audio_file, value, "Bad")
    for name in ("", " ", "\n"):
        with pytest.raises(ValueError):
            store.add(audio_file, 10_000, name)
    assert store.add(audio_file, 10_000, "  Neat\n name  ")["title"] == "Neat name"
    for position in range(199):
        store.add(audio_file, position, f"Bookmark {position}")
    assert len(store.list(audio_file)) == 200
    with pytest.raises(ValueError, match="200"):
        store.add(audio_file, 20_000, "Over the limit")


def test_corrupted_bookmark_payload_does_not_crash(store, audio_file):
    key = store._key(audio_file)
    store.settings.setValue(key, "{wrong JSON")
    assert store.list(audio_file) == []
    store.settings.setValue(key, "x" * 150001)
    assert store.list(audio_file) == []
    store.settings.setValue(key, json.dumps({
        "version": 1, "stamp": store._stamp(audio_file),
        "marks": [
            {"id": str(uuid4()), "position_ms": True, "title": "Invalid bool"},
            {"id": str(uuid4()), "position_ms": 2, "title": ""},
        ],
    }))
    assert store.list(audio_file) == []
    store.settings.setValue(key, json.dumps({
        "version": 1, "stamp": store._stamp(audio_file),
        "marks": [
            {"id": "not-a-uuid", "position_ms": 1000, "title": "Broken"},
            {"id": str(uuid4()), "position_ms": 2000, "title": "Valid"},
        ],
    }))
    assert len(store.list(audio_file)) == 1
    assert store.list(audio_file)[0]["title"] == "Valid"


def test_bookmark_markdown_across_multiple_audio_parts(store, audio_file, tmp_path):
    second_path = tmp_path / "Part(2).m4b"
    second_path.write_bytes(b"another file")
    next_file = dict(audio_file, id=str(uuid4()),
                     path=str(second_path), filename=second_path.name)
    store.add(audio_file, 3_600_000, "Part one")
    store.add(next_file, 65_000, "Part two")
    markdown = bookmarks_markdown([next_file, audio_file], store)
    assert markdown.startswith("# Audiobook timestamps")
    assert "## Part(2).m4b" in markdown
    assert "## A Book.m4b" in markdown
    assert "0:01:05" in markdown
    assert "1:00:00" in markdown
    assert markdown.index("Part two") < markdown.index("Part one")


def test_player_ui_saves_and_jumps_across_parts(app, data, tmp_path, monkeypatch):
    db, work, files = data
    settings = QSettings(str(tmp_path/"ui.ini"), QSettings.Format.IniFormat)
    media = FakeMediaPlayer()
    dialog = AudioPlayerDialog(None, files, files[0],
                               settings=settings,player=media,audio_output=FakeOutput())
    try:
        media._duration = 180_000
        media.setPosition(45_000)
        dialog.bookmark_name.setText("First scene")
        dialog.save_audio_bookmark()
        assert dialog.audio_bookmark_list.count() == 1
        assert dialog.navigation_tabs.currentIndex() == 2
        assert "First scene" in dialog.audio_bookmark_list.item(0).text()
        dialog.change_part(1)
        media.setPosition(70_000)
        dialog.bookmark_name.setText("Second part")
        dialog.save_audio_bookmark()
        assert dialog.audio_bookmark_list.count() == 2
        dialog.bookmark_filter.setText("second")
        assert dialog.audio_bookmark_list.item(0).isHidden()
        assert not dialog.audio_bookmark_list.item(1).isHidden()
        dialog.bookmark_filter.clear()
        dialog.audio_bookmark_list.setCurrentRow(0)
        dialog.jump_audio_bookmark()
        assert dialog.current["id"] == files[0]["id"]
        assert media.position() == 45_000
        monkeypatch.setattr(
            QInputDialog,"getText",lambda *_a,**_kw:("Renamed first scene",True)
        )
        dialog.audio_bookmark_list.setCurrentRow(0)
        dialog.rename_audio_bookmark()
        assert "Renamed" in dialog.audio_bookmark_list.item(0).text()
        dialog.copy_audio_bookmarks()
        assert "Renamed first scene" in QApplication.clipboard().text()
        dialog.audio_bookmark_list.setCurrentRow(0)
        dialog.remove_audio_bookmark()
        assert dialog.audio_bookmark_list.count() == 1
        assert not db.list_sessions(work_id=work)
        assert not db.history(work)
        assert "audio_bookmarks" not in str(db.export_data())
    finally:
        dialog.close()


def test_bookmark_persists_after_player_reopen(app, data, tmp_path):
    _db, _work, files = data
    settings = QSettings(str(tmp_path/"ui.ini"),QSettings.Format.IniFormat)
    first = AudioPlayerDialog(None,files,files[0],settings=settings,
                              player=FakeMediaPlayer(),audio_output=FakeOutput())
    first.player.setPosition(17_000)
    first.save_audio_bookmark()
    first.close()
    second = AudioPlayerDialog(None,files,files[0],settings=settings,
                               player=FakeMediaPlayer(),audio_output=FakeOutput())
    try:
        assert second.audio_bookmark_list.count()==1
        assert "0:17" in second.audio_bookmark_list.item(0).text()
    finally:
        second.close()


def test_global_bookmark_action_from_any_page(app, data, tmp_path):
    from booktracker.gui import MainWindow

    db, work, files = data
    settings=QSettings(str(tmp_path/"actions.ini"),QSettings.Format.IniFormat)
    media=FakeMediaPlayer()
    dialog=AudioPlayerDialog(None,files,files[0],settings=settings,
                             player=media,audio_output=FakeOutput())
    main=MainWindow(db)
    main.audio_page._player_dialog=dialog
    main.attach_audio_player(dialog)
    try:
        media.setPosition(32_000)
        main.audio_transport("bookmark")
        assert dialog.audio_bookmark_list.count()==1
        assert "0:32" in dialog.audio_bookmark_list.item(0).text()
        main.audio_transport("marks")
        assert dialog.navigation_tabs.currentIndex()==2
        assert "Ctrl+Alt+B" in [
            action.shortcut().toString() for action in main._audio_shortcuts
        ]
        assert not db.history(work)
    finally:
        dialog.close()
        main.audio_page._player_dialog=None
        main.detach_audio_player()
        main.close()
