# BookTracker

**Your reading, remembered.** BookTracker is a local-first desktop reading-history manager for traditional books, fanfiction, and chapter-by-chapter web novels/LitRPG.

Built with Python, PySide6, and SQLite. No account, cloud subscription, or always-on internet connection is required.

## Features

- Manage regular books, fanfiction, web novels and custom works in one library.
- Record **reading progress** in chapters, pages, or percent, with dated milestones and optional notes.
- Distinguish **caught up with an ongoing story** from **completed the work**.
- Track **rereads** as independent reading runs without losing previous history.
- Search titles, authors, fandoms, series and tags; filter by type, reading status, shelves, exact series, exact tags and favorites.
- Store personal star ratings (0.5 increments), notes, publication status, word counts, source URLs and more.
- Import a **Goodreads library CSV** (from Goodreads → My Books → Import and export).
- Search by **title/ISBN or author** from **+ Add work → Find online**. Author search returns many matching works, with checkboxes and an **Add N selected** action.
- Paste a list of book titles using **+ Add work → Paste titles**. Enter an author once and import every pasted line as a book, even when catalog services are unavailable.
- Import checked books as **Completed (date unknown)** or **Plan to read**. Existing titles and cross-provider duplicate editions are skipped without changing previous reading history.
- Show cover thumbnails in the library and larger covers in each work's details. Covers are cached on disk for offline use.
- Enrich public metadata where available, including pages, year, ISBN, genre/subject tags, language and a description. Existing books have a **Fill missing metadata / cover** button.
- Cache successful public book searches locally for 12 hours, reducing API requests and allowing previously searched titles to be viewed if the catalogs temporarily go offline.
- Paste story URLs for *best-effort* metadata extraction from AO3, FanFiction.net, Royal Road, Scribble Hub, SpaceBattles, and Sufficient Velocity.
- When a website blocks automated requests, **import HTML saved from your browser** to fill in title, author, and (for AO3) tags, chapters, word count and fandom, without uploading the file.
- Export and import **portable .btbackup files** containing the entire reading history, shelves, goals, and locally imported EPUB covers. JSON/XML remain available for structured-data portability without images.
- Export and import **CSV catalogs** for spreadsheets (CSV does *not* preserve reading-history events).
- Use a **Reading dashboard** with goals for books, chapters and pages, a Continue Reading list, and recent milestones. Double-click a current book to open it.
- Browse a **Series explorer** with progress counts and open its books directly.
- **Import local EPUB metadata** offline, individually or in bulk, including embedded covers, authors, series, ISBNs, language, description, and tags where available. Recognize supported fanfiction source links embedded in EPUB metadata.
- Review library statistics, recent progress history, reading goals and series progress.
- All data is saved locally in an SQLite database; portable JSON/XML import uses stable UUIDs for merge.

## New in v0.5.0

- **Reading dashboard:** A new dashboard shows the books you're currently reading, recent activity and yearly goals. Set targets for completed books, chapters read and pages logged, and choose a year. It only counts works with real, known finish dates and recorded reading milestones; imported completed books without dates are not counted toward a year.
- **Series explorer:** A new sidebar screen groups recorded series and shows books completed and currently in progress. Double-click a series to filter the library.
- **Series and tag filters:** Filter by an exact series or tag in addition to shelves, types, favorites and reading status. A Clear filters action restores your full library.
- **EPUB metadata imports:** In **+ Add work → Book & source**, click **Read metadata from EPUB…**; or use **Import EPUB files** in the sidebar to scan multiple EPUB files in the background. You'll preview titles before adding them. All parsing happens locally; it does not upload book files or download full ebooks.
- **EPUB cover support:** Valid embedded images are resized, fingerprinted and stored in an imported_covers folder next to your database. They display in the same cover gallery and details panel as online covers.
- **Enrich existing books:** Importing an EPUB that matches a book already in your library fills *missing* public fields and cover art without altering your notes, ratings, favorites or reading status.
- **Portable full backup:** Export a **.btbackup** file to preserve your full library and local imported EPUB covers across computers. JSON and XML still preserve metadata, history, shelves and goals, but not local image files.
- **Goodreads import fix:** Goodreads Date Added is not mistaken for Date Started; known Date Read values are normalized to ISO dates. Missing dates remain unknown.
- **CSV tag fix:** Comma-containing tags round-trip as JSON arrays inside CSV fields.
- **Shelf management:** Rename or delete shelves (without deleting their books), or remove selected books from a shelf.
- **Settings and shortcuts:** Window size and list/gallery selection survive app restarts. Keyboard shortcuts include Ctrl+N (add work), Ctrl+Shift+E (EPUB import) and Ctrl+B (database backup).
- **Safety:** Replacing a library automatically takes a timestamped SQLite snapshot before the import starts. v1/v2 backup imports remain supported.

