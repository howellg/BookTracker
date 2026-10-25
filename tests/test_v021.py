"""v0.21 playback browser, long multi-part timeline and queue safety."""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from test_v018 import FakeMediaPlayer, FakeOutput

from booktracker.audio_navigation import (
    MAX_TOTAL_MS,
    chapter_at,
    global_position,
    locate_global_time,
    playlist_duration,
    valid_chapters,
)
from booktracker.audio_player import AudioPlayerDialog, parse_timestamp

pytest_plugins = ("test_v019",)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fixture_data(data):
    return data


def view(fixture_data, tmp_path):
    _db, _work, files = fixture_data
    player = FakeMediaPlayer()
    dialog = AudioPlayerDialog(
        None, files, files[0],
        settings=QSettings(str(tmp_path / "v021.ini"), QSettings.Format.IniFormat),
        player=player, audio_output=FakeOutput(),
    )
    return dialog, player


def test_timeline_long_book_and_file_boundaries():
    files = [{"id": f"part-{idx}", "duration_ms": 72 * 3600 * 1000}
             for idx in range(4)]
    timeline = playlist_duration(files, "part-1")
    assert timeline["known"]
    assert timeline["total_ms"] == 288 * 3600 * 1000
    assert timeline["missing"] == 0
    assert global_position(timeline, "part-1", 10_000) == (72 * 3600 + 10) * 1000
    assert locate_global_time(timeline, 0) == (0, 0)
    assert locate_global_time(timeline, 72 * 3600 * 1000) == (1, 0)
    assert locate_global_time(timeline, timeline["total_ms"]) == (
        3, 72 * 3600 * 1000
    )
    assert parse_timestamp("200:12:15", max_position_ms=MAX_TOTAL_MS) == (
        200 * 3600 + 12 * 60 + 15
    ) * 1000
    with pytest.raises(ValueError):
        parse_timestamp("200:12:15")  # File-local seek remains limited to seven days.
    with pytest.raises(ValueError):
        locate_global_time(timeline, -1)
    with pytest.raises(ValueError):
        locate_global_time(timeline, timeline["total_ms"] + 1)


def test_missing_metadata_does_not_invent_playlist_progress():
    files = [{"id": "1", "duration_ms": 0},
             {"id": "2", "duration_ms": 100_000}]
    timeline = playlist_duration(files, "2", 30_000)
    assert timeline["known"] is False
    assert timeline["missing"] == 1
    assert global_position(timeline, "2", 9000) is None
    with pytest.raises(ValueError):
        locate_global_time(timeline, 500)
    fixed = playlist_duration(files, "1", 40_000)
    assert fixed["known"]
    assert global_position(fixed, "1", 50_000) == 40_000
    assert locate_global_time(fixed, 40_000) == (1, 0)


def test_invalid_metadata_and_huge_queues():
    data = [{"id": "a", "duration_ms": -1},
            {"id": "b", "duration_ms": "not a number"}]
    assert playlist_duration(data, "a")["missing"] == 2
    assert not playlist_duration(data * 251, "a")["known"]
    assert playlist_duration([{"id": "x", "duration_ms": 99}], "missing")["parts"][0]["duration_ms"] != 0
    with pytest.raises(ValueError):
        locate_global_time({"known": False, "parts": []}, 0)


def test_chapter_marker_filter_and_bisect():
    chapters = [
        {"start_ms": 0, "end_ms": 90_000, "title": "The Start"},
        {"start_ms": 90_000, "end_ms": 180_000, "title": "Middle"},
        {"start_ms": 180_000, "end_ms": 240_000, "title": "Finish"},
        {"start_ms": -1, "end_ms": 900, "title": "Invalid"},
    ]
    filtered = valid_chapters(chapters)
    assert len(filtered) == 3
    assert chapter_at(filtered, 0) == 0
    assert chapter_at(filtered, 89_999) == 0
    assert chapter_at(filtered, 90_000) == 1
    assert chapter_at(filtered, 200_000) == 2
    assert chapter_at([], 5000) is None
    assert valid_chapters(chapters, 180_000) == filtered[:2]


