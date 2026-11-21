"""Device-local named M4B time bookmarks, independent of reading history.

Per-file identity prevents saved bookmarks from seeking into replaced media.
Stored in QSettings only, never SQLite/library sync or progress sessions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QSettings

MAX_BOOKMARKS = 200
MAX_BLOB_SIZE = 150_000
MAX_POSITION = 7 * 24 * 3600 * 1000


class AudioBookmarkStore:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings(
            "BookTracker", "BookTracker"
        )

    @staticmethod
    def _key(file):
        try:
            return f"audio_bookmarks/{UUID(str(file['id']))}"
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError("Invalid audiobook ID.") from exc

    @staticmethod
    def _stamp(file):
        path = Path(file["path"]).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".m4b":
            raise ValueError("Audiobook is unavailable.")
        stat = path.stat()
        return [str(path), stat.st_size, stat.st_mtime_ns]

    @staticmethod
    def _normalize_title(value):
        title = " ".join(str(value).split()).strip()[:120]
        if not title:
            raise ValueError("Enter a bookmark name.")
        return title

    def list(self, file):
        try:
            key = self._key(file)
            stamp = self._stamp(file)
            raw = str(self.settings.value(key, ""))
            if len(raw) > MAX_BLOB_SIZE:
                return []
            saved = json.loads(raw)
            if (not isinstance(saved, dict) or saved.get("version") != 1
                    or saved.get("stamp") != stamp):
                return []
            raw_items = saved.get("marks")
            if not isinstance(raw_items, list) or len(raw_items) > MAX_BOOKMARKS:
                return []
            result = []
            seen = set()
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                try:
                    ident = str(UUID(str(item["id"])))
                    value = item["position_ms"]
                    title = item["title"]
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
                if type(value) is not int or not 0 <= value <= MAX_POSITION:
                    continue
                if not isinstance(title, str) or not title.strip() or len(title) > 120:
                    continue
                if ident in seen:
                    continue
                seen.add(ident)
                result.append({"id": ident, "position_ms": value,
                               "title": title, "created_at": str(
                                   item.get("created_at", "")
                               )[:50]})
            return sorted(result, key=lambda m: (m["position_ms"], m["id"]))
        except (ValueError, TypeError, KeyError, AttributeError, OSError,
                json.JSONDecodeError, OverflowError):
            return []

    def _write(self, file, marks):
        if len(marks) > MAX_BOOKMARKS:
            raise ValueError("This audiobook already has 200 bookmarks.")
        stamp = self._stamp(file)
        payload = json.dumps(
            {"version": 1, "stamp": stamp, "marks": marks},
            ensure_ascii=False, separators=(",", ":"),
        )
        if len(payload) > MAX_BLOB_SIZE:
            raise ValueError("Audiobook bookmarks exceed the storage limit.")
        self.settings.setValue(self._key(file), payload)

    def add(self, file, position_ms, title):
        if type(position_ms) is not int or not 0 <= position_ms <= MAX_POSITION:
            raise ValueError("Invalid audio position.")
        title = self._normalize_title(title)
        marks = self.list(file)
        if len(marks) >= MAX_BOOKMARKS:
            raise ValueError("This audiobook has reached its 200-bookmark limit.")
        result = {"id": str(uuid4()), "position_ms": position_ms,
                  "title": title, "created_at": datetime.now(
                      timezone.utc
                  ).isoformat()}
        marks.append(result)
        self._write(file, marks)
        return result

    def rename(self, file, mark_id, title):
        title = self._normalize_title(title)
        mark_id = str(UUID(str(mark_id)))
        marks = self.list(file)
        match = next((m for m in marks if m["id"] == mark_id), None)
        if match is None:
            return False
        match["title"] = title
        self._write(file, marks)
        return True

    def remove(self, file, mark_id):
        mark_id = str(UUID(str(mark_id)))
        marks = self.list(file)
        next_marks = [m for m in marks if m["id"] != mark_id]
        if len(next_marks) == len(marks):
            return False
        self._write(file, next_marks)
        return True


def bookmarks_markdown(files, store):
    """Copyable, plain Markdown overview in current playlist order."""
    lines = ["# Audiobook timestamps", ""]
    for part in files:
        marks = store.list(part)
        if not marks:
            continue
        title = str(part.get("filename", "M4B")).replace("\n", " ").strip()
        lines.extend([f"## {title}", ""])
        for mark in marks:
            seconds = mark["position_ms"] // 1000
            hours, remainder = divmod(seconds, 3600)
            minutes, secs = divmod(remainder, 60)
            value = f"{hours}:{minutes:02d}:{secs:02d}"
            name = mark["title"].replace("\n", " ")
            lines.append(f"- {value} — {name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