**Upgrade:** Close the app, run git pull and uv sync, then start the app. BookTracker automatically adds the goal table to existing SQLite libraries. Export a backup before major upgrades, even though an automatic daily SQLite backup is available.

## New in v0.4.0

- **Cover gallery:** Toggle between Detailed list and Cover gallery. Covers show when available; otherwise title placeholders.
- **Custom shelves:** Create collections, select several books and add them to shelves, then filter by shelf.
- **Bulk edit:** Change status, favorites or add tags across selected books without touching historical dates.
- **Daily verified backups:** BookTracker creates one SQLite snapshot per day on startup, beside its database in the backups folder. It retains the 14 most recent backups. Manual backups are also available from the sidebar.
- **Correct history mistakes:** Select a milestone in Reading history, then Delete selected mistaken milestone. Current progress recalculates using remaining milestones.
- **Portable shelf backups:** JSON and XML version 2 include shelves and memberships. Version 1 imports remain compatible.
- **Safer cross-device merges:** Existing personal notes, statuses, favorites and ratings aren't overwritten by older imports; missing public metadata is filled.
- **Honest historical dates:** Importing an old completed book without a finish date no longer manufactures a reading run dated today.

Before updating, export a JSON backup as an additional precaution. Existing v0.3 SQLite libraries are upgraded in place.

## Installation (Linux, Windows, macOS)

Install Python **3.10–3.13** and Git. On Arch Linux you may prefer uv for isolated dependencies.

### Using uv

    git clone https://github.com/howellg/BookTracker.git
    cd BookTracker
    uv sync
    uv run python -m booktracker

### Using pip and a virtual environment

    git clone https://github.com/howellg/BookTracker.git
    cd BookTracker
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install -e .
    python -m booktracker

On Windows PowerShell, activate with .venv\Scripts\Activate.ps1 instead of source .venv/bin/activate.

**Linux note:** Qt requires a graphical desktop and platform libraries. On Arch/Wayland, the default PySide6 wheels typically run through XWayland or Qt's Wayland backend. If you have trouble with your existing global PySide6 installation, use the isolated virtual environment above.

The database is stored in your OS's user-data directory (platformdirs), not inside the Git checkout. For testing with a different database:

    python -m booktracker --database /tmp/booktracker-test.sqlite3

## First steps

1. Click **+ Add work** and open **Find online**.
2. To bulk-add one author, choose **Author** from the search selector, type **Brandon Sanderson**, and click **Search books**. Check the titles you read, or use **Select all results**. Choose **Completed (date unknown)** or **Plan to read** and click **Add N selected**. Optionally check **Also mark matching existing unread books completed** if you want the books already in your library updated too. You can review each edition before checking it.
3. To import one book, use **Title / ISBN**, select a result and choose **Add to library** or **Review details**.
4. If the catalog sites are down, choose the **Paste titles** tab. Enter **Brandon Sanderson** once, paste several titles (one per line), and click **Add all pasted titles**. These are manual entries without automatically fetched metadata.
5. For a serialized work or a completely custom book, use the **Book & source** tab. Paste an HTTPS story URL and click **Fetch from URL**, or enter a manual title. The application identifies supported story sites and work IDs even if retrieval fails.
6. **If a story lookup fails:** Open that story in Firefox, press **Ctrl+S**, and save as **Web Page, HTML Only**. Back in BookTracker, leave the story URL in place and click **Import saved page…** to select the saved HTML. This is especially helpful for AO3, where Cloudflare may reject direct requests. You can also enter the title manually.
7. Select a work and click **Log progress**. Enter the **chapter/page reached**, not the number read in that session. Each change becomes a dated history event.
8. Use **Mark caught up** for ongoing serials you've reached the latest chapter; **Mark completed** for a finished read; **Start a reread** to begin a new reading run.
9. Use **Import library** / **Export library** in the sidebar to move reading history between computers. For EPUB cover images, choose a **.btbackup** portable full backup.
10. Set annual reading goals and double-click current reads on the **Dashboard**. Browse series from the **Series** sidebar page.

