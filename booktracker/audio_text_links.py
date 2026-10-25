"""Explicit, device-local EPUB ↔ audiobook timestamp anchors.

No automatic alignment, listening minutes, page/chapter milestones or DB writes.
Saved anchors are invalidated when either local source file changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QSettings

from .audio_player import MAX_AUDIO_POSITION_MS, AudioResumeStore


class AudioTextAnchors:
    MAX_ANCHORS = 100
    MAX_SERIALIZED = 150_000

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings(
            "BookTracker", "BookTracker"
        )

    @staticmethod
    def _key(work_id, epub_id):
        return f"audio_text_anchors/{UUID(str(work_id))}/{UUID(str(epub_id))}"

    @staticmethod
    def _stamp(path):
        return AudioResumeStore._stamp(AudioResumeStore._identity(path))

    @staticmethod
    def _epub_stamp(path):
        file = Path(path).resolve(strict=True)
        if not file.is_file() or file.suffix.casefold() != ".epub":
            raise ValueError("Linked EPUB is unavailable.")
        stat = file.stat()
        return AudioResumeStore._stamp((str(file), stat.st_size, stat.st_mtime_ns))

    def _read(self, work_id, epub_id):
        raw = str(self.settings.value(self._key(work_id, epub_id), "[]"))
        if len(raw) > self.MAX_SERIALIZED:
            return []
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return value if isinstance(value, list) else []

    def list_links(self, work_id, epub_id, epub_path, audio_files):
        try:
            expected_epub = self._epub_stamp(epub_path)
        except (ValueError, OSError):
            return []
        audio = {item["id"]: item for item in audio_files
                 if item.get("work_id") == work_id and item.get("available")}
        result = []
        for item in self._read(work_id, epub_id)[:self.MAX_ANCHORS]:
            if not isinstance(item, dict) or item.get("epub_stamp") != expected_epub:
                continue
            audio_id = item.get("audio_id")
            if not isinstance(audio_id, str):
                continue
            file_info = audio.get(audio_id)
            if file_info is None:
                continue
            try:
                UUID(str(item["id"]))
                if (item.get("audio_stamp") != self._stamp(file_info["path"])
                        or not 0 <= int(item["position_ms"]) <= MAX_AUDIO_POSITION_MS
                        or not 0 <= int(item["scroll_ratio"]) <= 10000):
                    continue
            except (ValueError, TypeError, KeyError, OSError, AttributeError):
                continue
            if (not isinstance(item.get("section_href"), str)
                    or len(item["section_href"]) > 1500):
                continue
            result.append(dict(item))
        return result

    def add(self, work_id, epub_id, epub_path, audio_file, *,
            section_href, section_title, scroll_ratio, position_ms,
            audio_files=None):
        if audio_file["work_id"] != work_id or not audio_file["available"]:
            raise ValueError("Audio must be a locally available file for this book.")
        if not isinstance(section_href, str) or not 0 < len(section_href) <= 1500:
            raise ValueError("Choose a valid EPUB section.")
        ratio = int(scroll_ratio)
        position = int(position_ms)
        if not 0 <= ratio <= 10000 or not 0 <= position <= MAX_AUDIO_POSITION_MS:
            raise ValueError("EPUB scroll position or audio time is outside its limits.")
        # Only previously verified, current-file anchors survive this update.
        current = self.list_links(
            work_id, epub_id, epub_path, audio_files or [audio_file]
        )[:self.MAX_ANCHORS]
        created = {
            "id": str(uuid4()),
            "audio_id": audio_file["id"],
            "audio_stamp": self._stamp(audio_file["path"]),
            "epub_stamp": self._epub_stamp(epub_path),
            "section_href": section_href,
            "section_title": str(section_title or "Section")[:160],
            "scroll_ratio": ratio,
            "position_ms": position,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        current = [created] + current[: self.MAX_ANCHORS - 1]
        self.settings.setValue(
            self._key(work_id, epub_id), json.dumps(current, ensure_ascii=False)
        )
        return created

    def remove(self, work_id, epub_id, anchor_id):
        identifier = str(UUID(str(anchor_id)))
        original = self._read(work_id, epub_id)
        updated = [item for item in original
                   if not isinstance(item, dict) or item.get("id") != identifier]
        if len(updated) == len(original):
            return False
        self.settings.setValue(
            self._key(work_id, epub_id), json.dumps(updated, ensure_ascii=False)
        )
        return True
