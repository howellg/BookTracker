"""Pitch-preserving mpv engine exposed through the small Qt media-player API.

Uses local-only mpv JSON IPC. Every launch uses a private temporary directory,
doesn't read arbitrary mpv configuration/scripts, and never opens network media.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtNetwork import QLocalSocket


def pitch_safe_backend():
    return shutil.which("mpv")


class MpvOutput(QObject):
    def __init__(self, player):
        super().__init__(player)
        self._player = player

    def setVolume(self, volume):
        self._player._command(["set_property", "volume", max(0, min(100, float(volume) * 100))])


class MpvPlayer(QObject):
    """Implements the media-player methods used in AudioPlayerDialog."""

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object)

    def __init__(self, parent=None, *, binary=None):
        super().__init__(parent)
        binary = binary or pitch_safe_backend()
        if not binary:
            raise RuntimeError("mpv is required for pitch-preserving playback speed.")
        self._tmp = tempfile.TemporaryDirectory(prefix="booktracker-mpv-")
        self.socket_path = str(Path(self._tmp.name) / "ipc.sock")
        self._process = QProcess(self)
        self._process.setProgram(binary)
        self._process.setArguments([
            "--no-config", "--idle=yes", "--no-video",
            "--audio-pitch-correction=yes", "--input-terminal=no",
            "--input-ipc-server=" + self.socket_path,
            "--no-resume-playback",
        ])
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.finished.connect(self._on_exit)
        self._process.start()
        if not self._process.waitForStarted(2500):
            self._tmp.cleanup()
            raise RuntimeError("Could not start the mpv audio engine.")
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.readyRead.connect(self._read_messages)
        self._socket.disconnected.connect(self._on_disconnected)
        self._linebuffer = bytearray()
        self._pending = []
        self._source = QUrl()
        self._position = 0
        self._duration = 0
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._status = QMediaPlayer.MediaStatus.NoMedia
        self._error = ""
        self._playing_requested = False
        self._closed = False
        self._retry_count = 0
        self._retry = QTimer(self)
        self._retry.setInterval(100)
        self._retry.timeout.connect(self._connect)
        self._retry.start()
        self._connect()

    def _connect(self):
        if self._closed or self._socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            return
        if self._retry_count > 75:
            self._retry.stop()
            self._error = "mpv IPC connection timed out."
            self.errorOccurred.emit(QMediaPlayer.Error.ResourceError)
            return
        self._retry_count += 1
        self._socket.connectToServer(self.socket_path)

    def _on_connected(self):
        self._retry.stop()
        for ident, name in enumerate(("time-pos", "duration", "pause"), start=1):
            self._write(["observe_property", ident, name])
        self._write(["set_property", "audio-pitch-correction", True])
        for command in self._pending:
            self._write(command)
        self._pending.clear()

    def _write(self, command):
        data = json.dumps({"command": command}, separators=(",", ":")).encode("utf-8") + b"\n"
        self._socket.write(data)

    def _command(self, command):
        if self._closed:
            return
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._write(command)
        elif len(self._pending) < 100:
            self._pending.append(command)

    def _read_messages(self):
        chunk = bytes(self._socket.readAll())
        self._linebuffer.extend(chunk)
        if len(self._linebuffer) > 500_000:
            self._linebuffer.clear()
            self._error = "Oversized mpv response discarded."
            self.errorOccurred.emit(QMediaPlayer.Error.ResourceError)
            return
        while b"\n" in self._linebuffer:
            line, _, remaining = self._linebuffer.partition(b"\n")
            self._linebuffer = bytearray(remaining)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("event") == "property-change":
                name = message.get("name")
                value = message.get("data")
                if name == "time-pos" and isinstance(value, (int, float)):
                    self._position = max(0, int(value * 1000))
                    self.positionChanged.emit(self._position)
                elif name == "duration" and isinstance(value, (int, float)):
                    self._duration = max(0, int(value * 1000))
                    self.durationChanged.emit(self._duration)
                elif name == "pause" and isinstance(value, bool):
                    state = (QMediaPlayer.PlaybackState.PausedState if value
                             else QMediaPlayer.PlaybackState.PlayingState)
                    self._state = state
                    self.playbackStateChanged.emit(state)
            elif message.get("event") == "file-loaded":
                self._status = QMediaPlayer.MediaStatus.LoadedMedia
                self.mediaStatusChanged.emit(self._status)
            elif message.get("event") == "end-file":
                if message.get("reason") == "eof":
                    self._status = QMediaPlayer.MediaStatus.EndOfMedia
                    self.mediaStatusChanged.emit(self._status)
                elif message.get("reason") == "error":
                    self._error = "mpv could not play this M4B. Try the external player."
                    self.errorOccurred.emit(QMediaPlayer.Error.FormatError)

    def _on_disconnected(self):
        if not self._closed:
            self._error = "mpv lost its IPC connection."
            self.errorOccurred.emit(QMediaPlayer.Error.ResourceError)

    def _on_exit(self, *_args):
        if not self._closed:
            self._error = "mpv audio engine exited."
            self.errorOccurred.emit(QMediaPlayer.Error.ResourceError)

    def setAudioOutput(self, output):
        self._output = output

    def setSource(self, url):
        self._source = url
        self._position = 0
        self._duration = 0
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._status = QMediaPlayer.MediaStatus.NoMedia
        if not url.isEmpty():
            if not url.isLocalFile():
                raise ValueError("Only local M4B files are allowed.")
            path = Path(url.toLocalFile()).resolve(strict=True)
            if not path.is_file() or path.suffix.lower() != ".m4b":
                raise ValueError("The linked M4B is missing or invalid.")
            self._status = QMediaPlayer.MediaStatus.LoadingMedia
            self._command(["loadfile", str(path), "replace"])
            # Explicit pause prevents a newly loaded file from unexpectedly playing.
            self._command(["set_property", "pause", True])

    def play(self):
        self._command(["set_property", "pause", False])
        self._state = QMediaPlayer.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self._state)

    def pause(self):
        self._command(["set_property", "pause", True])
        self._state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def stop(self):
        self._command(["stop"])
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.playbackStateChanged.emit(self._state)

    def setPosition(self, position):
        position = max(0, min(7 * 24 * 3600 * 1000, int(position)))
        self._command(["set_property", "time-pos", position / 1000])
        self._position = position
        self.positionChanged.emit(position)

    def setPlaybackRate(self, rate):
        self._command(["set_property", "speed", max(0.5, min(3.0, float(rate)))])
        # mpv keeps audio-pitch-correction=yes at ALL supported speeds.
        self._command(["set_property", "audio-pitch-correction", True])

    def position(self):
        return self._position

    def duration(self):
        return self._duration

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def errorString(self):
        return self._error

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._retry.stop()
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._write(["quit"])
            self._socket.flush()
        self._socket.abort()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(1000):
                self._process.kill()
                self._process.waitForFinished(1000)
        self._tmp.cleanup()
