# BookTracker

**Your reading, remembered.** BookTracker is a local-first desktop reading-history manager for traditional books, fanfiction, and chapter-by-chapter web novels/LitRPG.

Built with Python, PySide6, and SQLite. No account, cloud subscription, or always-on internet connection is required.

## Features

- Manage regular books, fanfiction, web novels and custom works in one library.
- Record **reading progress** in chapters, pages, or percent, with dated milestones and optional notes.
- Independently log **time spent reading or listening to audiobooks**, using explicit dates and durations. Time records do not invent chapter/page progress.
- Save **bookmarks, quotations, and reading notes** associated with books and optional chapter/page positions.
- Distinguish **caught up with an ongoing story** from **completed the work**.
- Build a **reading queue** with priority, planned dates, personal notes, manual order and random suggestions.
- Associate existing EPUB/M4B/PDF and other reading files with a book; bulk-relink missing files from a folder across computers.
- Associate **multiple crossposted story sources** with the same work, instead of creating duplicate reading histories.
- Track **print, ebook, web, and audiobook editions** under one work; choose which edition should supply the main ISBN and page count.
- Use the **Update center** to review known unread chapters and previously failed checks, and check supported sites manually.
- Track **rereads** as independent reading runs without losing previous history.
- Search titles, authors, fandoms, series, ISBNs, descriptions, personal notes and tags. Filter by type, status, shelves, exact author/series/tag, favorites and metadata completeness.
- Store personal star ratings (0.5 increments), notes, publication status, word counts, source URLs and more. Imported tag lists in the book details panel collapse when long.
- Import a **Goodreads library CSV** (from Goodreads → My Books → Import and export).
- Search by **title/ISBN or author** from **+ Add work → Find online**. Author search returns many matching works, with checkboxes and an **Add N selected** action.
- Paste a list of book titles using **+ Add work → Paste titles**. Enter an author once and import every pasted line as a book, even when catalog services are unavailable.
- Import checked books as **Completed (date unknown)** or **Plan to read**. Existing reading history remains untouched while newly recognized crosspost URLs and alternate ISBNs may be attached to matching existing works.
- Show cover thumbnails in the library and larger covers in each work's details. Covers are cached on disk for offline use.
- Enrich public metadata where available, including pages, year, ISBN, genre/subject tags, language and a description. Existing books have a **Fill missing metadata / cover** button.
- Cache successful public book searches locally for 12 hours, reducing API requests and allowing previously searched titles to be viewed if the catalogs temporarily go offline.
- Paste story URLs for *best-effort* metadata extraction from AO3, FanFiction.net, Royal Road, Scribble Hub, SpaceBattles, and Sufficient Velocity.
- When a website blocks automated requests, **import HTML saved from your browser** to fill in title, author, and (for AO3) tags, chapters, word count and fandom, without uploading the file.
- Export and import **portable .btbackup files** containing the entire reading history, shelves, goals, and locally imported EPUB covers. JSON/XML remain available for structured-data portability without images.
- Export and import **CSV catalogs** for spreadsheets (CSV does *not* preserve reading-history events).
- Use a **Reading dashboard** with goals, current reads, serialized works with unread published chapters and recent milestones.
- Browse **Series** and **Authors** explorers; double-click to filter your library by that series or author.
- **Import local EPUB metadata** offline, individually or in bulk, including embedded covers, authors, series, ISBNs, language, description, and tags where available. Recognize supported fanfiction source links embedded in EPUB metadata and link the original EPUB files.
- Search and filter your reading-event history by book, author, note and year. Edit milestones and backdate progress without corrupting the most recent reading position. Export the filtered history as CSV.
- Save and restore **named library views** with complex filters and sorting rules. These travel in JSON/XML/.btbackup exports.
- Explore **Insights** by month, including dated chapter/page progress, active reading days, reading/listening time and completions, and export a monthly CSV.
- Use **Library health** to inspect potential duplicates, missing metadata, unknown dates and serial backlogs without automatic deletions.
- Manage global tags: rename/merge tag names or remove a tag across the library, keeping the books intact.
- Quick-update serialized chapter counts, increment progress by one or five chapters, and mark a story caught up to its known published chapter count.
- Explore the **Reading calendar**, marked dates, streaks, individual activities and time sessions. Export time logs as CSV.
- Search the **Notes & quotes** journal, copy selections to the clipboard and export selected annotations to Markdown.
- Review library statistics, recent progress history, reading goals, time spent and series progress.
- Manage crosspost source URLs, editions, and manually triggered chapter checks without altering work-level reading history.
- All data is saved locally in an SQLite database; portable JSON/XML import uses stable UUIDs for merge.

