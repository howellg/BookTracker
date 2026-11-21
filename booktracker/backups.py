"""Safety-first daily backups using SQLite's online backup API."""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from platformdirs import user_data_dir

from .database import Library
from .maintenance import inspect_sqlite


def backup_directory() -> Path:
    return Path(user_data_dir("BookTracker", "BookTracker")) / "backups"


def automatic_backup(library: Library, *, destination: Path | None = None,
                     keep: int = 14) -> Path | None:
    """At most one backup each day, with a bounded retention policy.

    Returns the path of a newly created backup, or None if today's backup
    already exists. Backup failures must be surfaced by the caller; they do not
    corrupt the live database, which SQLite snapshots transactionally.
    """
    folder = destination or backup_directory()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("BookTracker-" + date.today().isoformat() + ".sqlite3")
    if path.exists():
        # Existing snapshots are never trusted merely because a filename exists.
        # A corrupt daily snapshot must surface as an actionable warning.
        checked = inspect_sqlite(path, full=False)
        if not checked["ok"]:
            raise ValueError(
                "Today's automatic SQLite backup failed its integrity check. "
                f"Keep the file for investigation: {path}"
            )
        return None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=folder, prefix=".booktracker-backup-", suffix=".sqlite3",
            delete=False
        ) as handle:
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        library.backup_to(temporary)
        if not inspect_sqlite(temporary, full=True)["ok"]:
            raise ValueError("New daily backup failed integrity validation.")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    backups = sorted(folder.glob("BookTracker-????-??-??.sqlite3"), reverse=True)
    for old in backups[max(1, keep):]:
        old.unlink()
    return path