def test_chapter_browser_search_jump_previous_next(app, fixture_data, tmp_path, monkeypatch):
    dialog, player = view(fixture_data, tmp_path)
    try:
        player._duration = 240_000
        player.durationChanged.emit(240_000)
        monkeypatch.setattr("booktracker.audio_player.probe_m4b", lambda path: {
            "probe_status": "ok",
            "chapters": [
                {"start_ms": 0, "end_ms": 90_000, "title": "Arrival"},
                {"start_ms": 90_000, "end_ms": 180_000, "title": "The Golden Pony"},
                {"start_ms": 180_000, "end_ms": 240_000, "title": "Departure"},
            ],
        })
        dialog.load_chapters()
        assert dialog.navigation_tabs.currentIndex() == 1
        assert dialog.chapter_list.count() == 3
        dialog.chapter_search.setText("pony")
        assert dialog.chapter_list.item(0).isHidden()
        assert not dialog.chapter_list.item(1).isHidden()
        assert dialog.chapter_list.item(2).isHidden()
        dialog.play_chapter_item(dialog.chapter_list.item(1))
        assert player.position() == 90_000
        dialog.next_chapter()
        assert player.position() == 180_000
        dialog.previous_chapter()
        assert player.position() == 90_000
        player.setPosition(100_000)
        dialog.previous_chapter()
        assert player.position() == 90_000
        dialog.select_current_chapter()
        assert dialog.chapter_search.text() == ""
        assert dialog.chapter_list.currentRow() == 1
        assert "▶" in dialog.chapter_list.item(1).text()
    finally:
        dialog.close()


def test_queue_drag_reorder_by_ids_and_persists(app, fixture_data, tmp_path):
    dialog, _player = view(fixture_data, tmp_path)
    try:
        original = [item["id"] for item in dialog.files]
        assert dialog.queue_list.model().moveRows(
            dialog.queue_list.rootIndex(), 2, 1,
            dialog.queue_list.rootIndex(), 0
        )
        # Qt signals rowsMoved after the model update; identity, not list row, wins.
        assert [item["id"] for item in dialog.files] == [
            original[2], original[0], original[1]
        ]
        assert dialog.current["id"] == original[0]
        assert dialog._index == 1
        saved = json.loads(dialog.settings.value(dialog._queue_key))
        assert saved["order"] == [original[2], original[0], original[1]]
        dialog.queue_filter.setText("Part(1)")
        assert not dialog.queue_list.dragEnabled()
        assert dialog.queue_list.item(0).isHidden()
        assert not dialog.queue_list.item(1).isHidden()
        dialog.queue_list.setCurrentRow(0)
        dialog.send_selected_to_end()
        assert dialog.files[-1]["id"] == original[2]
        dialog.queue_filter.clear()
        assert dialog.queue_list.dragEnabled()
        assert not dialog.queue_list.item(0).isHidden()
        assert not dialog.queue_list.item(2).isHidden()
    finally:
        dialog.close()


def test_queue_play_next_remove_restore_and_playback_unchanged(app, fixture_data, tmp_path):
    db, work, _files = fixture_data
    dialog, _player = view(fixture_data, tmp_path)
    try:
        original = [item["id"] for item in dialog.files]
        dialog.queue_list.setCurrentRow(2)
        dialog.play_selected_next()
        assert [item["id"] for item in dialog.files] == [
            original[0], original[2], original[1]
        ]
        dialog.queue_list.setCurrentRow(1)
        dialog.send_selected_to_end()
        assert [item["id"] for item in dialog.files] == [
            original[0], original[1], original[2]
        ]
        dialog.queue_list.setCurrentRow(1)
        dialog.remove_from_queue()
        assert len(dialog.files) == 2
        dialog.restore_queue()
        assert len(dialog.files) == 3
        assert [item["id"] for item in dialog.files] == original
        assert not db.list_sessions(work_id=work)
        assert not db.history(work)
    finally:
        dialog.close()


