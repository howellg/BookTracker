"""Opt-in local M4B playback with per-file, device-local resume locations.

QtMultimedia decodes media using the installed platform backend. Playback position
is NOT a verified listening session and never becomes a progress milestone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from .audio_gui import fmt_duration
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


def time_label(position, duration):
    return f"{fmt_duration(position)} / {fmt_duration(duration)}"


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
        self._advance_scheduled = False
        self._mpv_engine = None
        self._supports_pitch_safe_speed = True
        self.setWindowTitle("BookTracker — Audiobook player")
        self.resize(680, 330)
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
        options.addWidget(self._button("Load markers", self.load_chapters))
        root.addLayout(options)
        root.addWidget(QLabel("Play queue (double-click to play a part)"))
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(120)
        self.queue_list.itemDoubleClicked.connect(self.play_queue_item)
        root.addWidget(self.queue_list)
        queue_buttons = QHBoxLayout()
        for text, callback in (
            ("Move up", lambda: self.move_queue(-1)),
            ("Move down", lambda: self.move_queue(1)),
            ("Remove", self.remove_from_queue),
            ("Restore sequence", self.restore_queue),
        ):
            queue_buttons.addWidget(self._button(text, callback))
        root.addLayout(queue_buttons)
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
        prior = self.queue_list.currentRow()
        self.queue_list.clear()
        for idx, item in enumerate(self.files):
            title = item["filename"]
            text = f"{idx + 1}. {title}"
            if idx == self._index:
                text = "▶ " + text
            widget = QListWidgetItem(text)
            widget.setData(Qt.ItemDataRole.UserRole, item["id"])
            self.queue_list.addItem(widget)
        if 0 <= prior < self.queue_list.count():
            self.queue_list.setCurrentRow(prior)
        else:
            self.queue_list.setCurrentRow(self._index)

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
        row = self.queue_list.currentRow()
        target = row + delta
        if not 0 <= row < len(self.files) or not 0 <= target < len(self.files):
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
        row = self.queue_list.currentRow()
        if row < 0 or row >= len(self.files):
            return
        if row == self._index:
            self.status.setText("Cannot remove the currently playing file from its queue.")
            return
        self.files.pop(row)
        if row < self._index:
            self._index -= 1
        self._save_queue()
        self.refresh_queue()
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < len(self.files) - 1)
        self.updated.emit()

    def restore_queue(self):
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

    def load_chapters(self):
        if not Path(self.current["path"]).is_file():
            self.status.setText("The selected M4B is no longer available.")
            return
        data = probe_m4b(self.current["path"])
        if data["probe_status"] != "ok" or not data["chapters"]:
            self.status.setText("This M4B has no readable chapter markers.")
            return
        self._loaded_chapters = data["chapters"][:5000]
        self.chapters.clear()
        self.chapters.addItem("Jump to chapter…", -1)
        for idx, ch in enumerate(self._loaded_chapters):
            self.chapters.addItem(
                f"{idx + 1}. {ch['title'] or 'Untitled'} ({fmt_duration(ch['start_ms'])})",
                ch["start_ms"],
            )
        self.status.setText(f"Loaded {len(self._loaded_chapters)} chapter markers.")

    def seek_chapter(self, *_args):
        position = self.chapters.currentData()
        if position is not None and 0 <= int(position) <= MAX_AUDIO_POSITION_MS:
            self._finished_file = False
            self.player.setPosition(int(position))
            self.save_position()

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
        self._save_timer.stop()
        self.save_position()
        self._closed = True
        self.player.stop()
        self.player.setSource(QUrl())
        if self._mpv_engine is not None:
            self._mpv_engine.shutdown()
        super().closeEvent(event)