## New in v0.10.0

This release introduces an **offline reading planner** and **file associations** for EPUBs, audiobooks and other supported reading formats. Both are optional additions around the existing book records. No cloud accounts or external services are needed.

### Reading queue and planning

- **Reading queue (sidebar):** Make an ordered list of the books, fanfiction and web novels you want to tackle next.
- **Priorities:** Mark items Next up, High, Normal or Someday. Add an optional target date and a personal planning note.
- **Reorder freely:** Move an entry up or down without changing its book status, completion dates, ratings, or reading history.
- **Bulk planning:** In the library list or gallery, select several books and choose Queue selected. Existing queued works are not duplicated.
- **Flexible planning:** Search queue titles/authors/notes, filter by priority, view overdue target dates, and use Surprise me to choose a random matching title.
- **One-click start:** Select a plan and choose Start reading to create or resume an actual reading run and remove the item from the queue. When the work is completed, BookTracker asks before creating a new reread.
- **No fabricated history:** Adding to the queue does not mark a book as read or create a reading session. Targets are personal planning dates, not automatic reminders.

### Local EPUB / audiobook file associations

- **Attach files to a book:** From its detail panel choose Local files to associate existing EPUB, M4B, PDF, MOBI, AZW3, CBZ, MP3, FLAC, OGG, WAV or TXT files.
- **Open with the system default application:** Double-click a linked file or choose Open selected. BookTracker does not attempt to read DRM-protected content or implement an audio player.
- **No duplicate storage:** Files stay in place. Unlinking an association cannot delete the underlying ebook, audio file or document.
- **Missing-file detection:** Files moved from a drive or absent on another computer are clearly marked Missing — relink.
- **Relink selected:** Attach an existing local-file record to a new file path while preserving its metadata and work relationship.
- **Bulk relink by folder:** Choose Find in folder from a book's file manager, or File → Relink library files from folder to locate unavailable files across the library. This search checks filenames within up to four nested folder levels and scans at most 20,000 files.
- **Ambiguity safeguards:** If two files have the same matching filename in different locations, BookTracker will not choose arbitrarily. A skipped match can be resolved manually.
- **Automatic EPUB association:** Import EPUB files now also links the original local EPUBs to newly added or already matching works. Importing the same EPUB again does not duplicate the association.
- **Safe file linking:** Executable and unrelated file types are not accepted for local-file associations.

### Backups, upgrades and security

- **Schema v7:** Adds the reading_queue and local_files SQLite tables without replacing existing works, editions, sources, shelves, notes or historical reading runs.
- **Pre-upgrade snapshot:** Before an existing library is migrated to v7, BookTracker creates an SQLite snapshot in its adjacent backups/ directory, named BookTracker-before-v010-...sqlite3.
- **Portable queue:** JSON, XML and .btbackup exports preserve queue order, priorities, notes and target dates. Imports merge entries by stable ID or matched work without overwriting existing local plans.
- **Portable file metadata:** Backups store filenames, types, sizes and labels, *not* absolute local paths or actual EPUB/M4B/PDF file contents. When restoring on another machine (or replacing a database from a portable export), choose Relink or Find in folder to reconnect your files.
- **Previous exports supported:** Old v1–v6 JSON/XML/.btbackup imports remain compatible.
- **Safe CSV viewing:** Exported CSV catalog text cells that could be interpreted as spreadsheet formulas are escaped. Structured JSON/XML backups remain unmodified.