def test_playlist_position_seek_cross_part_and_unknown_rejected(app, fixture_data, tmp_path):
    db, work, _files = fixture_data
    dialog, media = view(fixture_data, tmp_path)
    try:
        for file in dialog.files:
            file["duration_ms"] = 300_000
        dialog.update_playlist_meter()
        assert dialog.book_seek.isEnabled()
        media._duration = 300_000
        media.durationChanged.emit(300_000)
        media.setPosition(130_000)
        assert "2:10" in dialog.playlist_meter.text()
        dialog.book_timestamp.setText("11:30") # 11:30 beyond 3*5min? no, 690sec = part3+90sec
        dialog.seek_playlist_time()
        assert dialog._index == 2
        # Simulate metadata loading the target part.
        media.durationChanged.emit(300_000)
        assert media.position() == 90_000
        dialog.book_timestamp.setText("40:00")
        dialog.seek_playlist_time()
        assert dialog._index == 2
        assert "outside" in dialog.status.text()
        dialog.files[0]["duration_ms"] = 0
        dialog.update_playlist_meter()
        assert not dialog.book_seek.isEnabled()
        assert "unavailable" in dialog.playlist_meter.text()
        dialog.seek_playlist_time()
        assert "unknown" in dialog.status.text().lower()
        assert not db.list_sessions(work_id=work)
    finally:
        dialog.close()


def test_undo_last_queue_removal_and_restart_persistence(app, fixture_data, tmp_path):
    db, work, _files = fixture_data
    dialog, _player = view(fixture_data, tmp_path)
    try:
        ids = [item["id"] for item in dialog.files]
        dialog.queue_list.setCurrentRow(1)
        dialog.remove_from_queue()
        assert [item["id"] for item in dialog.files] == [ids[0], ids[2]]
        dialog.undo_remove()
        assert [item["id"] for item in dialog.files] == ids
        dialog.undo_remove()
        assert [item["id"] for item in dialog.files] == ids
        dialog.queue_list.setCurrentRow(1)
        dialog.remove_from_queue()
        dialog.restore_queue()
        dialog.undo_remove()
        assert [item["id"] for item in dialog.files] == ids
        assert not db.history(work)
    finally:
        dialog.close()


def test_asynchronous_chapter_scanning_keeps_controls_responsive(
    app, fixture_data, tmp_path, monkeypatch
):
    from time import sleep

    from PySide6.QtCore import QEventLoop, QTimer

    def slow_probe(_path, *, timeout=10):
        sleep(.05)
        return {"probe_status": "ok", "chapters": [
            {"start_ms": 0, "end_ms": 10_000, "title": "Part start"}
        ]}

    monkeypatch.setattr("booktracker.audio_player.probe_m4b", slow_probe)
    dialog, _player = view(fixture_data, tmp_path)
    try:
        dialog.start_chapter_probe()
        assert dialog._chapter_worker is not None
        assert not dialog.chapter_load_button.isEnabled()
        assert "background" in dialog.chapter_summary.text()
        worker = dialog._chapter_worker
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        app.processEvents()
        assert dialog.chapter_load_button.isEnabled()
        assert dialog.chapter_list.count() == 1
        assert dialog._chapter_worker is None
    finally:
        dialog.close()


def test_stale_async_chapter_result_discarded_after_part_change(
    app, fixture_data, tmp_path, monkeypatch
):
    from time import sleep

    from PySide6.QtCore import QEventLoop, QTimer

    def slow_probe(_path, *, timeout=10):
        sleep(.05)
        return {"probe_status": "ok", "chapters": [
            {"start_ms": 0, "end_ms": 10_000, "title": "Old chapter"}
        ]}

    monkeypatch.setattr("booktracker.audio_player.probe_m4b", slow_probe)
    dialog, _player = view(fixture_data, tmp_path)
    try:
        dialog.start_chapter_probe()
        worker = dialog._chapter_worker
        dialog.change_part(1)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        app.processEvents()
        assert dialog.chapter_list.count() == 0
        assert dialog._loaded_chapters == []
        assert dialog.chapter_load_button.isEnabled()
    finally:
        dialog.close()
