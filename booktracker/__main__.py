"""BookTracker CLI: run desktop GUI or inspect/backup a library safely."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__


def default_database():
    from platformdirs import user_data_dir

    return Path(user_data_dir("BookTracker", "BookTracker")) / "library.sqlite3"


def main(argv=None):
    parser = argparse.ArgumentParser(description="BookTracker desktop reading library")
    parser.add_argument("--database", type=Path, default=None,
                        help="Use a different database (does not change your default library)")
    parser.add_argument("--version", action="version",
                        version=f"BookTracker {__version__}")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--doctor", action="store_true",
                            help="Read-only SQLite integrity and foreign-key check")
    operations.add_argument("--verify-backup", type=Path, metavar="FILE",
                            help="Verify a SQLite or .btbackup archive without importing")
    operations.add_argument("--snapshot", type=Path, metavar="FILE",
                            help="Make a verified SQLite backup without modifying the source")
    args = parser.parse_args(argv)
    db_path = args.database or default_database()

    if args.doctor or args.verify_backup or args.snapshot:
        from .maintenance import (
            format_report,
            inspect_backup,
            inspect_sqlite,
            snapshot_sqlite,
        )

        if args.doctor:
            result = inspect_sqlite(db_path, full=True)
        elif args.verify_backup:
            result = inspect_backup(args.verify_backup)
        else:
            try:
                result = snapshot_sqlite(db_path, args.snapshot)
            except (OSError, ValueError) as exc:
                result = {"ok": False, "kind": "sqlite",
                          "error": str(exc), "path": str(args.snapshot)}
        print(format_report(result))
        return 0 if result.get("ok") else 1

    # Loading Qt only for the GUI also allows recovery checks on headless hosts.
    from .gui import run

    return run(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
