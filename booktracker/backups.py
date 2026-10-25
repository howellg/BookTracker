"""Safety-first daily backups using SQLite's online backup API."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from platformdirs import user_data_dir

from .database import Library


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
        return None
    temporary = path.with_suffix(".pending")
    try:
        library.backup_to(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    backups = sorted(folder.glob("BookTracker-????-??-??.sqlite3"), reverse=True)
    for old in backups[max(1, keep):]:
        old.unlink()
    return path
