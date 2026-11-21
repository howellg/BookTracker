"""Private on-device list of recently opened audiobook files.

Only identifiers and a source-file identity stamp are persisted. Stale files,
unlinked works, and replaced recordings are never resumed automatically.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QSettings

MAX_RECENT = 30
MAX_DATA_SIZE = 50_000
KEY = "recent_audio_files/v1"


class RecentAudioStore:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings(
            "BookTracker", "BookTracker"
        )

    @staticmethod
    def stamp(file):
        if not file.get("available", True):
            raise ValueError("The local M4B is missing.")
        path = Path(file["path"]).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.casefold() != ".m4b":
            raise ValueError("A locally linked M4B is required.")
        stat = path.stat()
        return [str(path), stat.st_size, stat.st_mtime_ns]

    def _load(self):
        try:
            raw = str(self.settings.value(KEY, ""))
            if len(raw) > MAX_DATA_SIZE:
                return []
            parsed = json.loads(raw)
            if not isinstance(parsed, list) or len(parsed) > MAX_RECENT:
                return []
            rows = []
            seen = set()
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                try:
                    file_id = str(UUID(str(item["id"])))
                    stamp = item["stamp"]
                except (ValueError, TypeError, AttributeError, KeyError):
                    continue
                if (file_id in seen or not isinstance(stamp, list) or len(stamp) != 3
                        or not isinstance(stamp[0], str)
                        or type(stamp[1]) is not int or type(stamp[2]) is not int):
                    continue
                seen.add(file_id)
                rows.append({"id": file_id, "stamp": stamp,
                             "opened_at": str(item.get("opened_at", ""))[:50]})
            return rows
        except (ValueError, TypeError, OSError, json.JSONDecodeError):
            return []

    def mark_opened(self, file):
        try:
            file_id = str(UUID(str(file["id"])))
            fingerprint = self.stamp(file)
            rows = [entry for entry in self._load() if entry["id"] != file_id]
            rows.insert(0, {
                "id": file_id, "stamp": fingerprint,
                "opened_at": datetime.now(timezone.utc).isoformat(),
            })
            self.settings.setValue(KEY, json.dumps(
                rows[:MAX_RECENT], ensure_ascii=False, separators=(",", ":")
            ))
            return True
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            return False

    def available(self, audio_files):
        """Return library-backed current files in order of most recent opening."""
        by_id = {str(file["id"]): file for file in audio_files
                 if file.get("available")}
        result = []
        for entry in self._load():
            file = by_id.get(entry["id"])
            if file is None:
                continue
            try:
                if self.stamp(file) == entry["stamp"]:
                    result.append(file)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return result

    def most_recent(self, audio_files):
        available = self.available(audio_files)
        return available[0] if available else None

    def clear(self):
        self.settings.remove(KEY)
