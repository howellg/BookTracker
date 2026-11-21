# BookTracker release acceptance checklist

**Release candidate:** 1.0.0. Use a clean worktree, never the live SQLite
user-data directory, for all development/test operations.

## Version and contents

- [ ] `booktracker.__version__`, `pyproject.toml`, and `uv.lock` agree.
- [ ] `python -m booktracker --version` prints the intended version.
- [ ] README installation, recovery, scope and limitations describe shipped
      features (including EPUB reading and M4B playback) accurately.
- [ ] Changelog, MIT license, and recovery guide included in source release.

## Data safety and compatibility

- [ ] A separate, SQLite-consistent pre-upgrade snapshot of the user's existing
      library has been created and verified.
- [ ] `--doctor` confirms the existing database's integrity and foreign keys,
      without migrating or changing the actual library.
- [ ] `--self-test` exercises a **disposable** database, portable backups,
      restored reading history, notes, SQLite snapshots and device sync.
- [ ] Portable archive checksums and old-format archives still work; corrupted
      archives are rejected without changing the destination library.
- [ ] WAL mode and multi-device sync round trips pass; new sessions are
      recorded only on explicit user actions.
- [ ] Existing EPUB reading positions, annotations, audiobook playback queues,
      local resume points and local audio bookmarks remain unaffected.

## Local testing

```bash
uv sync --extra dev
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run ruff check .
uv build
uv run python -m booktracker --self-test
uv run python -m booktracker --doctor
```

- [ ] Full regression suite green without hangs or unexplained skips.
- [ ] Ruff and build green; wheel and source archive contain intended files.
- [ ] Smoke-test actual local M4B with mpv pitch correction at 1.5× and
      clean shutdown; document fallback to Qt 1.0× if mpv is unavailable.
- [ ] GUI starts and exits against a **temporary** library offscreen, and
      refuses a second concurrent writer for the same database.

## GitHub and distribution

- [ ] Pull request contains only intended files, no user data or secrets.
- [ ] All Python 3.10, 3.11, 3.12 and 3.13 GitHub CI matrix checks pass.
- [ ] Separate packaging CI validates wheel/sdist and installed-wheel CLI.
- [ ] Merge release PR into `main`, verify final merge SHA, create an annotated
      `v1.0.0` tag **on that merge** and push it.
- [ ] Publish GitHub Release notes and attach wheel + sdist artifacts.
      These are Python distributions, **not** standalone native installers.
- [ ] Fast-forward the user's original laptop checkout and run `uv sync`;
      verify 1.0.0 and a clean Git working tree.
- [ ] Verify the pre-v1 backup remains untouched and available.

## Post-release limitations

Optional public metadata services can change or rate-limit requests.
M4B variable-speed pitch correction requires external mpv, and chapter
metadata scanning requires ffprobe. Some app settings are device-local and
do not travel in library backups. No automated EPUB/text-audio alignment
or automatic listening-time estimates are promised.
