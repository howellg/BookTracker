"""Offline integrity diagnostics and atomic SQLite recovery snapshots.

All inspection paths open databases read-only; no migration, WAL checkpoint or
user-data changes are performed. Snapshot creation uses SQLite's online backup
API and validates the destination before its final atomic rename.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from .portable_backup import verify_portable

# Never copy/inspect pathologically large backups in GUI.
MAX_DATABASE_BYTES = 100 * 1024**3


def _readonly_connection(path):
    filename = Path(path).expanduser().resolve(strict=True)
    if not filename.is_file():
        raise ValueError("The selected database is not a file.")
    if not 0 < filename.stat().st_size <= MAX_DATABASE_BYTES:
        raise ValueError("The database size is outside the supported range.")
    # pathlib.as_uri() safely quotes names containing '?' or '#'.
    return filename, sqlite3.connect(filename.as_uri() + "?mode=ro", uri=True, timeout=8)


def inspect_sqlite(path, *, full=True):
    """Return compact, user-safe facts about a read-only SQLite file."""
    try:
        filename, connection = _readonly_connection(path)
        try:
            pragma = "integrity_check" if full else "quick_check"
            failures = [
                str(row[0]) for row in connection.execute(
                    f"PRAGMA {pragma}(20)"
                ).fetchmany(20)
            ]
            structural = failures == ["ok"]
            foreign_keys = [
                list(row) for row in connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchmany(20)
            ]
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            works = (
                connection.execute("SELECT count(*) FROM works").fetchone()[0]
                if "works" in tables else None
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            return {
                "kind": "sqlite", "path": str(filename),
                "ok": structural and not foreign_keys and "works" in tables,
                "integrity": failures[:20],
                "foreign_key_issues": foreign_keys,
                "works": works,
                "schema_version": version,
                "size_bytes": filename.stat().st_size,
                "full_check": bool(full),
            }
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        return {"kind": "sqlite", "path": str(path), "ok": False,
                "error": str(exc)[:500]}


def inspect_backup(path):
    """Verify a SQLite or full portable bundle without importing anything."""
    candidate = Path(path).expanduser()
    if candidate.suffix.casefold() == ".btbackup":
        return verify_portable(candidate)
    if candidate.suffix.casefold() in (".sqlite3", ".db", ".sqlite"):
        return inspect_sqlite(candidate, full=True)
    return {"ok": False, "kind": "unknown", "path": str(candidate),
            "error": "Use a .btbackup, .sqlite3, .db or .sqlite file."}


def snapshot_sqlite(path, target):
    """Make a validated, atomic SQLite snapshot of an existing DB.

    Never opens the source in read/write mode or creates an absent source.
    Never silently replaces an existing destination file.
    """
    source = Path(path).expanduser().resolve(strict=True)
    destination = Path(target).expanduser().resolve(strict=False)
    if source == destination:
        raise ValueError("Cannot back up a database onto itself.")
    if destination.exists():
        raise FileExistsError("Backup destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".booktracker-snapshot-",
            suffix=".sqlite3", delete=False
        ) as temporary:
            staged = Path(temporary.name)
        os.chmod(staged, 0o600)
        _filename, src = _readonly_connection(source)
        try:
            with sqlite3.connect(staged) as dst:
                src.backup(dst)
        finally:
            src.close()
        check = inspect_sqlite(staged, full=True)
        if not check["ok"]:
            raise ValueError("Generated SQLite snapshot did not pass integrity checks.")
        # An absent destination must remain absent until all checks pass.
        if destination.exists():
            raise FileExistsError("Backup destination appeared during snapshot.")
        os.link(staged, destination)
        staged.unlink()
        staged = None
        return inspect_sqlite(destination, full=True)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def format_report(result):
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
