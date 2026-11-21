# BookTracker: backups and recovery

These procedures are for BookTracker 1.0.0 and the v0.25+ recovery CLI.
Do not restore over an open SQLite database.

## Preserve data first

Close all BookTracker windows and any program using the library. On Linux the
default location is usually `~/.local/share/BookTracker/library.sqlite3`;
the actual path is visible in the application status bar or `--doctor` report.
Windows and macOS paths are platform-specific.

**Copy the entire library-data directory to another safe location before
restoring**, including any SQLite `-wal` and `-shm` sidecar files and
`imported_covers/`. A WAL file may contain committed updates not yet
present in the main database file. Never delete it from a live database.

## Check database health (read-only)

From the BookTracker checkout:

```bash
uv run python -m booktracker --doctor
uv run python -m booktracker --self-test
```

The doctor reads your configured library but never edits it. It checks SQLite
integrity and foreign keys. The self-test is completely separate: it creates a
temporary library and exercises progress history, notes, SQLite snapshots,
portable export/restore, and device sync. It **does not open your library**.

A nonzero exit code indicates an unsuccessful check.

## Verify backups and take snapshots

```bash
uv run python -m booktracker --verify-backup "/path/to/recovery.sqlite3"
uv run python -m booktracker --verify-backup "/path/to/recovery.btbackup"
uv run python -m booktracker --snapshot "/path/to/new-snapshot.sqlite3"
```

Verification never imports data. The SQLite snapshot command uses the online
backup API for WAL consistency and validates its result before publishing it.
It refuses to overwrite an existing destination. Keep several independent
copies before major upgrades.

## Restore an SQLite snapshot

1. Close the app and make an untouched copy of the entire original
   database-data folder, including WAL/SHM sidecars if present.
2. Verify the selected `.sqlite3` snapshot with `--verify-backup`.
3. Copy that snapshot to a **separate test file** and run
   `uv run python -m booktracker --database "/path/to/test-copy.sqlite3"`.
   Inspect reading history, bookmarks and notes without touching the original
   recovery snapshot.
4. Close the test window. Run `--database "/path/to/test-copy.sqlite3" --doctor`.
5. With the original app closed, move the original database **and its WAL/SHM
   sidecars together** to another recovery directory. Keep them.
6. Copy the verified snapshot to the configured main database path; do not
   copy old WAL/SHM files back alongside the restored database. Open and
   verify the restored library.

A SQLite snapshot does **not** include separately stored imported EPUB
covers. Preserve `imported_covers/` or use a full portable backup.

## Restore a portable .btbackup

A portable bundle contains the library's reading history, metadata, queue,
notes, and imported covers, but not original EPUB/M4B/PDF media files.
Verify it with `--verify-backup` first. Create a fresh separate test database
with `--database`, choose **File → Import BookTracker file** and import your
verified bundle. Inspect the resulting library. Only after successful
verification should you consider using it to replace the main library;
preserve all original database and WAL/SHM files first.

The GUI also offers **File → Verify backup file**.

## Limits and device synchronization

Device-local audiobook resumes, local audio bookmarks, play queues and some
manually recorded EPUB/audio anchors are kept in local application settings,
not in SQLite or portable backups. Their positions may need to be recreated
on another computer.

Syncing uses explicit conflict review. After a recovery from an older
snapshot, review peers and revisions carefully before applying shared-folder
changes. An older local baseline must not erase more recent events on other
devices.

Do not attempt to import corrupt/unverified archives. If every backup fails
validation, preserve all copies and investigate before proceeding.
