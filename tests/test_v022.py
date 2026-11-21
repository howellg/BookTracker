"""v0.22 device-local chapter cache and resilient multi-part playback."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QSettings
from test_v018 import FakeMediaPlayer, FakeOutput

from booktracker.audio_player import AudioPlayerDialog, AudioResumeStore
from booktracker.chapter_cache import (
    MAX_CACHE_BYTES,
    MAX_CACHED_FILES,
    ChapterCache,
)

pytest_plugins = ("test_v019",)


@pytest.fixture
def entry(tmp_path):
    file = tmp_path / "Story - Part(1).m4b"
    file.write_bytes(b"not real audio; only file identity is tested")
    return {"id": str(uuid4()), "path": str(file), "work_id": str(uuid4()),
            "available": True}


@pytest.fixture
def chapters():
    return {
        "probe_status": "ok",
        "duration_ms": 90_000,
        "chapters": [
            {"start_ms": 0, "end_ms": 40_000, "title": "Opening"},
            {"start_ms": 40_000, "end_ms": 90_000, "title": "Another chapter"},
        ],
    }


def test_chapter_cache_round_trip_and_reopen(tmp_path, entry, chapters):
    store = ChapterCache(tmp_path / "metadata")
    assert store.read(entry) is None
    assert store.write(entry, chapters)
    reopened = ChapterCache(tmp_path / "metadata")
    loaded = reopened.read(entry)
    assert loaded == chapters
    assert len(list(store.directory.glob("*.json"))) == 1
    assert store.clear(entry)
    assert reopened.read(entry) is None


def test_cache_is_invalidated_on_file_replacement_or_move(tmp_path, entry, chapters):
    store = ChapterCache(tmp_path / "metadata")
    assert store.write(entry, chapters)
    Path(entry["path"]).write_bytes(b"replaced with another file")
    assert store.read(entry) is None
    assert store.write(entry, chapters)
    original = Path(entry["path"])
    moved = original.rename(original.with_name("moved.m4b"))
    updated = dict(entry, path=str(moved))
    assert store.read(updated) is None
    assert not store.write(dict(entry, path=str(tmp_path / "../none.m4b")), chapters)
    assert store.write(updated, chapters)


def test_cache_rejects_malformed_and_large_content(tmp_path, entry, chapters):
    store = ChapterCache(tmp_path / "metadata")
    assert not store.write(entry, {"probe_status": "bad", "chapters": []})
    assert not store.write(entry, {"probe_status": "ok", "chapters": []})
    assert not store.write(entry, {"probe_status": "ok", "chapters": [
        {"start_ms": 99, "end_ms": 0, "title": "Bad"}
    ]})
    assert store.write(entry, chapters)
    file = store._cache_file(entry)
    file.write_text("{ broken", encoding="utf-8")
    assert store.read(entry) is None
    file.write_text(" " * (MAX_CACHE_BYTES + 1), encoding="utf-8")
    assert store.read(entry) is None
    file.write_text(json.dumps({"version": 1000, "fingerprint": []}),encoding="utf-8")
    assert store.read(entry) is None


def test_cache_is_bounded_by_number_of_files(tmp_path, entry, chapters):
    store = ChapterCache(tmp_path / "metadata")
    for _ in range(MAX_CACHED_FILES + 3):
        file = dict(entry, id=str(uuid4()))
        assert store.write(file, chapters)
    assert len(list(store.directory.glob("*.json"))) <= MAX_CACHED_FILES


def test_cache_sanitizes_filename_and_does_not_touch_library(tmp_path, data, chapters):
    db, work, files = data
    store = ChapterCache(tmp_path / "chapters")
    assert store.write(files[0], chapters)
    assert store.read(dict(files[0], id="../../unsafe")) is None
    assert not db.list_sessions(work_id=work)
    assert not db.history(work)
    assert "chapter_cache" not in str(db.export_data())


def test_player_reuses_cached_markers_without_renormalizing_playback(
    app, data, tmp_path, chapters, monkeypatch
):
    db, work, files = data
    store = ChapterCache(tmp_path / "chapters")
    assert store.write(files[0], chapters)
    monkeypatch.setattr("booktracker.audio_player.ChapterCache", lambda: store)
    def fail_probe(*args, **kwargs):
        raise AssertionError("Should use the local chapter cache, not ffprobe")
    monkeypatch.setattr("booktracker.audio_player.probe_m4b", fail_probe)
    view = AudioPlayerDialog(
        None, files, files[0],
        settings=QSettings(str(tmp_path/"settings.ini"),QSettings.Format.IniFormat),
        player=FakeMediaPlayer(), audio_output=FakeOutput()
    )
    try:
        assert view.chapter_list.count()==2
        assert view.chapter_load_button.text()=="Refresh markers"
        assert "local cache" in view.status.text()
        assert view.navigation_tabs.currentIndex()==0
        view.change_part(1)
        assert view.chapter_list.count()==0
        view.change_part(-1)
        assert view.chapter_list.count()==2
        assert not db.history(work)
    finally:
        view.close()


def test_player_rejects_unavailable_next_part_preserving_current_resume(
    app, data, tmp_path
):
    db, work, files = data
    settings=QSettings(str(tmp_path/"settings.ini"),QSettings.Format.IniFormat)
    view=AudioPlayerDialog(None,files,files[0],settings=settings,
                           player=FakeMediaPlayer(),audio_output=FakeOutput())
    try:
        view.player._duration = 180_000
        view.player.setPosition(12_000)
        Path(files[1]["path"]).unlink()
        view.change_part(1)
        assert view.current["id"]==files[0]["id"]
        assert "missing" in view.status.text()
        assert AudioResumeStore(settings).position(
            files[0]["id"],files[0]["path"])==12_000
        assert not db.history(work)
    finally:
        view.close()
