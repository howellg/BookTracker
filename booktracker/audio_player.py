"""Opt-in local M4B playback with per-file, device-local resume locations.

QtMultimedia decodes media using the installed platform backend. Playback position
is NOT a verified listening session and never becomes a progress milestone.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
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

    def __init__(self, parent, files, selected, *, settings=None, player=None, audio_output=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings("BookTracker", "BookTracker")
        self.resume = AudioResumeStore(self.settings)
        self.files = playlist_for_file(files, selected)
        self._index = next(i for i, item in enumerate(self.files) if item["id"] == selected["id"])
        self._pending_resume = 0
        self._loading = False
        self._closed = False
        self._finished_file = False
        self._loaded_chapters = []
        self._advance_scheduled = False
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
        root.addWidget(self._button("Close player", self.close))
        if player is None or audio_output is None:
            try:
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            except ImportError as exc:
                raise RuntimeError("QtMultimedia is unavailable on this system.") from exc
            self.player = player if player is not None else QMediaPlayer(self)
            self.output = audio_output if audio_output is not None else QAudioOutput(self)
        else:
            self.player, self.output = player, audio_output
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

    @staticmethod
    def _button(title, callback):
        widget = QPushButton(title)
        widget.clicked.connect(callback)
        return widget

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

    def change_part(self, offset):
        target = self._index + offset
        if not 0 <= target < len(self.files):
            return
        self.save_position()
        self._index = target
        self.load_part()

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
        value = float(self.speed.currentData())
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
        super().closeEvent(event)