**Important:** .btbackup still includes locally imported cover images, but deliberately does not package books or audiobook files, which may be very large or subject to licensing restrictions. Keep your original media files separately.

**Upgrade:** Close BookTracker, export a .btbackup copy, and run git pull and uv sync in the repository. Then launch with uv run python -m booktracker. Existing records will be migrated automatically.

## New in v0.9.0

This release adds an additive **catalog relationship layer** around each existing work. A work remains the single anchor for reading history, notes, ratings, shelves, progress and time sessions, but can now have multiple websites and editions.

### Multiple story sources

- From a book's details, choose **Manage sources & editions**. In the **Story sources** tab, attach another hosted version or mirror URL. Recognized sites (AO3, FanFiction.net, Royal Road, Scribble Hub, SpaceBattles and Sufficient Velocity) get stable site IDs; arbitrary HTTPS links can still be stored, but are not automatically fetched.
- **Make primary** chooses which source link appears in the main work record. You can change the primary without losing past milestones, rereads, quotes or other personal information. Removing a source only removes the link and its associated check history.
- **Import improvements:** When matching an existing book by ISBN or recognized source ID, BookTracker can now also find alternate ISBNs and crossposted links. Bulk importing a recognized crosspost attaches it as a secondary source instead of forcing a new library entry.
- **Automatic source URL reconciliation:** Editing the primary story URL in Edit Work updates the linked source record. New works with a source link have a linked source immediately, without restarting the application.

### Editions and formats

- The **Print / ebook / audiobook editions** tab supports edition title, format, ISBN, publisher, publication date, page count, language and personal edition notes.
- Set an edition as preferred to expose its ISBN and (where progress is page-based) its page count in the main library. Reading progress and history are never reset.
- Existing works with ISBNs are migrated into a basic edition record. Older custom entries without ISBN remain valid.
- Additional ISBNs from imported duplicates can be preserved as non-preferred edition entries.

### New Update center

- The new **Update center** sidebar page lists tracked serialized works with their known read / published chapters, unread chapter counts, number of linked sources, last check and errors.
- Filter by **Unread known chapters**, **Failed checks**, or **Never checked**, and search by title or author.
- **Check chapters** retrieves metadata from a recognized public story site *only when you ask*. If the site blocks the request, the error is recorded without deleting your work, reducing the chapter count or claiming the story was removed.
- **Import saved HTML** parses a browser-saved story page locally when automated access is blocked. This also records an update in that source's check history.
- **Set chapter count manually** lets you enter a known count without contacting the provider. It is explicitly labeled as user-entered in the audit trail.
- **View check history** shows the previously reported counts, errors, dates and notes for a source.
- A higher published chapter count increases the work's known published total when appropriate. It does **not** advance your reading position or silently change your reading status.

### Safer migration and backups

- Database **schema v6** adds the work_sources, work_editions, and source_checks tables. Existing tables are not replaced or rewritten, and legacy primary URL/ISBN fields remain supported.
- **Before applying the schema upgrade**, BookTracker creates a WAL-consistent SQLite backup in the adjacent backups/ directory named BookTracker-before-v09-...sqlite3.
- Version 6 **JSON, XML and .btbackup** exports include linked sources, editions and source-check audit records, along with all previous reading history, notes, goals, saved views and sessions. Portable .btbackup continues to embed locally imported EPUB covers.
- Old v1–v5 exports remain importable. Source and edition IDs are remapped during a merge to link records to an already-matched work without overwriting local personal changes.

### Upgrade

1. Close BookTracker and export your current library as a .btbackup file, storing the backup somewhere safe.
2. In the BookTracker repository, run git pull then uv sync.
3. Launch with uv run python -m booktracker. The additive database upgrade runs automatically.

