"""Run with python -m booktracker."""
import argparse
from pathlib import Path

from .gui import run


def main():
    parser = argparse.ArgumentParser(description="BookTracker desktop reading library")
    parser.add_argument("--database", type=Path, help="Override database location")
    args = parser.parse_args()
    if args.database:
        db_path = args.database
    else:
        try:
            from platformdirs import user_data_dir
            db_path = Path(user_data_dir("BookTracker", "BookTracker")) / "library.sqlite3"
        except ImportError:
            db_path = Path.home() / ".local" / "share" / "BookTracker" / "library.sqlite3"
    raise SystemExit(run(db_path))


if __name__ == "__main__":
    main()