For existing Goodreads history, use **File → Import Goodreads CSV**. It imports Goodreads shelves and ratings; it does not create a detailed historical progress log that Goodreads never exported.

## Backup and portability

| Format | Works & history | Shelves & goals | Local EPUB covers |
|---|---|---|---|
| **.btbackup** portable bundle | Yes | Yes | **Yes** |
| JSON (schema v3) | Yes | Yes | No |
| XML (schema v3) | Yes | Yes | No |
| CSV catalog | Works/metadata only | No | No |
| Goodreads CSV (import) | Imported work fields | No | No |

**Prefer .btbackup for moving to another computer.** It is a ZIP bundle containing versioned JSON and your locally imported EPUB cover images. All referenced embedded images are verified with SHA-256 checksums on import; invalid images are rejected. Files inside the archive are never extracted to arbitrary paths.

JSON/XML remain useful for structured-data interchange and preserve reading runs, progress history, shelves and yearly goals, but do **not** carry local EPUB images. The default import mode merges reading data and preserves newer local personal fields; Replace fully overwrites your library and automatically creates a pre-import database snapshot first.

Automatic SQLite snapshots are kept beside your database in the backups folder (14 daily snapshots retained). **The SQLite snapshot itself does not include the separate imported_covers directory**. For a portable, complete transfer of locally imported covers, use a .btbackup file.

This is manual file-based portability, **not** continuous multi-device synchronization.

## Site-specific limitations

The website importer uses public HTML metadata on a **best-effort** basis, without logging in, bypassing bot protection, or requesting hidden content. For browser-saved HTML, the parser works locally: the page contents are never transmitted anywhere. AO3 and other sites may require login, show age gates, rate-limit requests, or change their markup; the import will then fail gracefully and manual entry is available. Only approved HTTPS hostnames are fetched.

Goodreads has no generally available new public API access; BookTracker supports its user-exported CSV instead. The built-in title search uses the public Open Library and Google Books catalogs. New searches require an internet connection and may be rate-limited or temporarily unavailable; the Paste titles tab always works offline. If one provider fails, the application still shows results from the other. Successful search results are cached on your computer for 12 hours and can also serve as a fallback during a temporary outage. Saved metadata and reading history remain local.

**Current scope:** This release is a reading-history tracker, not a downloader, e-reader, chapter-notification service, or background story crawler.

## Development

    uv sync --extra dev
    uv run pytest -q
    uv run ruff check booktracker tests

To run a minimal GUI smoke test without a display on CI, set QT_QPA_PLATFORM=offscreen.

**Catalog metadata limits:** Public book catalogs can sometimes supply an ISBN, page count, cover, subjects and description, but not necessarily all of them. They do **not** know your personal rating, notes, original completion date, or reading status. Bulk marking books completed deliberately leaves the historical finish date unknown rather than recording today. For books already in your library, select one and click **Fill missing metadata / cover**; personal data isn't overwritten.

Project layout:

- booktracker/database.py — reading runs, shelves, bulk editing, corrections and versioned merges
- booktracker/backups.py — daily SQLite snapshots and retention
- booktracker/integrations.py — Open Library/story metadata, Goodreads CSV, JSON/XML/CSV
- booktracker/epub_import.py — offline EPUB metadata and embedded cover parsing
- booktracker/portable_backup.py — checksum-validated ZIP bundles with local covers
- booktracker/book_search.py — title/author catalog search and caching
- booktracker/covers.py — async disk-cached cover thumbnails
- booktracker/metadata.py — optional public metadata enrichment
- booktracker/gui.py — Qt desktop application
- tests/ — database and portability regression tests

## Roadmap

Automatic site-specific word/chapter adapters, deeper monthly reading analytics, easier bulk URL importing, multi-source/edition grouping, a browser helper for fanfiction, AudiobookAI integration, optional multi-device synchronization, and installable desktop binaries.

## License

MIT.