**Limitations:** Website checks are user-triggered and best-effort; no CAPTCHA or Cloudflare bypass, no continuously running background scraper, and no automatic new-chapter notifications. Unsupported HTTPS sites can be saved as links but won't be requested automatically. Check history records metadata observations, not proof that every individual chapter is available.

## Fixed in v0.8.1

- **Bookmark/quote locations are editable immediately.** When adding a bookmark, passage or personal note, the Position and Unit fields now start enabled. BookTracker automatically suggests the selected book's chapter/page/percent unit and, when available, the current reading position.
- **Locations are still optional.** Uncheck **Include a position (uncheck if unknown)** to omit the chapter/page value. Editing an older note without a location keeps that checkbox unchecked until you choose to attach one.
- **Switching books** in the Add dialog suggests the newly selected book's tracking unit and current reading position.
- No database migration is needed. Existing bookmarks, saved notes, progress and cover images are unaffected.

## New in v0.8.0

BookTracker v0.8 adds an optional **personal reading journal** without disturbing the existing books, history, shelves or progress model.

### Reading calendar

- **Reading calendar page:** A month-view calendar highlights dates with real recorded progress milestones or manual reading/listening sessions. Select any day to inspect its activity and jump to the relevant book.
- **Reading streak:** Displays the count of consecutive days with logged activity, including today or yesterday when today has not yet been logged. Missed or unknown days are not inferred to be active.
- **Timed reading sessions:** Choose a book, date, time in minutes and **Reading** or **Listening** (audiobooks). Add notes, then edit or remove the session if needed. Sessions are optional and do **not** update chapter/page progress automatically.
- **Manual backdating:** Record a historical session from any date in the supported range. A session on a historical date stays on that date.
- **Export a time log CSV:** Download a structured list of your logged sessions with date, book, author, activity, duration and note. Text fields are protected against spreadsheet formula interpretation.
- **Insights integration:** Monthly statistics now include time spent reading and listening, along with active days that come from either timed sessions or progress milestones. Reading time is never guessed from page/chapter counts.
- **Local timezone accuracy:** Offset-aware automatic timestamps are displayed and grouped by the computer's local date (including activity recorded near midnight). Explicit YYYY-MM-DD historical dates remain untouched.

### Notes, quotes and bookmarks

- **Notes & quotes page:** Save personal notes, passages and bookmarks for books, fanfiction or web novels.
- **Precise locations:** Optionally attach chapter, page or percent positions to an annotation.
- **Find and filter:** Search note text, title or author; filter by annotation type or book.
- **Edit and organize:** Correct notes, quotes and positions later, copy text to the clipboard, jump to the associated work, or delete a mistaken annotation. Removing an annotation never deletes its book.
- **Book-level shortcuts:** The selected book's detail panel links directly to timed reading logs and its personal notes.
- **Markdown export:** Export annotations for the currently filtered selection as a readable Markdown document grouped by book.

### Backups and compatibility

- **Portable schema v5:** .btbackup, JSON and XML exports now preserve timed sessions, bookmarks, quotations and personal notes in addition to all prior fields and shelves. .btbackup continues to carry locally imported EPUB covers.
- **Previous backups are still supported:** BookTracker can import version 1–4 JSON/XML/portable backups. Versions before v5 simply have no time sessions or annotations.
- **Idempotent merge:** Imported sessions and annotations retain their UUIDs. Reimporting the same backup won't duplicate them, and a normal merge will not overwrite edits on your local copy.
- **Non-destructive SQLite migration:** Existing databases are upgraded automatically by adding reading_sessions and annotations tables and indexes. The existing works and progress tables are left intact.

Before upgrading, export a .btbackup file and keep it somewhere safe. Close BookTracker and run git pull, uv sync, then uv run python -m booktracker.

## New in v0.7.0

BookTracker v0.7 is a substantial quality-of-life release with a new portable database schema (**v4**) and two new sidebar pages.

### Library tools

