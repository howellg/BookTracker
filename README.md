# BookTracker

**Your reading, remembered.** BookTracker is a local-first desktop reading-history manager for traditional books, fanfiction, and chapter-by-chapter web novels/LitRPG.

Built with Python, PySide6, and SQLite. No account, cloud subscription, or always-on internet connection is required.

## Features

- Manage regular books, fanfiction, web novels and custom works in one library.
- Record **reading progress** in chapters, pages, or percent, with dated milestones and optional notes.
- Distinguish **caught up with an ongoing story** from **completed the work**.
- Track **rereads** as independent reading runs without losing previous history.
- Search titles, authors, fandoms, series and tags; filter by type, reading status and favorites.
- Store personal star ratings (0.5 increments), notes, publication status, word counts, source URLs and more.
- Import a **Goodreads library CSV** (from Goodreads → My Books → Import and export).
- Search traditional books **inside + Add Work** by title, author or ISBN, using Open Library and Google Books. Compare catalog results, cover art (when available), page counts and publication years; choose **Add to library** or **Review details**. No API key required.
- Cache successful public book searches locally for 12 hours, reducing API requests and allowing previously searched titles to be viewed if the catalogs temporarily go offline.
- Paste story URLs for *best-effort* metadata extraction from AO3, FanFiction.net, Royal Road, Scribble Hub, SpaceBattles, and Sufficient Velocity.
- When a website blocks automated requests, **import HTML saved from your browser** to fill in title, author, and (for AO3) tags, chapters, word count and fandom, without uploading the file.
- Export and import **JSON/XML full backups**, preserving the books, reading runs and progress events across computers.
- Export and import **CSV catalogs** for spreadsheets (CSV does *not* preserve reading-history events).
- Review basic library statistics and recent progress history.
- All data is saved locally in an SQLite database; portable JSON/XML import uses stable UUIDs for merge.

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

1. Click **+ Add work**. It opens on the **Find online** tab.
2. Type a title such as **Way of Kings**, an author's name, or an ISBN; press **Enter** or click **Search books**.
3. Select a search result to see the author, edition, pages and cover (when available). Click **Add to library** to save it immediately with status **Plan to read**, or **Review details** to set a different status and edit metadata first.
4. For a serialized work or a completely custom book, use the **Book & source** tab. Paste an HTTPS story URL and click **Fetch from URL**, or enter a manual title. The application identifies supported story sites and work IDs even if retrieval fails.
5. **If a story lookup fails:** Open that story in Firefox, press **Ctrl+S**, and save as **Web Page, HTML Only**. Back in BookTracker, leave the story URL in place and click **Import saved page…** to select the saved HTML. This is especially helpful for AO3, where Cloudflare may reject direct requests. You can also enter the title manually.
6. Select a work and click **Log progress**. Enter the **chapter/page reached**, not the number read in that session. Each change becomes a dated history event.
7. Use **Mark caught up** for ongoing serials you've reached the latest chapter; **Mark completed** for a finished read; **Start a reread** to begin a new reading run.
8. Use **Import library** / **Export library** in the sidebar to move reading history between computers.

For existing Goodreads history, use **File → Import Goodreads CSV**. It imports Goodreads shelves and ratings; it does not create a detailed historical progress log that Goodreads never exported.

## Backup and portability

| Format | Works/metadata | Rereads | Progress history |
|---|---|---|---|
| JSON | Yes | Yes | Yes |
| XML | Yes | Yes | Yes |
| CSV | Yes | No | No |
| Goodreads CSV (import) | Yes | No | No |

For cross-computer migration, **prefer JSON**. The default import mode merges known works and ignores already-seen event IDs. You can instead choose **Replace library**, which completely replaces your existing data. Make a backup before replacing. This is **manual file-based portability**, not automatic two-way cloud synchronization.

The SQLite database is another valid local backup, but copy it while BookTracker is closed (SQLite uses WAL files during runtime).

## Site-specific limitations

The website importer uses public HTML metadata on a **best-effort** basis, without logging in, bypassing bot protection, or requesting hidden content. For browser-saved HTML, the parser works locally: the page contents are never transmitted anywhere. AO3 and other sites may require login, show age gates, rate-limit requests, or change their markup; the import will then fail gracefully and manual entry is available. Only approved HTTPS hostnames are fetched.

Goodreads has no generally available new public API access; BookTracker supports its user-exported CSV instead. The built-in title search uses the public Open Library and Google Books catalogs. New searches require an internet connection and may be rate-limited or temporarily unavailable. If one provider fails, the application still shows results from the other. Successful search results are cached on your computer for 12 hours and can also serve as a fallback during a temporary outage. Saved metadata and reading history remain local.

**Current scope:** This release is a reading-history tracker, not a downloader, e-reader, chapter-notification service, or background story crawler.

## Development

    uv sync --extra dev
    uv run pytest -q
    uv run ruff check booktracker tests

To run a minimal GUI smoke test without a display on CI, set QT_QPA_PLATFORM=offscreen.

Project layout:

- booktracker/database.py — data model, reading runs, progress and merge logic
- booktracker/integrations.py — Open Library/story metadata, Goodreads CSV, JSON/XML/CSV
- booktracker/book_search.py — multi-provider public book catalog search and ranking
- booktracker/gui.py — Qt desktop application
- tests/ — database and portability regression tests

## Roadmap

Automatic site-specific word/chapter metadata adapters, richer reading analytics and timelines, reading goals, easier bulk URL import, EPUB metadata parsing, multi-source/edition grouping, optional AudiobookAI integration, and optional privacy-preserving synchronization.

## License

MIT.
