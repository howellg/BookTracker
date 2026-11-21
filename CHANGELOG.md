# BookTracker 1.0 release history

BookTracker is a local-first personal reading manager. A version 1.0 release
means the core library, EPUB/audio workflows, offline recovery, and file-format
round trips have explicit regression checks. It does not imply that every
optional network provider or platform-specific media backend is guaranteed.

## 1.0.0 — Stable local-first release

- Introduced a disposable, headless `--self-test` exercising a complete
  reading-history workflow, verified SQLite snapshots, full portable backup
  round trips, and two-device sync.
- Added a Help menu with the installed version, licensing/privacy information,
  and a one-click offline self-test.
- Introduced release-package validation and built-wheel installation checks
  in GitHub CI, alongside the Python 3.10–3.13 GUI and database matrix.
- Documented safe recovery, upgrade, and release verification procedures.
- Preserved the v11 library format, existing local media, device-local
  audiobook resumes, and explicit-only session accounting.

## 0.26.0 — Startup and shutdown stability

- Refuse to open damaged existing SQLite libraries and prevent concurrent GUI
  processes from writing the same database.
- Guard shutdown while a player or import worker remains active.
- Verify daily snapshots and create them atomically; warn about a damaged
  existing backup instead of trusting or overwriting it.

## 0.25.0 — Offline recovery and verified portable backups

- Add read-only SQLite integrity and foreign-key diagnostics and snapshot
  verification via GUI and command line.
- Protect portable archive manifests with SHA-256 while continuing to import
  older archives. Validate sync envelopes and filtered peer snapshots.
- Resolve a keyboard shortcut collision.

## 0.24.0 — Listening continuity

- Resume recent local M4Bs, choose recent recordings, and switch books with
  confirmation and saved resume positions.
- Add pitch-safe global speed controls.

## 0.23.0 — Named audiobook bookmarks

- Save, search, rename, delete, and export local M4B timestamps.
- Navigate across queued recordings and save quick bookmarks by keyboard.

## 0.22.0 — Chapter metadata reliability

- Add private, bounded, invalidation-aware chapter marker caching.
- Keep the active player when a queued part has been moved or removed.

## 0.21.0 — Long-form audiobook navigation

- Search embedded chapter markers, browse and reorder a persisted part queue,
  and navigate by cumulative media position where durations are known.
- Load chapter metadata in the background and validate results after part changes.

## 0.20.0 — Listening comfort and EPUB/audio links

- Add sleep timer, precise timestamp seeking, long skips and media shortcuts.
- Add explicit EPUB section ↔ audio timestamp links without claiming automatic
  textual alignment or inventing listening sessions.

## 0.19.0 — Pitch-preserving playback

- Introduce a private, local mpv backend for corrected variable-speed audio.
- Preserve ordinary 1.0× playback if mpv is missing, rather than changing pitch.
- Add mini-player controls, queue persistence and EPUB-to-audio entry point.

## 0.18.0 — Built-in audio playback

- Add local M4B player, per-file resume, chapter markers, part navigation,
  volume, speed selector and opt-in listening timer.

## 0.1.0–0.17.0 — Core library and reading features

- Developed the SQLite library, progress history and rereads, CSV/JSON/XML and
  portable exports, Goodreads import, book metadata and cover management,
  reading queue, habits/insights, personal notes, time logs and annual goals.
- Added offline EPUB reading and imports, annotated EPUB positions, multi-source
  tracking and device-to-device snapshot sync.
- Added AudiobookAI metadata imports and local-file relinking.