- **Saved views:** Combine your search text, type/status, favorites, collection/shelf, series, author, tag, quality and sort criteria. Click **Save current filters** to create a named view. Select it later to restore the filters or **Delete saved view** to remove only the preset, never its books. The saved view clears from the selector when you change one of its filters.
- **Quality filters:** Filter by missing cover, author, description or source, no series, any of those incomplete fields, unlogged finish dates, or serialized stories with unread published chapters.
- **Deeper search:** Search across ISBN, book description and personal notes in addition to titles, authors, fandoms, series and tags. Percentage signs and underscores are treated literally.
- **Expanded bulk editing:** Change ratings, publication status and series; add or remove tags for multiple selected books. Existing progress and historical dates are retained.
- **New sort:** Sort books by greatest recorded reading progress.

### Serialized-fiction workflow

- **Update published chapters:** Update how many chapters the author has currently released without altering your reading position.
- **Quick progress:** Buttons to log +1 or +5 chapters on serialized works, capped at the known published count.
- **Catch up to published:** Confirm a one-click operation to record progress through the known published chapter count and mark the story Caught up.
- **Uncaught chapter counts:** The book details panel shows the remaining chapters, while the Dashboard displays serialized works with known chapter gaps. You can open a story directly by double-clicking it.
- **Progress visualization:** Book details now include a progress bar where the published/total length is known. Cover tiles have informative tooltips.

### New Insights and Library health screens

- **Insights (sidebar):** Monthly chapters, pages, active reading days, milestone events and completions for a selected year, with an exportable CSV. Figures only use recorded activity and known finish dates; the page doesn't invent reading history. There is no 10,000-event aggregation truncation.
- **Library health (sidebar):** Counts for missing metadata and unknown finish dates, an interactive list of likely duplicate books, and shortcuts to show affected books in the library.
- **Duplicates are review-only:** Candidate matches are based on normalized title and author. No books, reading runs or notes are merged or deleted automatically.
- **Tag cleanup:** Rename or merge a tag across the entire library, or remove the tag while keeping all works intact.
- **Database integrity:** Run an on-demand SQLite integrity check from Library health.

### Compatibility and migration

- **JSON/XML/.btbackup v4** now include saved views as well as all previous works, reading events, goals and shelves. Portable .btbackup still includes locally imported EPUB covers.
- **Legacy formats remain readable:** Versions 1–3 of the BookTracker backup schema can still be imported.
- **Safer cross-device views:** A saved view's shelf reference is remapped by shelf name if merging a backup into a different library.
- **Non-destructive database upgrade:** An existing v0.6 SQLite library gains the new saved_views table automatically, with no existing book or event records rewritten.

Before installing a major version update, export a **.btbackup** file to somewhere safe. Close the app, run git pull and uv sync, and restart BookTracker. Your normal per-day SQLite snapshots continue to be created.

## New in v0.6.0

- **Author explorer:** The **Authors** sidebar screen groups books and fanfiction by author credit. Search names, view completed/active/favorite counts and average ratings, and double-click an author to filter the library. Coauthor credits are grouped as entered; they are not automatically split into separate people.
- **Author filter:** The library's Collections row now includes an exact **All authors** filter alongside shelves, series and tags.
- **Backdated reading progress:** When logging a chapter or page, check **Set a specific reading date** to record a past date. Leave it unchecked to use the current timestamp. Earlier milestones are placed in chronological order without replacing a more recent current position.
- **Correct historical milestones:** In **Reading history**, select a record and click **Edit selected** (or double-click it) to correct its date, chapter/page number, or note. All following previous-progress values in that reading run are reconciled, keeping statistics consistent.
- **History search and year filters:** Find reading events by work title, author, or note, then optionally select a particular year. **Open book** jumps to its library entry, and **Export CSV** exports the filtered history to a spreadsheet-friendly file. CSV text fields are protected against spreadsheet-formula injection.
- **Author and series navigation:** Use Ctrl+F to focus library search, Ctrl+H to open reading history and Ctrl+Shift+A for author browsing. Additional library sorts include recently finished and series.
- **Smarter book details:** Long lists of imported tags are collapsed by default. A book-specific **View this book's history** button provides quick navigation.
- **Progress dialog usability:** The suggested progress starts at the active reading run's current position, rather than zero.
- **Data integrity:** Removing or correcting an early milestone recalculates subsequent event deltas and run progress, instead of leaving inconsistent old-progress values.

