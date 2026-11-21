"""Bounded, local-only on-disk cache of verified M4B chapter markers.

Never writes reading/listening sessions or modifies source audio. Cache identities
follow the existing audiobook resume rules: canonical path, size and mtime_ns.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from platformdirs import user_cache_dir

from .audio_navigation import valid_chapters

CACHE_VERSION = 1
MAX_CACHE_BYTES = 2_000_000
MAX_CACHED_FILES = 64


class ChapterCache:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else (
            Path(user_cache_dir("BookTracker", "BookTracker")) / "chapters"
        )

    @staticmethod
    def _fingerprint(file):
        path = Path(file["path"]).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.casefold() != ".m4b":
            raise ValueError("A local M4B file is required.")
        stat = path.stat()
        return [str(file["id"]), str(path), stat.st_size, stat.st_mtime_ns]

    def _cache_file(self, file):
        return self.directory / (hashlib.sha256(
            str(file["id"]).encode("utf-8")
        ).hexdigest() + ".json")

    def read(self, file):
        try:
            fingerprint = self._fingerprint(file)
            path = self._cache_file(file)
            if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict)
                    or data.get("version") != CACHE_VERSION
                    or data.get("fingerprint") != fingerprint):
                return None
            raw = data.get("chapters")
            if not isinstance(raw, list) or not 0 < len(raw) <= 5000:
                return None
            chapters = valid_chapters(raw)
            if len(chapters) != len(raw):
                return None
            # Last access time for bounded least-recently-used cleanup.
            os.utime(path, None)
            return {"probe_status": "ok", "chapters": chapters,
                    "duration_ms": int(data.get("duration_ms") or 0)}
        except (OSError, ValueError, TypeError, OverflowError,
                UnicodeError, KeyError, json.JSONDecodeError):
            return None

    def write(self, file, result):
        if not isinstance(result, dict) or result.get("probe_status") != "ok":
            return False
        raw = result.get("chapters")
        if not isinstance(raw, list) or not 0 < len(raw) <= 5000:
            return False
        chapters = valid_chapters(raw)
        if len(chapters) != len(raw):
            return False
        try:
            fingerprint = self._fingerprint(file)
            duration = int(result.get("duration_ms") or 0)
            if duration < 0:
                return False
            contents = json.dumps(
                {"version": CACHE_VERSION, "fingerprint": fingerprint,
                 "chapters": chapters, "duration_ms": duration},
                ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(contents) > MAX_CACHE_BYTES:
                return False
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.directory, prefix=".chapter-", suffix=".tmp",
                    delete=False,
                ) as handle:
                    temp = Path(handle.name)
                    os.chmod(temp, 0o600)
                    handle.write(contents)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self._cache_file(file))
                temp = None
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            self._prune()
            return True
        except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
            return False

    def clear(self, file):
        try:
            self._cache_file(file).unlink(missing_ok=True)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def _prune(self):
        try:
            files = sorted(
                self.directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
            )
            for path in files[:-MAX_CACHED_FILES]:
                path.unlink(missing_ok=True)
        except OSError:
            pass
