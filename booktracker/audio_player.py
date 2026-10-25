"""Opt-in local M4B playback with per-file, device-local resume locations.

QtMultimedia decodes media using the installed platform backend. Playback position
is NOT a verified listening session and never becomes a progress milestone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .audio_gui import fmt_duration
from .audio_navigation import (
    MAX_TOTAL_MS,
    chapter_at,
    global_position,
    locate_global_time,
    playlist_duration,
    valid_chapters,
)
from .audiobooks import probe_m4b

MAX_AUDIO_POSITION_MS = 7 * 24 * 3600 * 1000


class AudioResumeStore:
    """Avoid stale resumes for files moved/replaced since their last play."""

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("BookTracker", "BookTracker")

    @staticmethod
    def _prefix(file_id):
        import uuid

        try:
            return "player_resume/" + str(uuid.UUID(str(file_id)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("Invalid linked audiobook ID.") from exc

    @staticmethod
    def _identity(path):
        file = Path(path).expanduser().resolve(strict=True)
        if not file.is_file() or file.suffix.casefold() != ".m4b":
            raise ValueError("A linked M4B file must be available for playback.")
        info = file.stat()
        return str(file), info.st_size, info.st_mtime_ns

    @staticmethod
    def _stamp(identity):
        import json

        return json.dumps(identity, ensure_ascii=False, separators=(",", ":"))

    def position(self, file_id, path, duration_ms=0):
        try:
            key = self._prefix(file_id)
            identity = self._identity(path)
        except (ValueError, OSError):
            return 0
        if self.settings.value(key + "/identity") != self._stamp(identity):
            return 0
        try:
            pos = int(self.settings.value(key + "/position", 0))
        except (TypeError, ValueError):
            return 0
        if not 0 <= pos <= MAX_AUDIO_POSITION_MS:
            return 0
        duration = int(duration_ms or 0)
        return min(pos, max(0, duration - 1000)) if duration > 0 else pos

    def save(self, file_id, path, position_ms):
        key = self._prefix(file_id)
        identity = self._identity(path)
        value = int(position_ms)
        if not 0 <= value <= MAX_AUDIO_POSITION_MS:
            raise ValueError("Invalid audio playback position.")
        self.settings.setValue(key + "/identity", self._stamp(identity))
        self.settings.setValue(key + "/position", value)

    def clear(self, file_id):
        self.settings.remove(self._prefix(file_id))


def playlist_for_file(files, selected):
    """Only sequence compatible parts within one work/narrator and edition type."""
    if not selected or not selected.get("available"):
        raise ValueError("Select a locally available audiobook first.")
    # A full edition or unnumbered standalone file is a single recording,
    # not one part in a sequence of other editions/standalone exports.
    if not selected.get("part_number"):
        return [selected]
    parts = [
        item for item in files
        if item.get("available") and item["work_id"] == selected["work_id"]
        and item["narrator"] == selected["narrator"]
        and bool(item["is_complete"]) == bool(selected["is_complete"])
        and bool(item["part_number"]) == bool(selected["part_number"])
    ]
    if selected["id"] not in {item["id"] for item in parts}:
        raise ValueError("Selected audiobook is not part of its available playlist.")
    return sorted(parts, key=lambda item: (
        item["part_number"] if item["part_number"] else 0,
        item["filename"].casefold(), item["id"],
    ))


def parse_timestamp(value, *, max_position_ms=MAX_AUDIO_POSITION_MS):
    """An explicit media timestamp, not a listening-session duration."""
    value = str(value).strip()
    chunks = value.split(":")
    if not 1 <= len(chunks) <= 3 or any(
        not chunk.isascii() or not chunk.isdecimal() or not chunk
        for chunk in chunks
    ):
        raise ValueError("Enter a time such as 2:35 or 1:02:35.")
    numbers = list(map(int, chunks))
    if len(numbers) == 3 and (numbers[1] >= 60 or numbers[2] >= 60):
        raise ValueError("Minutes and seconds must be below 60.")
    if len(numbers) == 2 and numbers[1] >= 60:
        raise ValueError("Seconds must be below 60.")
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    result = seconds * 1000
    if result > max_position_ms:
        raise ValueError("Timestamp exceeds the supported maximum.")
    return result


def time_label(position, duration):
    return f"{fmt_duration(position)} / {fmt_duration(duration)}"


class ChapterProbeThread(QThread):
    """ffprobe can take several seconds on large M4Bs; never block playback."""

    found = Signal(str, object)

    def __init__(self, file_id, path, parent=None):
        super().__init__(parent)
        self.file_id = file_id
        self.path = str(path)
        file = Path(path).resolve(strict=True)
        info = file.stat()
        self.file_stamp = (str(file), info.st_size, info.st_mtime_ns)

    def run(self):
        try:
            info = probe_m4b(self.path, timeout=10)
        except (OSError, RuntimeError, ValueError) as exc:
            info = {"probe_status": str(exc), "chapters": []}
        self.found.emit(self.file_id, info)


class AudioPlayerDialog(QDialog):
    """Local media playback. Never infer time spent from playback state."""

    updated = Signal()

    def __init__(self, parent, files, selected, *, settings=None, player=None, audio_output=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings("BookTracker", "BookTracker")
        self.resume = AudioResumeStore(self.settings)
        self._all_files = playlist_for_file(files, selected)
        self.files = list(self._all_files)
        group = (selected["work_id"], selected["narrator"],
                 bool(selected["is_complete"]), bool(selected["part_number"]))
        self._queue_key = ("player_queue/" + hashlib.sha256(
            json.dumps(group, ensure_ascii=False).encode("utf-8")
        ).hexdigest())
        self._restore_queue_order()
        if not any(item["id"] == selected["id"] for item in self.files):
            # Launching a previously removed part explicitly requeues it.
            self.files.insert(0, selected)
            self._save_queue()
        self._index = next(i for i, item in enumerate(self.files) if item["id"] == selected["id"])
        self._pending_resume = 0
        self._loading = False
        self._closed = False
        self._finished_file = False
        self._loaded_chapters = []
        self._current_chapter_index = None
        self._chapter_worker = None
        self._last_removed = None
        self._advance_scheduled = False
        self._mpv_engine = None
        self._supports_pitch_safe_speed = True
        self._sleep_deadline = None
        self.setWindowTitle("BookTracker — Audiobook player")
        self.resize(820, 710)
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        root.addWidget(self.title)
        root.addWidget(QLabel(
            "Playback position is saved on this computer only. It does not log "
            "listening minutes, chapters, or reading milestones."
        ))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderReleased.connect(self.seek_released)
        root.addWidget(self.seek)
        self.clock = QLabel("0:00 / 0:00")
        root.addWidget(self.clock)
        self.playlist_meter = QLabel("Playlist: waiting for duration metadata")
        self.playlist_meter.setWordWrap(True)
        root.addWidget(self.playlist_meter)
        global_seek = QHBoxLayout()
        self.book_timestamp = QLineEdit()
        self.book_timestamp.setPlaceholderText("Playlist time 25:30:00")
        self.book_timestamp.setMaxLength(14)
        self.book_timestamp.setToolTip(
            "Cumulative media time across the queued parts. Only available "
            "when all queued parts have verified duration metadata."
        )
        self.book_timestamp.returnPressed.connect(self.seek_playlist_time)
        global_seek.addWidget(QLabel("Go to playlist time"))
        global_seek.addWidget(self.book_timestamp)
        self.book_seek = self._button("Go", self.seek_playlist_time)
        global_seek.addWidget(self.book_seek)
        global_seek.addStretch()
        root.addLayout(global_seek)
        buttons = QHBoxLayout()
        self.previous = self._button("Previous part", lambda: self.change_part(-1))
        self.back = self._button("−30 sec", lambda: self.jump(-30000))
        self.play_button = self._button("Play", self.toggle_play)
        self.forward = self._button("+30 sec", lambda: self.jump(30000))
        self.next = self._button("Next part", lambda: self.change_part(1))
        for btn in (self.previous, self.back, self.play_button, self.forward, self.next):
            buttons.addWidget(btn)
        root.addLayout(buttons)
        playback_actions = QHBoxLayout()
        playback_actions.addWidget(self._button("Restart this part", self.restart_part))
        self.auto_next = QCheckBox("Automatically play next part")
        self.auto_next.setChecked(self.settings.value("player_auto_next", False, type=bool))
        self.auto_next.toggled.connect(
            lambda enabled: self.settings.setValue("player_auto_next", bool(enabled))
        )
        playback_actions.addWidget(self.auto_next)
        playback_actions.addStretch()
        root.addLayout(playback_actions)
        options = QHBoxLayout()
        options.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        for rate in (0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5):
            self.speed.addItem(f"{rate:g}×", rate)
        try:
            saved_speed = float(self.settings.value("player_speed", 1.0))
        except (TypeError, ValueError):
            saved_speed = 1.0
        self.speed.setCurrentIndex(max(0, self.speed.findData(saved_speed)))
        self.speed.currentIndexChanged.connect(self.change_speed)
        options.addWidget(self.speed)
        options.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        try:
            saved_volume = int(self.settings.value("player_volume", 75))
        except (ValueError, TypeError):
            saved_volume = 75
        self.volume.setValue(max(0, min(100, saved_volume)))
        options.addWidget(self.volume)
        self.chapters = QComboBox()
        self.chapters.addItem("Chapter markers (load on request)", -1)
        self.chapters.activated.connect(self.seek_chapter)
        options.addWidget(self.chapters, stretch=1)
        options.addWidget(self._button("Load markers", self.start_chapter_probe))
        root.addLayout(options)
        long_form = QHBoxLayout()
        self.goto_time = QLineEdit()
        self.goto_time.setPlaceholderText("Jump to 1:02:35")
        self.goto_time.setMaximumWidth(150)
        self.goto_time.setMaxLength(12)
        self.goto_time.returnPressed.connect(self.jump_to_timestamp)
        long_form.addWidget(self.goto_time)
        long_form.addWidget(self._button("Go to time", self.jump_to_timestamp))
        long_form.addWidget(self._button("−5 min", lambda:self.jump(-300_000)))
        long_form.addWidget(self._button("+5 min", lambda:self.jump(300_000)))
        long_form.addStretch()
        root.addLayout(long_form)
        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("Sleep timer"))
        self.sleep = QComboBox()
        for text, minutes in (
            ("Off", 0), ("10 min", 10), ("15 min", 15),
            ("20 min", 20), ("30 min", 30), ("45 min", 45),
            ("60 min", 60), ("90 min", 90)
        ):
            self.sleep.addItem(text, minutes)
        self.sleep.currentIndexChanged.connect(self.configure_sleep)
        sleep_row.addWidget(self.sleep)
        self.sleep_label = QLabel("Off")
        sleep_row.addWidget(self.sleep_label, stretch=1)
        root.addLayout(sleep_row)
        self.navigation_tabs = QTabWidget()
        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(4, 4, 4, 4)
        queue_layout.addWidget(QLabel(
            "Queue order is saved on this computer. Drag parts to reorder."
        ))
        self.queue_filter = QLineEdit()
        self.queue_filter.setPlaceholderText("Find part number or filename…")
        self.queue_filter.textChanged.connect(self.filter_queue)
        queue_layout.addWidget(self.queue_filter)
        self.queue_list = QListWidget()
        self.queue_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.queue_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.queue_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.queue_list.setMinimumHeight(145)
        self.queue_list.itemDoubleClicked.connect(self.play_queue_item)
        self.queue_list.model().rowsMoved.connect(self.sync_queue_from_view)
        queue_layout.addWidget(self.queue_list, stretch=1)
        queue_buttons = QHBoxLayout()
        for text, callback in (
            ("▲", lambda: self.move_queue(-1)),
            ("▼", lambda: self.move_queue(1)),
            ("Play next", self.play_selected_next),
            ("To end", self.send_selected_to_end),
            ("Remove", self.remove_from_queue),
            ("Undo", self.undo_remove),
            ("Restore", self.restore_queue),
        ):
            queue_buttons.addWidget(self._button(text, callback))
        queue_layout.addLayout(queue_buttons)
        self.navigation_tabs.addTab(queue_page, "Play queue")
        chapter_page = QWidget()
        chapter_layout = QVBoxLayout(chapter_page)
        chapter_layout.setContentsMargins(4, 4, 4, 4)
        chapter_controls = QHBoxLayout()
        self.chapter_search = QLineEdit()
        self.chapter_search.setPlaceholderText("Search chapter titles or numbers…")
        self.chapter_search.textChanged.connect(self.filter_chapters)
        chapter_controls.addWidget(self.chapter_search, stretch=1)
        self.chapter_load_button = self._button(
            "Load markers", self.start_chapter_probe
        )
        chapter_controls.addWidget(self.chapter_load_button)
        chapter_layout.addLayout(chapter_controls)
        self.chapter_summary = QLabel("Chapter markers are loaded only on request.")
        chapter_layout.addWidget(self.chapter_summary)
        self.chapter_list = QListWidget()
        self.chapter_list.setMinimumHeight(145)
        self.chapter_list.itemDoubleClicked.connect(self.play_chapter_item)
        chapter_layout.addWidget(self.chapter_list, stretch=1)
        chapter_nav = QHBoxLayout()
        for text,callback in (
            ("◀ Previous chapter", self.previous_chapter),
            ("Current chapter", self.select_current_chapter),
            ("Next chapter ▶", self.next_chapter),
        ):
            chapter_nav.addWidget(self._button(text, callback))
        chapter_layout.addLayout(chapter_nav)
        self.navigation_tabs.addTab(chapter_page, "Chapters")
        root.addWidget(self.navigation_tabs, stretch=1)
        root.addWidget(self._button("Close player", self.close))
        if player is None and audio_output is None:
            from .mpv_player import MpvOutput, MpvPlayer, pitch_safe_backend

            if pitch_safe_backend():
                self.player = MpvPlayer(self)
                self._mpv_engine = self.player
                self.output = MpvOutput(self.player)
                self.status.setText("Pitch-preserving audio engine: mpv")
            else:
                # Qt 6.8 QMediaPlayer changes pitch when rate changes. Never
                # silently use that behavior when no pitch-safe engine exists.
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

                self.player = QMediaPlayer(self)
                self.output = QAudioOutput(self)
                self._supports_pitch_safe_speed = False
        else:
            self.player, self.output = player, audio_output
        if not self._supports_pitch_safe_speed:
            self.speed.setCurrentIndex(self.speed.findData(1.0))
            self.speed.setEnabled(False)
            self.speed.setToolTip("Install mpv for pitch-preserving variable speed.")
            self.settings.setValue("player_speed", 1.0)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(self.volume.value() / 100)
        self.volume.valueChanged.connect(self.change_volume)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.playbackStateChanged.connect(self.update_playback)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.errorOccurred.connect(self.on_error)
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(4000)
        self._save_timer.timeout.connect(self.save_position)
        self._save_timer.start()
        self._sleep_tick = QTimer(self)
        self._sleep_tick.setInterval(1000)
        self._sleep_tick.timeout.connect(self.update_sleep)
        self.load_part()
        self.refresh_queue()

    @staticmethod
    def _button(title, callback):
        widget = QPushButton(title)
        widget.clicked.connect(callback)
        return widget

    def _restore_queue_order(self):
        raw = str(self.settings.value(self._queue_key, ""))
        try:
            saved = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(saved, dict):
            return
        identifiers = saved.get("order")
        known = saved.get("known")
        if (not isinstance(identifiers, list) or len(identifiers) > 500
                or not isinstance(known, list) or len(known) > 500):
            return
        records = {item["id"]: item for item in self._all_files}
        arranged = []
        seen = set()
        for identifier in identifiers:
            if not isinstance(identifier, str):
                continue
            if identifier in records and identifier not in seen:
                arranged.append(records[identifier])
                seen.add(identifier)
        known_ids = {key for key in known if isinstance(key,str)}
        # Only append genuinely new files; intentionally removed IDs stay out.
        self.files = arranged + [item for item in self._all_files
                                 if item["id"] not in seen and item["id"] not in known_ids]
        if not self.files:
            self.files = list(self._all_files)

    def _save_queue(self):
        self.settings.setValue(self._queue_key, json.dumps({
            "order": [item["id"] for item in self.files],
            "known": [item["id"] for item in self._all_files],
        }))

    def refresh_queue(self):
        if not hasattr(self, "queue_list"):
            return
        selected = self.queue_list.currentItem()
        selected_id = (selected.data(Qt.ItemDataRole.UserRole)
                       if selected else self.current["id"])
        self.queue_list.clear()
        for idx, item in enumerate(self.files):
            text = f"{idx + 1}. {item['filename']}"
            if idx == self._index:
                text = "▶ " + text
            duration = int(item.get("duration_ms") or 0)
            if duration > 0:
                text += f"  ·  {fmt_duration(duration)}"
            widget = QListWidgetItem(text)
            widget.setData(Qt.ItemDataRole.UserRole, item["id"])
            widget.setToolTip(item["filename"])
            self.queue_list.addItem(widget)
        self.filter_queue()
        for row in range(self.queue_list.count()):
            if self.queue_list.item(row).data(Qt.ItemDataRole.UserRole) == selected_id:
                self.queue_list.setCurrentRow(row)
                break
        self.update_playlist_meter()

    def filter_queue(self, *_args):
        query = self.queue_filter.text().strip().casefold()
        # Dragging in a filtered view can obscure the insertion destination.
        self.queue_list.setDragEnabled(not bool(query))
        for row in range(self.queue_list.count()):
            item = self.queue_list.item(row)
            item.setHidden(query not in item.text().casefold())

    def sync_queue_from_view(self, *_args):
        """Synchronize Qt drag/drop order using immutable IDs, not row indices."""
        if self.queue_list.count() != len(self.files):
            return
        identifiers = [self.queue_list.item(i).data(Qt.ItemDataRole.UserRole)
                       for i in range(self.queue_list.count())]
        previous = {item["id"]: item for item in self.files}
        if len(set(identifiers)) != len(self.files) or set(identifiers) != set(previous):
            self.refresh_queue()
            return
        active = self.current["id"]
        self.files = [previous[identifier] for identifier in identifiers]
        self._index = identifiers.index(active)
        self._save_queue()
        self.refresh_queue()
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.updated.emit()

    def _selected_queue_index(self):
        item = self.queue_list.currentItem()
        if item is None:
            return None
        target = item.data(Qt.ItemDataRole.UserRole)
        return next((i for i, row in enumerate(self.files)
                     if row["id"] == target), None)

    def play_selected_next(self):
        index = self._selected_queue_index()
        if index is not None:
            self.queue_selected_next(self.files[index])

    def send_selected_to_end(self):
        index = self._selected_queue_index()
        if index is None or index == len(self.files) - 1:
            return
        selected_id = self.files[index]["id"]
        active_id = self.current["id"]
        self.files.append(self.files.pop(index))
        self._index = next(i for i, item in enumerate(self.files)
                           if item["id"] == active_id)
        self._save_queue()
        self.refresh_queue()
        for row in range(self.queue_list.count()):
            if self.queue_list.item(row).data(Qt.ItemDataRole.UserRole) == selected_id:
                self.queue_list.setCurrentRow(row)
                break
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.updated.emit()

    def play_queue_item(self, item):
        identifier = item.data(Qt.ItemDataRole.UserRole)
        target = next((i for i, row in enumerate(self.files)
                       if row["id"] == identifier), None)
        if target is not None and target != self._index:
            self.change_part_to(target)

    def change_part_to(self, target):
        if not 0 <= target < len(self.files) or target == self._index:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        was_playing=(
            self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        )
        self.save_position()
        self._index = target
        self.load_part()
        if was_playing:
            self.player.play()

    def move_queue(self, delta):
        row = self._selected_queue_index()
        if row is None:
            return
        target = row + delta
        if not 0 <= target < len(self.files):
            return
        active_id = self.current["id"]
        self.files[row], self.files[target] = self.files[target], self.files[row]
        self._index = next(i for i, item in enumerate(self.files)
                           if item["id"] == active_id)
        self._save_queue()
        self.refresh_queue()
        self.queue_list.setCurrentRow(target)
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.updated.emit()

    def remove_from_queue(self):
        row = self._selected_queue_index()
        if row is None:
            return
        if row == self._index:
            self.status.setText("Cannot remove the currently playing file from its queue.")
            return
        self._last_removed = (self.files[row]["id"], row)
        self.files.pop(row)
        if row < self._index:
            self._index -= 1
        self._save_queue()
        self.refresh_queue()
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.updated.emit()

    def undo_remove(self):
        if self._last_removed is None:
            self.status.setText("No removed queue entry to restore.")
            return
        file_id, original_index = self._last_removed
        self._last_removed = None
        if any(item["id"] == file_id for item in self.files):
            return
        item = next((item for item in self._all_files if item["id"] == file_id), None)
        if item is None:
            return
        active_id = self.current["id"]
        self.files.insert(min(original_index, len(self.files)), item)
        self._index = next(i for i, part in enumerate(self.files)
                           if part["id"] == active_id)
        self._save_queue()
        self.refresh_queue()
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.status.setText("Restored the last removed queue entry.")
        self.updated.emit()

    def restore_queue(self):
        self._last_removed = None
        selected_id = self.current["id"]
        self.files = list(self._all_files)
        self._index = next(i for i, item in enumerate(self.files)
                           if item["id"] == selected_id)
        self._save_queue()
        self.refresh_queue()
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.updated.emit()

    def queue_selected_next(self, selected):
        """Insert a file from this one narrator and edition, never mix collections."""
        if not self.current.get("part_number") or not selected.get("part_number"):
            return False
        if not selected.get("available"):
            return False
        first = self.current
        if (selected["work_id"] != first["work_id"]
                or selected["narrator"] != first["narrator"]
                or bool(selected["is_complete"]) != bool(first["is_complete"])
                or bool(selected["part_number"]) != bool(first["part_number"])):
            return False
        identifier = selected["id"]
        if identifier == self.current["id"]:
            return False
        index = next((i for i, row in enumerate(self.files)
                      if row["id"] == identifier), None)
        if index is not None:
            self.files.pop(index)
            if index < self._index:
                self._index -= 1
        if len(self.files) >= 500:
            return False
        self.files.insert(self._index + 1, selected)
        self._save_queue()
        self.refresh_queue()
        self.status.setText("Added to play next (this device only).")
        return True

    @property
    def current(self):
        return self.files[self._index]

    def load_part(self):
        self._loading = True
        self._finished_file = False
        item = self.current
        self._loaded_chapters = []
        self._current_chapter_index = None
        self.chapter_list.clear()
        self.chapter_summary.setText("Chapter markers are loaded only on request.")
        self.chapters.clear()
        self.chapters.addItem("Chapter markers (load on request)", -1)
        self.title.setText(
            f"{item['work_title']} · {item['filename']} "
            f"({self._index + 1}/{len(self.files)})"
        )
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self._pending_resume = self.resume.position(item["id"], item["path"])
        self.seek.setValue(0)
        self.seek.setMaximum(0)
        self.clock.setText("0:00 / 0:00")
        self.status.setText(
            f"Ready. Local resume: {fmt_duration(self._pending_resume)}. "
            "Press Play to start playback."
        )
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(item["path"]))
        self.change_speed()
        self._loading = False
        self.refresh_queue()
        self.updated.emit()

    def change_part(self, offset):
        self.change_part_to(self._index + offset)

    def toggle_play(self):
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.save_position()
        else:
            self.player.play()

    def update_playback(self, state):
        if self._closed:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        self.play_button.setText(
            "Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"
        )
        self.updated.emit()

    def update_duration(self, duration):
        if self._closed or self._loading:
            return
        self.seek.setMaximum(min(max(0, int(duration)), MAX_AUDIO_POSITION_MS))
        if self._pending_resume and duration > 0:
            resume_pos = min(self._pending_resume, max(0, int(duration) - 1000))
            self._pending_resume = 0
            self.player.setPosition(resume_pos)
        self.update_position(self.player.position())

    def update_position(self, position):
        if self._closed or self._loading:
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(max(0, int(position)))
        self.clock.setText(time_label(position, self.player.duration()))
        self.update_playlist_meter()
        self.highlight_current_chapter()
        self.updated.emit()

    def restart_part(self):
        self._finished_file = False
        self._pending_resume = 0
        self.resume.clear(self.current["id"])
        self.player.setPosition(0)
        self.status.setText("Restarted this part. Previous local resume was cleared.")

    def seek_released(self):
        self._finished_file = False
        self.player.setPosition(self.seek.value())
        self.save_position()

    def seek_to_position(self, position_ms):
        """Seek on an explicit command; if loading, defer to loaded duration."""
        position = int(position_ms)
        if not 0 <= position <= MAX_AUDIO_POSITION_MS:
            raise ValueError("Invalid audio timestamp.")
        self._finished_file = False
        if self.player.duration() <= 0:
            self._pending_resume = position
        else:
            self._pending_resume = 0
            self.player.setPosition(min(position, self.player.duration()))
            self.save_position()

    def jump_to_timestamp(self):
        try:
            value = parse_timestamp(self.goto_time.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        duration = self.player.duration()
        if duration > 0 and value > duration:
            self.status.setText("Requested timestamp exceeds this audio part.")
            return
        self.seek_to_position(value)
        self.status.setText(f"Moved to {fmt_duration(value)} in this audio file.")

    def configure_sleep(self, *_args):
        minutes = int(self.sleep.currentData() or 0)
        if minutes:
            self._sleep_deadline = monotonic() + minutes * 60
            if hasattr(self, "_sleep_tick"):
                self._sleep_tick.start()
        else:
            self._sleep_deadline = None
            if hasattr(self, "_sleep_tick"):
                self._sleep_tick.stop()
        self.update_sleep()

    def update_sleep(self):
        if self._closed:
            return
        if self._sleep_deadline is None:
            self.sleep_label.setText("Off")
            return
        seconds = max(0, int(self._sleep_deadline - monotonic() + 0.999))
        if seconds == 0:
            self._sleep_deadline = None
            self._sleep_tick.stop()
            self.sleep.blockSignals(True)
            self.sleep.setCurrentIndex(0)
            self.sleep.blockSignals(False)
            self.sleep_label.setText("Timer finished — playback paused")
            self.player.pause()
            self.save_position()
            return
        minutes, remaining = divmod(seconds, 60)
        self.sleep_label.setText(f"Pauses in {minutes}:{remaining:02d}")

    def jump(self, offset):
        duration = int(self.player.duration())
        if duration <= 0:
            return
        self._finished_file = False
        self.player.setPosition(max(0, min(duration, self.player.position() + offset)))
        self.save_position()

    def change_speed(self, *_args):
        value = float(self.speed.currentData()) if self._supports_pitch_safe_speed else 1.0
        self.player.setPlaybackRate(value)
        self.settings.setValue("player_speed", value)

    def change_volume(self, number):
        self.output.setVolume(number / 100)
        self.settings.setValue("player_volume", number)

    def start_chapter_probe(self):
        if self._closed or self._chapter_worker is not None:
            return
        if not Path(self.current["path"]).is_file():
            self.status.setText("The selected M4B is no longer available.")
            return
        try:
            worker = ChapterProbeThread(
                self.current["id"], self.current["path"], self
            )
        except (ValueError, OSError) as exc:
            self.status.setText(f"Cannot read M4B chapter markers: {exc}")
            return
        self._chapter_worker = worker
        self.chapter_load_button.setEnabled(False)
        self.chapter_summary.setText("Reading chapter markers in the background…")
        worker.found.connect(self._chapter_probe_result)
        worker.finished.connect(self._chapter_probe_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _chapter_probe_result(self, file_id, result):
        if self._closed or self.current["id"] != file_id:
            return
        try:
            file = Path(self.current["path"]).resolve(strict=True)
            info = file.stat()
            stamp = (str(file), info.st_size, info.st_mtime_ns)
        except (ValueError, OSError):
            stamp = None
        if stamp != self._chapter_worker.file_stamp:
            self.chapter_summary.setText("File changed during metadata scan. Reload markers.")
            return
        self.load_chapters(result)

    def _chapter_probe_finished(self):
        self._chapter_worker = None
        if not self._closed:
            self.chapter_load_button.setEnabled(True)

    def load_chapters(self, data=None):
        if not Path(self.current["path"]).is_file():
            self.status.setText("The selected M4B is no longer available.")
            return
        if data is None:
            data = probe_m4b(self.current["path"])
        if data.get("probe_status") != "ok" or not data.get("chapters"):
            self.chapter_summary.setText("No readable chapter markers in this M4B.")
            self.status.setText("This M4B has no readable chapter markers.")
            return
        self._loaded_chapters = valid_chapters(
            data["chapters"], self.player.duration()
        )
        if not self._loaded_chapters:
            self.chapter_summary.setText("No valid chapter markers in this M4B.")
            return
        self.chapters.clear()
        self.chapters.addItem("Jump to chapter…", -1)
        self.chapter_list.clear()
        for idx, ch in enumerate(self._loaded_chapters):
            title = f"{idx + 1}. {ch['title']} ({fmt_duration(ch['start_ms'])})"
            self.chapters.addItem(title, ch["start_ms"])
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setToolTip(
                f"From {fmt_duration(ch['start_ms'])} to {fmt_duration(ch['end_ms'])}"
            )
            self.chapter_list.addItem(item)
        self.filter_chapters()
        self.highlight_current_chapter()
        self.chapter_summary.setText(
            f"{len(self._loaded_chapters):,} markers in this audio part"
        )
        self.navigation_tabs.setCurrentIndex(1)
        self.status.setText(f"Loaded {len(self._loaded_chapters)} chapter markers.")

    def filter_chapters(self, *_args):
        query = self.chapter_search.text().strip().casefold()
        for row in range(self.chapter_list.count()):
            item = self.chapter_list.item(row)
            item.setHidden(query not in item.text().casefold())

    def play_chapter_item(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int) or not 0 <= idx < len(self._loaded_chapters):
            return
        self.seek_to_position(self._loaded_chapters[idx]["start_ms"])

    def highlight_current_chapter(self):
        idx = chapter_at(self._loaded_chapters, self.player.position())
        if idx == self._current_chapter_index:
            return
        old = self._current_chapter_index
        self._current_chapter_index = idx
        for row in (old, idx):
            if row is None or not 0 <= row < self.chapter_list.count():
                continue
            item = self.chapter_list.item(row)
            item.setText(
                ("▶ " if row == idx else "") + f"{row + 1}. "
                + self._loaded_chapters[row]["title"] + " ("
                + fmt_duration(self._loaded_chapters[row]["start_ms"]) + ")"
            )
        self.filter_chapters()
        if idx is not None:
            self.chapter_summary.setText(
                f"Chapter {idx + 1}/{len(self._loaded_chapters)}: "
                f"{self._loaded_chapters[idx]['title']}"
            )

    def select_current_chapter(self):
        index = chapter_at(self._loaded_chapters, self.player.position())
        if index is not None:
            self.chapter_search.clear()
            self.chapter_list.setCurrentRow(index)
            self.chapter_list.scrollToItem(self.chapter_list.item(index))

    def next_chapter(self):
        index = chapter_at(self._loaded_chapters, self.player.position())
        target = 0 if index is None else index + 1
        if 0 <= target < len(self._loaded_chapters):
            self.seek_to_position(self._loaded_chapters[target]["start_ms"])

    def previous_chapter(self):
        index = chapter_at(self._loaded_chapters, self.player.position())
        if index is None:
            return
        start = self._loaded_chapters[index]["start_ms"]
        target = index - 1 if self.player.position() <= start + 3000 else index
        if target >= 0:
            self.seek_to_position(self._loaded_chapters[target]["start_ms"])

    def seek_chapter(self, *_args):
        position = self.chapters.currentData()
        if position is not None and 0 <= int(position) <= MAX_AUDIO_POSITION_MS:
            self.seek_to_position(int(position))

    def update_playlist_meter(self):
        """Only display cumulative playback time when all durations are known."""
        if not hasattr(self, "playlist_meter") or not hasattr(self, "player"):
            return
        timeline = playlist_duration(self.files, self.current["id"],
                                     self.player.duration())
        self.book_timestamp.setEnabled(timeline["known"])
        self.book_seek.setEnabled(timeline["known"])
        if not timeline["known"]:
            if timeline["missing"] == 0:
                reason = "playlist duration exceeds the one-year seek limit"
            else:
                reason = f"{timeline['missing']} missing duration(s)"
            self.playlist_meter.setText(
                f"Playlist: {len(self.files)} part(s); {reason}. "
                "Book-wide seeking unavailable."
            )
            return
        position = global_position(timeline, self.current["id"],
                                   self.player.position())
        remaining = max(0, timeline["total_ms"] - position)
        self.playlist_meter.setText(
            f"Playlist media time: {fmt_duration(position)} / "
            f"{fmt_duration(timeline['total_ms'])} "
            f"· remaining {fmt_duration(remaining)} "
            f"· part {self._index + 1} of {len(self.files)}"
        )

    def seek_playlist_time(self):
        """Jump by cumulative imported media duration, never by reading progress."""
        timeline = playlist_duration(self.files, self.current["id"],
                                     self.player.duration())
        if not timeline["known"]:
            self.status.setText("Cannot seek playlist: one or more part durations unknown.")
            return
        try:
            position = parse_timestamp(
                self.book_timestamp.text(), max_position_ms=MAX_TOTAL_MS
            )
            idx, file_time = locate_global_time(timeline, position)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if idx != self._index:
            self.change_part_to(idx)
        self.seek_to_position(file_time)
        self.status.setText(
            f"Moved to playlist time {fmt_duration(position)} "
            f"(part {idx + 1} at {fmt_duration(file_time)})."
        )

    def on_media_status(self, state):
        from PySide6.QtMultimedia import QMediaPlayer

        if self._closed:
            return
        if state == QMediaPlayer.MediaStatus.EndOfMedia and not self._loading:
            self._finished_file = True
            self.resume.clear(self.current["id"])
            self._pending_resume = 0
            self.status.setText("Reached the end. Playback position cleared.")
            if (self.auto_next.isChecked() and self._index + 1 < len(self.files)
                    and not self._advance_scheduled):
                # Defer changing the media source until Qt has returned from
                # the active end-of-media signal delivery.
                self._advance_scheduled = True
                QTimer.singleShot(0, self.advance_after_end)
        elif state == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status.setText("Cannot decode this M4B. Try the external player.")

    def advance_after_end(self):
        self._advance_scheduled = False
        if self._closed or not self.auto_next.isChecked():
            return
        if self._finished_file and self._index + 1 < len(self.files):
            self.change_part(1)
            self.player.play()

    def on_error(self, *_args):
        if self._closed:
            return
        self.status.setText(
            f"Playback error: {self.player.errorString()}. "
            "The external-player option remains available."
        )

    def save_position(self):
        if self._closed or self._loading or self._finished_file:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            return
        pos = self.player.position()
        if not 0 <= pos <= MAX_AUDIO_POSITION_MS:
            return
        try:
            self.resume.save(self.current["id"], self.current["path"], pos)
        except (ValueError, OSError):
            self.status.setText("Playback resume position could not be saved.")

    def closeEvent(self, event):
        worker = self._chapter_worker
        # ffprobe itself has a ten-second timeout. Keep its QThread
        # alive until done so Qt cannot destroy a running worker.
        if worker is not None and worker.isRunning() and not worker.wait(12_000):
            event.ignore()
            self.status.setText(
                "Finishing the chapter scan before closing the player."
            )
            return
        self._save_timer.stop()
        self._sleep_tick.stop()
        self.save_position()
        self._closed = True
        self.player.stop()
        self.player.setSource(QUrl())
        if self._mpv_engine is not None:
            self._mpv_engine.shutdown()
        super().closeEvent(event)