**Upgrade:** v0.6.0 does not change the SQLite database schema or the v3 JSON/XML/portable backup format. Your existing library, locally imported EPUB covers and reading history remain compatible. Export a portable backup before updating as a precaution.

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
10. Set annual reading goals and double-click current reads on the **Dashboard**. Browse series from **Series** or writers from **Authors**.
11. For ongoing stories, update their chapter totals and use the +1, +5 or Catch up to published buttons in the book details.
12. To reuse library searches and filters, click **Save current filters**. Explore **Insights** and **Library health** for more management tools.
13. Use **Reading calendar → Log reading / listening** to record real reading time. Marked days contribute to the streak counter.
14. Use **Notes & quotes** or the selected book's **Add bookmark / quote / note** action to store passages or reading reactions.
15. From the selected book, choose **Manage sources & editions** to attach crossposts, alternate ISBNs, ebook editions or audiobooks.
16. Open **Update center** to review serial backlogs, failed checks and source chapter history.
17. Open **Reading queue**, use **+ Plan a book** or choose **Queue selected** in your library, then set priorities and optionally target dates.
18. Select a book and choose **Local files** to attach existing EPUB or M4B files. Use **Find in folder** to relink missing media after transferring your library.
11. To record something you read last year, choose **Log progress**, check **Set a specific reading date**, then select the historical date. To correct an older log, use **Reading history → Edit selected**.

For existing Goodreads history, use **File → Import Goodreads CSV**. It imports Goodreads shelves and ratings; it does not create a detailed historical progress log that Goodreads never exported.

## Backup and portability

| Format | Works & history | Queue, editions, journal, file-link metadata | Local EPUB covers |
|---|---|---|---|
| **.btbackup** portable bundle | Yes | Yes (including time, notes and saved views) | **Yes** |
| JSON (schema v7) | Yes | Yes (including time, notes and saved views) | No |
| XML (schema v7) | Yes | Yes (including time, notes and saved views) | No |
| CSV catalog | Works/metadata only | No | No |
| Goodreads CSV (import) | Imported work fields | No | No |

**Prefer .btbackup for moving to another computer.** It is a ZIP bundle containing versioned JSON and your locally imported EPUB cover images. All referenced embedded images are verified with SHA-256 checksums on import; invalid images are rejected. Files inside the archive are never extracted to arbitrary paths.

JSON/XML remain useful for structured-data interchange and preserve reading runs, progress history, shelves, yearly goals, queue plans, local-file metadata and the personal journal, but do **not** carry local EPUB images. The default import mode merges reading data and preserves newer local personal fields; Replace fully overwrites your library and automatically creates a pre-import database snapshot first.

Automatic SQLite snapshots are kept beside your database in the backups folder (14 daily snapshots retained). **The SQLite snapshot itself does not include the separate imported_covers directory**. For a portable, complete transfer of locally imported covers, use a .btbackup file.

Local EPUB, M4B and other book/audio files are never copied into BookTracker exports. Only filenames and metadata are saved for them; the original files must be transferred separately and relinked on another machine. This is manual file-based portability, **not** continuous multi-device synchronization.

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

- booktracker/database.py — reading runs, queue, timed sessions, annotations, local file associations, calendar statistics, shelves, linked sources/editions, versioned merges
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

Automatic site-specific word/chapter adapters, optional timed-session stopwatch and additional visualizations, easier bulk URL importing, multi-source/edition grouping, a browser helper for fanfiction, AudiobookAI integration, optional multi-device synchronization, and installable desktop binaries.

## License

MIT.
