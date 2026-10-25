"""Local-first SQLite storage for works, rereads, and progress history."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date as date_class
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("book", "fanfiction", "web_novel", "other")
STATUSES = ("plan_to_read", "reading", "caught_up", "completed", "paused", "dropped")
PUBLICATION = ("unknown", "ongoing", "completed", "hiatus")
UNITS = ("chapters", "pages", "percent")
WORK_FIELDS = (
    "title", "author", "kind", "status", "publication_status", "source_site",
    "source_url", "source_id", "isbn", "description", "fandom", "series",
    "language", "cover_url", "total_units", "progress_unit", "word_count",
    "rating", "notes", "favorite", "started_at", "finished_at", "published_at",
    "tags",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def _book_identity(value: str) -> str:
    """Basic normalized title/author comparison; does not combine different volumes."""
    words = re.findall(r"[^\W_]+", str(value).casefold(), flags=re.UNICODE)
    if len(words) > 1 and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)




class Library:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._initialize()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _initialize(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS works (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL CHECK(length(trim(title)) > 0),
            author TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'book',
            status TEXT NOT NULL DEFAULT 'plan_to_read',
            publication_status TEXT NOT NULL DEFAULT 'unknown',
            source_site TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            isbn TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            fandom TEXT NOT NULL DEFAULT '',
            series TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            cover_url TEXT NOT NULL DEFAULT '',
            total_units INTEGER NOT NULL DEFAULT 0 CHECK(total_units >= 0),
            progress_unit TEXT NOT NULL DEFAULT 'chapters',
            word_count INTEGER NOT NULL DEFAULT 0 CHECK(word_count >= 0),
            rating REAL CHECK(rating IS NULL OR (rating >= 0 AND rating <= 5)),
            notes TEXT NOT NULL DEFAULT '',
            favorite INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reading_runs (
            id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            current_progress INTEGER NOT NULL DEFAULT 0 CHECK(current_progress >= 0),
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS progress_events (
            id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES reading_runs(id) ON DELETE CASCADE,
            recorded_at TEXT NOT NULL,
            old_progress INTEGER NOT NULL,
            new_progress INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_runs_work ON reading_runs(work_id);
        CREATE INDEX IF NOT EXISTS idx_events_work ON progress_events(work_id, recorded_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_unique
            ON works(source_site, source_id)
            WHERE source_site != '' AND source_id != '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_isbn_unique ON works(isbn) WHERE isbn != '';
        CREATE INDEX IF NOT EXISTS idx_works_title ON works(title COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS shelves (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS work_shelves (
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            shelf_id TEXT NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
            PRIMARY KEY(work_id,shelf_id)
        );
        CREATE INDEX IF NOT EXISTS idx_shelf_work ON work_shelves(shelf_id,work_id);
        CREATE TABLE IF NOT EXISTS reading_goals (
            year INTEGER PRIMARY KEY CHECK(year BETWEEN 1900 AND 2200),
            books_target INTEGER NOT NULL DEFAULT 0 CHECK(books_target BETWEEN 0 AND 100000),
            chapters_target INTEGER NOT NULL DEFAULT 0 CHECK(chapters_target BETWEEN 0 AND 10000000),
            pages_target INTEGER NOT NULL DEFAULT 0 CHECK(pages_target BETWEEN 0 AND 100000000),
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_works_series ON works(series COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_works_finished_at ON works(finished_at);
        """)
        self.db.execute("PRAGMA user_version=3")
        self.db.commit()

    @staticmethod
    def _clean(data):
        out = {}
        for key in WORK_FIELDS:
            if key not in data:
                continue
            value = data[key]
            if key in ("total_units", "word_count"):
                value = max(0, int(value or 0))
            elif key == "favorite":
                value = int(bool(value))
            elif key == "rating":
                value = None if value is None or value == "" else float(value)
                if value is not None and not 0 <= value <= 5:
                    raise ValueError("Rating must be between 0 and 5.")
            elif key == "tags":
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        value = [t.strip() for t in value.split(",") if t.strip()]
                if not isinstance(value, list):
                    raise ValueError("Tags must be a list.")
                value = json.dumps(list(dict.fromkeys(str(t).strip() for t in value if str(t).strip())))
            else:
                value = str(value or "").strip()
            out[key] = value
        for field, values in (("kind", KINDS), ("status", STATUSES),
                              ("publication_status", PUBLICATION), ("progress_unit", UNITS)):
            if field in out and out[field] not in values:
                raise ValueError(f"Unsupported {field}: {out[field]}")
        if "title" in out and not out["title"]:
            raise ValueError("Title is required.")
        return out

    def add_work(self, data: dict, *, work_id: str | None = None) -> str:
        fields = self._clean(data)
        if not fields.get("title"):
            raise ValueError("Title is required.")
        work_id = work_id or new_id()
        timestamp = now()
        fields.update(id=work_id, added_at=timestamp, updated_at=timestamp)
        with self.transaction():
            columns = ", ".join(fields)
            markers = ", ".join("?" for _ in fields)
            self.db.execute(f"INSERT INTO works ({columns}) VALUES ({markers})", tuple(fields.values()))
            if fields.get("status") in ("reading", "caught_up"):
                self._begin_run(work_id, fields.get("started_at") or timestamp)
            elif fields.get("status") == "completed" and fields.get("finished_at"):
                run_id = self._begin_run(work_id, fields.get("started_at") or fields["finished_at"])
                self.db.execute("UPDATE reading_runs SET finished_at=?,is_active=0 WHERE id=?",
                                (fields["finished_at"], run_id))
        return work_id

    def update_work(self, work_id: str, data: dict):
        fields = self._clean(data)
        if not fields:
            return
        fields["updated_at"] = now()
        with self.transaction():
            assignment = ", ".join(f"{key}=?" for key in fields)
            cursor = self.db.execute(
                f"UPDATE works SET {assignment} WHERE id=?",
                (*fields.values(), work_id),
            )
            if not cursor.rowcount:
                raise KeyError(work_id)

    def get_work(self, work_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
        if row is None:
            return None
        work = dict(row)
        work["tags"] = json.loads(work["tags"])
        return work

    def list_works(self, search="", status="all", kind="all", favorites=False, sort="updated",
                   shelf_id=None, series=None, tag=None, author=None) -> list[dict]:
        sql = """SELECT w.*, COALESCE((
            SELECT current_progress FROM reading_runs
            WHERE work_id=w.id ORDER BY started_at DESC, rowid DESC LIMIT 1
        ),0) AS progress
        FROM works w WHERE 1=1"""
        args = []
        if search:
            # Treat SQL wildcard characters as literal search text.
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += (" AND (w.title LIKE ? ESCAPE '\\' OR w.author LIKE ? ESCAPE '\\'"
                    " OR w.fandom LIKE ? ESCAPE '\\' OR w.tags LIKE ? ESCAPE '\\'"
                    " OR w.series LIKE ? ESCAPE '\\')")
            args.extend([f"%{escaped}%"] * 5)
        if status != "all":
            sql += " AND w.status=?"
            args.append(status)
        if kind != "all":
            sql += " AND w.kind=?"
            args.append(kind)
        if favorites:
            sql += " AND w.favorite=1"
        if shelf_id:
            sql += " AND EXISTS (SELECT 1 FROM work_shelves ws WHERE ws.work_id=w.id AND ws.shelf_id=?)"
            args.append(shelf_id)
        if series:
            sql += " AND w.series=? COLLATE NOCASE"
            args.append(series)
        if author:
            sql += " AND w.author=? COLLATE NOCASE"
            args.append(author)
        if tag:
            sql += (" AND EXISTS (SELECT 1 FROM json_each(w.tags) t "
                    "WHERE t.value=? COLLATE NOCASE)")
            args.append(tag)
        order = {
            "updated": "w.updated_at DESC", "title": "w.title COLLATE NOCASE",
            "author": "w.author COLLATE NOCASE", "added": "w.added_at DESC",
            "rating": "w.rating DESC NULLS LAST",
            "finished": "w.finished_at DESC",
            "series": "w.series COLLATE NOCASE, w.title COLLATE NOCASE",
        }
        sql += " ORDER BY " + order.get(sort, order["updated"])
        rows = [dict(r) for r in self.db.execute(sql, args)]
        for r in rows:
            r["tags"] = json.loads(r["tags"])
        return rows

    def author_summary(self):
        """Author credits as entered, including unknown and multi-author credits."""
        rows = self.db.execute(
            """SELECT author, COUNT(*) AS total,
                SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status IN ('reading','caught_up') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN favorite=1 THEN 1 ELSE 0 END) AS favorites,
                ROUND(AVG(rating), 2) AS average_rating
            FROM works WHERE TRIM(author)!=''
            GROUP BY author COLLATE NOCASE
            ORDER BY total DESC, author COLLATE NOCASE"""
        )
        return [dict(row) for row in rows]

    def delete_work(self, work_id):
        with self.transaction():
            self.db.execute("DELETE FROM works WHERE id=?", (work_id,))

    def _begin_run(self, work_id, date=None, *, run_id=None, progress=0, active=True, finished_at=""):
        run_id = run_id or new_id()
        self.db.execute(
            "INSERT INTO reading_runs(id,work_id,started_at,finished_at,current_progress,is_active) VALUES(?,?,?,?,?,?)",
            (run_id, work_id, date or now(), finished_at, progress, int(active)),
        )
        return run_id

    def active_run(self, work_id):
        row = self.db.execute(
            "SELECT * FROM reading_runs WHERE work_id=? AND is_active=1 ORDER BY started_at DESC,rowid DESC LIMIT 1",
            (work_id,),
        ).fetchone()
        return dict(row) if row else None

    def start_reading(self, work_id, *, reread=False, date=None):
        work = self.get_work(work_id)
        if work is None:
            raise KeyError(work_id)
        with self.transaction():
            run = self.active_run(work_id)
            if run and not reread:
                self.db.execute("UPDATE works SET status='reading',updated_at=? WHERE id=?", (now(), work_id))
                return run["id"]
            if run:
                self.db.execute("UPDATE reading_runs SET is_active=0 WHERE id=?", (run["id"],))
            self.db.execute("UPDATE works SET status='reading', started_at=?, finished_at='',updated_at=? WHERE id=?",
                            (date or now(), now(), work_id))
            return self._begin_run(work_id, date)

    def set_progress(self, work_id, progress: int, *, date=None, note=""):
        progress = int(progress)
        if progress < 0:
            raise ValueError("Progress cannot be negative.")
        work = self.get_work(work_id)
        if not work:
            raise KeyError(work_id)
        if work["progress_unit"] == "percent" and progress > 100:
            raise ValueError("Percentage cannot exceed 100.")
        with self.transaction():
            run = self.active_run(work_id)
            if not run:
                run_id = self._begin_run(work_id, date)
                old = 0
                self.db.execute("UPDATE works SET started_at=COALESCE(NULLIF(started_at,''), ?) WHERE id=?",
                                (date or now(), work_id))
            else:
                run_id = run["id"]
                old = run["current_progress"]
            if progress != old or note:
                self.db.execute(
                    "INSERT INTO progress_events VALUES(?,?,?,?,?,?,?)",
                    (new_id(), work_id, run_id, date or now(), old, progress, note),
                )
            self.db.execute("UPDATE reading_runs SET current_progress=? WHERE id=?", (progress, run_id))
            new_status = "reading" if work["status"] in ("plan_to_read", "paused", "dropped") else work["status"]
            self.db.execute("UPDATE works SET status=?, updated_at=? WHERE id=?", (new_status, now(), work_id))

    def set_status(self, work_id, status: str, *, date=None):
        if status not in STATUSES:
            raise ValueError("Invalid reading status.")
        work = self.get_work(work_id)
        if not work:
            raise KeyError(work_id)
        if status == "reading":
            self.start_reading(work_id, date=date)
            return
        with self.transaction():
            run = self.active_run(work_id)
            if status == "caught_up" and not run:
                self._begin_run(work_id, work["started_at"] or date or now())
            if status == "completed":
                if not run:
                    run_id = self._begin_run(work_id, work["started_at"] or date or now())
                else:
                    run_id = run["id"]
                self.db.execute("UPDATE reading_runs SET finished_at=?,is_active=0 WHERE id=?",
                                (date or now(), run_id))
                self.db.execute("UPDATE works SET finished_at=?,status=?,updated_at=? WHERE id=?",
                                (date or now(), status, now(), work_id))
            else:
                self.db.execute("UPDATE works SET status=?,updated_at=? WHERE id=?", (status, now(), work_id))

    @staticmethod
    def _reading_date(value) -> str:
        """Normalize explicit historical dates, never infer missing timestamps."""
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.isoformat(timespec="seconds")
            return value.isoformat(timespec="seconds")
        if isinstance(value, date_class):
            return value.isoformat()
        text = str(value or "").strip()
        if not text:
            raise ValueError("Choose a date for this reading milestone.")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("Enter a date in YYYY-MM-DD format.") from exc
        if not 1900 <= parsed.year <= 2200:
            raise ValueError("Milestone year must be between 1900 and 2200.")
        if "T" in text or " " in text:
            return parsed.isoformat(timespec="seconds")
        return parsed.date().isoformat()

    def _reconcile_run_events(self, run_id):
        """Recalculate deltas chronologically after editing a past milestone.

        Event progress means the absolute position reached at that point,
        so each successor's previous value must follow the predecessor.
        """
        entries = list(self.db.execute(
            """SELECT id,new_progress FROM progress_events WHERE run_id=?
            ORDER BY recorded_at ASC,rowid ASC""", (run_id,),
        ))
        previous = 0
        for event in entries:
            if previous != self.db.execute(
                "SELECT old_progress FROM progress_events WHERE id=?", (event["id"],)
            ).fetchone()[0]:
                self.db.execute(
                    "UPDATE progress_events SET old_progress=? WHERE id=?",
                    (previous, event["id"]),
                )
            previous = event["new_progress"]
        self.db.execute(
            "UPDATE reading_runs SET current_progress=? WHERE id=?",
            (previous, run_id),
        )

    def get_progress_event(self, event_id):
        row = self.db.execute(
            """SELECT e.*, w.title,w.progress_unit,w.total_units
            FROM progress_events e JOIN works w ON w.id=e.work_id WHERE e.id=?""",
            (event_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_progress_event(self, event_id, *, date=None, progress=None, note=None):
        """Correct a milestone without creating a new reading session."""
        event = self.get_progress_event(event_id)
        if not event:
            raise ValueError("Progress event not found.")
        changes = {}
        if date is not None:
            changes["recorded_at"] = self._reading_date(date)
        if progress is not None:
            progress = int(progress)
            if progress < 0 or (event["progress_unit"] == "percent" and progress > 100):
                raise ValueError("Progress is outside the allowed range.")
            changes["new_progress"] = progress
        if note is not None:
            changes["note"] = str(note).strip()
        if not changes:
            return
        with self.transaction():
            pairs = ", ".join(f"{key}=?" for key in changes)
            self.db.execute(
                f"UPDATE progress_events SET {pairs} WHERE id=?",
                (*changes.values(), event_id),
            )
            self._reconcile_run_events(event["run_id"])
            first = self.db.execute(
                "SELECT MIN(recorded_at) AS first FROM progress_events WHERE run_id=?",
                (event["run_id"],),
            ).fetchone()["first"]
            if first:
                run = self.db.execute(
                    "SELECT started_at FROM reading_runs WHERE id=?",
                    (event["run_id"],),
                ).fetchone()
                if run and (not run["started_at"] or first < run["started_at"]):
                    self.db.execute(
                        "UPDATE reading_runs SET started_at=? WHERE id=?",
                        (first, event["run_id"]),
                    )
            self.db.execute(
                "UPDATE works SET updated_at=? WHERE id=?",
                (now(), event["work_id"]),
            )

    def record_progress_on(self, work_id, progress: int, *, date, note=""):
        """Log an event on a historical date and recalculate future deltas.

        Unlike ordinary set_progress, inserting an older milestone won't
        replace today's latest progress in the active reading run.
        """
        recorded_at = self._reading_date(date)
        work = self.get_work(work_id)
        if not work:
            raise KeyError(work_id)
        progress = int(progress)
        if progress < 0 or (work["progress_unit"] == "percent" and progress > 100):
            raise ValueError("Progress is outside the allowed range.")
        with self.transaction():
            run = self.active_run(work_id)
            if run is None:
                run_id = self._begin_run(work_id, recorded_at)
            else:
                run_id = run["id"]
            self.db.execute(
                """INSERT INTO progress_events
                (id,work_id,run_id,recorded_at,old_progress,new_progress,note)
                VALUES(?,?,?,?,?,?,?)""",
                (new_id(), work_id, run_id, recorded_at, 0, progress, str(note).strip()),
            )
            self._reconcile_run_events(run_id)
            run_date = self.db.execute(
                "SELECT started_at FROM reading_runs WHERE id=?", (run_id,)
            ).fetchone()["started_at"]
            if recorded_at < run_date:
                self.db.execute(
                    "UPDATE reading_runs SET started_at=? WHERE id=?", (recorded_at, run_id)
                )
            if not work["started_at"] or recorded_at < work["started_at"]:
                self.db.execute(
                    "UPDATE works SET started_at=? WHERE id=?",
                    (recorded_at, work_id),
                )
            new_status = (
                "reading" if work["status"] in ("plan_to_read", "paused", "dropped")
                else work["status"]
            )
            self.db.execute(
                "UPDATE works SET status=?,updated_at=? WHERE id=?",
                (new_status, now(), work_id),
            )

    def history_filtered(self, *, search="", year=None, work_id=None, limit=2000):
        """Search notes, titles and author with optional date-year filter."""
        sql = """SELECT e.*, w.title,w.author,w.progress_unit FROM progress_events e
            JOIN works w ON w.id=e.work_id WHERE 1=1"""
        args = []
        if work_id:
            sql += " AND e.work_id=?"
            args.append(work_id)
        if search.strip():
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += (" AND (w.title LIKE ? ESCAPE '\\' OR w.author LIKE ? ESCAPE '\\'"
                    " OR e.note LIKE ? ESCAPE '\\')")
            args.extend(["%" + escaped + "%"] * 3)
        if year is not None:
            year = int(year)
            if not 1900 <= year <= 2200:
                raise ValueError("History year is outside the supported range.")
            sql += " AND SUBSTR(e.recorded_at,1,4)=?"
            args.append(str(year))
        sql += " ORDER BY e.recorded_at DESC,e.rowid DESC LIMIT ?"
        args.append(max(1, min(10000, int(limit))))
        return [dict(r) for r in self.db.execute(sql, args)]

    def history_years(self):
        return [int(row["year"]) for row in self.db.execute(
            """SELECT DISTINCT SUBSTR(recorded_at,1,4) AS year
            FROM progress_events WHERE LENGTH(recorded_at)>=4
            ORDER BY year DESC"""
        ) if row["year"].isdigit()]

    def history(self, work_id=None, limit=500):
        if work_id:
            rows = self.db.execute(
                """SELECT e.*,w.title,w.progress_unit FROM progress_events e
                JOIN works w ON w.id=e.work_id WHERE e.work_id=?
                ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT ?""", (work_id, limit))
        else:
            rows = self.db.execute(
                """SELECT e.*,w.title,w.progress_unit FROM progress_events e
                JOIN works w ON w.id=e.work_id
                ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]

    def runs(self, work_id):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM reading_runs WHERE work_id=? ORDER BY started_at DESC,rowid DESC", (work_id,))]

    def stats(self):
        works = self.list_works()
        counts = {s: sum(w["status"] == s for w in works) for s in STATUSES}
        completed_runs = self.db.execute(
            "SELECT COUNT(*) FROM reading_runs WHERE finished_at!=''").fetchone()[0]
        totals = self.db.execute(
            """SELECT COALESCE(SUM(CASE WHEN e.new_progress>e.old_progress
            THEN e.new_progress-e.old_progress ELSE 0 END),0) FROM progress_events e
            JOIN works w ON e.work_id=w.id WHERE w.progress_unit='chapters'""").fetchone()[0]
        return {"works": len(works), "completed_works": counts["completed"],
                "completed_reads": completed_runs, "chapters_logged": totals,
                "favorites": sum(w["favorite"] for w in works), "by_status": counts,
                "by_kind": {k: sum(w["kind"] == k for w in works) for k in KINDS}}

    def find_match(self, data):
        for column in ("id", "isbn"):
            value = data.get(column)
            if value:
                row = self.db.execute(f"SELECT id FROM works WHERE {column}=?", (value,)).fetchone()
                if row:
                    return row["id"]
        if data.get("source_site") and data.get("source_id"):
            row = self.db.execute("SELECT id FROM works WHERE source_site=? AND source_id=?",
                                  (data["source_site"], data["source_id"])).fetchone()
            if row:
                return row["id"]
        if data.get("source_url"):
            row = self.db.execute("SELECT id FROM works WHERE source_url=?",
                                  (data["source_url"],)).fetchone()
            if row:
                return row["id"]
        return None

    def bulk_add_works(
        self, books: list[dict], *, status: str = "completed",
        update_existing_status: bool = False, enrich_duplicates: bool = False,
    ) -> dict:
        """Atomically import books, matching across catalog sources and editions.

        Historical completion timestamps remain unknown. By default, existing
        books and all their reading history stay untouched. An explicit opt-in
        upgrades existing unread (planned/dropped) matches to Completed, without
        fabricating a completed reading run or losing personal metadata.
        """
        if status not in ("completed", "plan_to_read"):
            raise ValueError("Bulk additions must be Completed or Plan to read.")
        if not isinstance(books, list):
            raise ValueError("Books must be a list.")
        if len(books) > 500:
            raise ValueError("Please import no more than 500 works at once.")

        added, updated, enriched, duplicates = [], [], [], []
        with self.transaction():
            existing = list(self.db.execute(
                "SELECT id,title,author,status,isbn,source_site,source_id,source_url FROM works"
            ))
            by_name = {
                (_book_identity(row["title"]), _book_identity(row["author"])): row["id"]
                for row in existing if row["title"] and row["author"]
            }
            by_isbn = {row["isbn"]: row["id"] for row in existing if row["isbn"]}
            by_site = {
                (row["source_site"], row["source_id"]): row["id"]
                for row in existing if row["source_site"] and row["source_id"]
            }
            by_url = {row["source_url"]: row["id"] for row in existing if row["source_url"]}
            statuses = {row["id"]: row["status"] for row in existing}

            for data in books:
                if not isinstance(data, dict):
                    raise ValueError("Each book must be an object.")
                fields = self._clean(data)
                if not fields.get("title"):
                    raise ValueError("Every imported book needs a title.")
                fields["status"] = status
                key = (_book_identity(fields["title"]), _book_identity(fields.get("author", "")))
                isbn = fields.get("isbn", "")
                site_id = (fields.get("source_site", ""), fields.get("source_id", ""))
                url = fields.get("source_url", "")

                # Existing match may be from a different provider or ISBN.
                existing_id = (
                    (by_name.get(key) if key[1] else None)
                    or (by_isbn.get(isbn) if isbn else None)
                    or (by_site.get(site_id) if all(site_id) else None)
                    or (by_url.get(url) if url else None)
                )
                if existing_id:
                    changes = {}
                    changed_status = False
                    if (
                        update_existing_status and status == "completed"
                        and statuses.get(existing_id) in ("plan_to_read", "dropped")
                    ):
                        changes["status"] = "completed"
                        changed_status = True
                        statuses[existing_id] = "completed"
                    if enrich_duplicates:
                        current = self.get_work(existing_id)
                        public = (
                            "author", "description", "isbn", "fandom", "series",
                            "language", "cover_url", "total_units", "word_count",
                            "published_at",
                        )
                        for field in public:
                            incoming = fields.get(field)
                            if not current.get(field) and incoming not in (None, "", 0):
                                changes[field] = incoming
                        if (
                            current.get("publication_status") == "unknown"
                            and fields.get("publication_status") not in (None, "", "unknown")
                        ):
                            changes["publication_status"] = fields["publication_status"]
                        if not current["tags"] and fields.get("tags") not in (None, "[]"):
                            changes["tags"] = fields["tags"]
                        if (
                            not current.get("source_site") and
                            fields.get("source_site") and fields.get("source_id")
                        ):
                            changes["source_site"] = fields["source_site"]
                            changes["source_id"] = fields["source_id"]
                            if fields.get("source_url"):
                                changes["source_url"] = fields["source_url"]
                    if changes:
                        changes["updated_at"] = now()
                        assignments = ", ".join(f"{field}=?" for field in changes)
                        self.db.execute(
                            f"UPDATE works SET {assignments} WHERE id=?",
                            (*changes.values(), existing_id),
                        )
                        if changed_status:
                            updated.append(existing_id)
                        if len(changes) > (2 if changed_status else 1):
                            enriched.append(existing_id)
                    else:
                        duplicates.append(fields["title"])
                    continue

                timestamp = now()
                fields.update(id=new_id(), added_at=timestamp, updated_at=timestamp)
                cols = ",".join(fields)
                self.db.execute(
                    f"INSERT INTO works ({cols}) VALUES ({','.join('?' for _ in fields)})",
                    tuple(fields.values()),
                )
                added.append(fields["id"])
                statuses[fields["id"]] = status
                if key[1]:
                    by_name[key] = fields["id"]
                if isbn:
                    by_isbn[isbn] = fields["id"]
                if all(site_id):
                    by_site[site_id] = fields["id"]
                if url:
                    by_url[url] = fields["id"]
                # No invented reading run, start or finish date.

        return {
            "added": len(added), "updated": len(updated),
            "enriched": len(enriched), "skipped": len(duplicates),
            "added_ids": added, "updated_ids": updated,
            "enriched_ids": enriched, "skipped_titles": duplicates,
        }

    def list_shelves(self):
        return [dict(row) for row in self.db.execute(
            "SELECT s.id,s.name,COUNT(ws.work_id) AS total FROM shelves s "
            "LEFT JOIN work_shelves ws ON ws.shelf_id=s.id "
            "GROUP BY s.id ORDER BY s.name COLLATE NOCASE"
        )]

    def create_shelf(self, name):
        name = str(name).strip()
        if not name or len(name) > 80:
            raise ValueError("Shelf name must contain 1–80 characters.")
        shelf_id = new_id()
        with self.transaction():
            self.db.execute("INSERT INTO shelves VALUES(?,?,?)", (shelf_id, name, now()))
        return shelf_id

    def rename_shelf(self, shelf_id, name):
        name = str(name).strip()
        if not name or len(name) > 80:
            raise ValueError("Shelf name must contain 1–80 characters.")
        with self.transaction():
            self.db.execute("UPDATE shelves SET name=? WHERE id=?", (name, shelf_id))

    def delete_shelf(self, shelf_id):
        with self.transaction():
            self.db.execute("DELETE FROM shelves WHERE id=?", (shelf_id,))

    def set_shelf_membership(self, work_ids, shelf_id, include=True):
        with self.transaction():
            if not self.db.execute("SELECT 1 FROM shelves WHERE id=?", (shelf_id,)).fetchone():
                raise ValueError("Shelf does not exist.")
            for work_id in set(work_ids):
                if include:
                    self.db.execute(
                        "INSERT OR IGNORE INTO work_shelves VALUES(?,?)", (work_id, shelf_id)
                    )
                else:
                    self.db.execute(
                        "DELETE FROM work_shelves WHERE work_id=? AND shelf_id=?", (work_id, shelf_id)
                    )

    def shelves_for_work(self, work_id):
        return [row["id"] for row in self.db.execute(
            "SELECT shelf_id AS id FROM work_shelves WHERE work_id=?", (work_id,)
        )]

    def batch_edit(self, work_ids, *, status=None, favorite=None, add_tags=None):
        """Edit several works atomically without manufacturing reading dates or runs."""
        work_ids = list(dict.fromkeys(work_ids))
        if status is not None and status not in STATUSES:
            raise ValueError("Invalid status.")
        if favorite is not None and not isinstance(favorite, bool):
            raise ValueError("Favorite must be True or False.")
        tags = [str(tag).strip() for tag in (add_tags or []) if str(tag).strip()]
        with self.transaction():
            for work_id in work_ids:
                book = self.get_work(work_id)
                if not book:
                    raise ValueError(f"Unknown work ID: {work_id}")
                changes = {}
                if status is not None:
                    changes["status"] = status
                if favorite is not None:
                    changes["favorite"] = int(favorite)
                if tags:
                    changes["tags"] = json.dumps(list(dict.fromkeys(book["tags"] + tags)))
                if changes:
                    changes["updated_at"] = now()
                    sql = ", ".join(f"{key}=?" for key in changes)
                    self.db.execute(
                        f"UPDATE works SET {sql} WHERE id=?", (*changes.values(), work_id)
                    )
        return len(work_ids)

    def delete_progress_event(self, event_id):
        """Remove an erroneous milestone and recompute that reading run's progress.

        The last remaining event determines the current progress; all other
        milestones, reading runs and unrelated works remain intact.
        """
        with self.transaction():
            event = self.db.execute(
                "SELECT * FROM progress_events WHERE id=?", (event_id,)
            ).fetchone()
            if not event:
                raise ValueError("Progress event not found.")
            self.db.execute("DELETE FROM progress_events WHERE id=?", (event_id,))
            self._reconcile_run_events(event["run_id"])
            self.db.execute(
                "UPDATE works SET updated_at=? WHERE id=?", (now(), event["work_id"])
            )

    def integrity_check(self):
        return self.db.execute("PRAGMA integrity_check").fetchone()[0]

    def backup_to(self, path):
        """Consistent SQLite snapshot, even in WAL mode."""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from the live database.")
        with sqlite3.connect(str(dest)) as target:
            self.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database backup failed integrity check.")
        return dest

    def series_summary(self):
        """Series with counts, including historical completions without dates."""
        rows = self.db.execute(
            """SELECT series,COUNT(*) AS total,
            SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status IN ('reading','caught_up') THEN 1 ELSE 0 END) AS active
            FROM works WHERE TRIM(series) != ''
            GROUP BY series COLLATE NOCASE ORDER BY series COLLATE NOCASE"""
        )
        return [dict(r) for r in rows]

    def tag_summary(self):
        """Tags remain user-controlled; calculate exact case-insensitive counts."""
        counts = {}
        for row in self.db.execute("SELECT tags FROM works"):
            for tag in json.loads(row["tags"]):
                cleaned = str(tag).strip()
                if cleaned:
                    key = cleaned.casefold()
                    if key in counts:
                        counts[key][1] += 1
                    else:
                        counts[key] = [cleaned, 1]
        return [
            {"tag": tag, "total": n}
            for tag, n in sorted(counts.values(), key=lambda pair: pair[0].casefold())
        ]

    def save_goal(self, year: int, *, books: int = 0, chapters: int = 0, pages: int = 0):
        year = int(year)
        targets = [int(books), int(chapters), int(pages)]
        if not 1900 <= year <= 2200 or any(n < 0 for n in targets):
            raise ValueError("Goals require a valid year and nonnegative targets.")
        if targets[0] > 100000 or targets[1] > 10000000 or targets[2] > 100000000:
            raise ValueError("Goal exceeds the supported limit.")
        with self.transaction():
            self.db.execute(
                """INSERT INTO reading_goals VALUES(?,?,?,?,?)
                ON CONFLICT(year) DO UPDATE SET
                books_target=excluded.books_target,
                chapters_target=excluded.chapters_target,
                pages_target=excluded.pages_target,
                updated_at=excluded.updated_at""",
                (year, *targets, now()),
            )

    def get_goal(self, year: int):
        year = int(year)
        row = self.db.execute("SELECT * FROM reading_goals WHERE year=?", (year,)).fetchone()
        return dict(row) if row else {
            "year": year, "books_target": 0, "chapters_target": 0,
            "pages_target": 0, "updated_at": ""
        }

    def goal_progress(self, year: int):
        """Count recorded activity only; unknown historical dates are excluded."""
        year = int(year)
        label = str(year)
        books = self.db.execute(
            """SELECT COUNT(*) FROM works w
            WHERE (w.finished_at != '' AND SUBSTR(w.finished_at,1,4)=?)
            OR EXISTS (
                SELECT 1 FROM reading_runs r
                WHERE r.work_id=w.id AND r.finished_at != ''
                AND SUBSTR(r.finished_at,1,4)=?
            )""", (label, label),
        ).fetchone()[0]
        totals = self.db.execute(
            """SELECT w.progress_unit AS unit,
                SUM(CASE WHEN e.new_progress > e.old_progress
                    THEN e.new_progress - e.old_progress ELSE 0 END) AS amount
            FROM progress_events e JOIN works w ON w.id=e.work_id
            WHERE SUBSTR(e.recorded_at,1,4)=?
            GROUP BY w.progress_unit""", (label,)
        )
        amounts = {r["unit"]: r["amount"] or 0 for r in totals}
        return {
            "year": year, "books": books,
            "chapters": amounts.get("chapters", 0),
            "pages": amounts.get("pages", 0),
        }

    def recent_activity(self, limit=10):
        return [dict(row) for row in self.db.execute(
            """SELECT e.recorded_at,e.new_progress,e.old_progress,
            e.note,w.id AS work_id,w.title,w.progress_unit FROM progress_events e
            JOIN works w ON w.id=e.work_id
            ORDER BY e.recorded_at DESC,e.rowid DESC LIMIT ?""",
            (max(1, min(100, int(limit))),)
        )]

    def export_data(self):
        return {"format": "booktracker", "schema_version": 3, "exported_at": now(),
                "works": [dict(row) for row in self.db.execute("SELECT * FROM works")],
                "reading_runs": [dict(row) for row in self.db.execute("SELECT * FROM reading_runs")],
                "progress_events": [dict(row) for row in self.db.execute("SELECT * FROM progress_events")],
                "shelves": [dict(row) for row in self.db.execute("SELECT * FROM shelves")],
                "work_shelves": [dict(row) for row in self.db.execute("SELECT * FROM work_shelves")],
                "reading_goals": [dict(row) for row in self.db.execute("SELECT * FROM reading_goals")]}

    def import_data(self, data, *, strategy="merge"):
        """Transactional import; duplicate event IDs are skipped when merging."""
        if data.get("format") != "booktracker" or data.get("schema_version") not in (1, 2, 3):
            raise ValueError("Unsupported BookTracker backup format or schema version.")
        if data["schema_version"] >= 2 and (
            not isinstance(data.get("shelves"), list)
            or not isinstance(data.get("work_shelves"), list)
        ):
            raise ValueError("Backup is missing shelf information.")
        if data["schema_version"] == 3 and not isinstance(data.get("reading_goals"), list):
            raise ValueError("Backup is missing reading goals.")
        for key in ("works", "reading_runs", "progress_events"):
            if not isinstance(data.get(key), list):
                raise ValueError(f"Backup is missing {key}.")
        if strategy not in ("merge", "replace"):
            raise ValueError("Invalid import strategy.")
        mapping = {}
        count = 0
        with self.transaction():
            if strategy == "replace":
                self.db.execute("DELETE FROM works")
                self.db.execute("DELETE FROM shelves")
                self.db.execute("DELETE FROM reading_goals")
            for item in data["works"]:
                if not isinstance(item, dict) or not item.get("id"):
                    raise ValueError("Invalid work in backup.")
                fields = self._clean(item)
                if not fields.get("title"):
                    raise ValueError("Work has no title.")
                existing = None if strategy == "replace" else self.find_match(item)
                target_id = existing or item["id"]
                mapping[item["id"]] = target_id
                if existing:
                    # During a merge, preserve local reading status, dates,
                    # notes and corrections; backfill missing public fields.
                    current = self.get_work(target_id)
                    public = ("author", "description", "source_site", "source_url",
                              "source_id", "isbn", "fandom", "series", "language",
                              "cover_url", "total_units", "word_count", "published_at")
                    patches = {}
                    for field in public:
                        incoming = fields.get(field)
                        if not current.get(field) and incoming not in (None, "", 0):
                            patches[field] = incoming
                    if not current["tags"] and fields.get("tags") not in (None, "[]"):
                        patches["tags"] = fields["tags"]
                    if patches:
                        patches["updated_at"] = now()
                        keys = ", ".join(f"{k}=?" for k in patches)
                        self.db.execute(f"UPDATE works SET {keys} WHERE id=?",
                                        (*patches.values(), target_id))
                else:
                    fields.update(id=target_id, added_at=item.get("added_at") or now(),
                                  updated_at=item.get("updated_at") or now())
                    cols = ",".join(fields)
                    self.db.execute(f"INSERT INTO works({cols}) VALUES({','.join('?' for _ in fields)})",
                                    tuple(fields.values()))
                    count += 1
            for run in data["reading_runs"]:
                work_id = mapping.get(run.get("work_id"))
                if not work_id or not run.get("id"):
                    raise ValueError("Orphaned or malformed reading run.")
                self.db.execute("""INSERT OR IGNORE INTO reading_runs
                    (id,work_id,started_at,finished_at,current_progress,is_active)
                    VALUES(?,?,?,?,?,?)""",
                    (run["id"], work_id, run.get("started_at") or now(),
                     run.get("finished_at") or "", max(0,int(run.get("current_progress",0))),
                     int(bool(run.get("is_active",0)))))
            for event in data["progress_events"]:
                work_id = mapping.get(event.get("work_id"))
                if not work_id or not event.get("id") or not event.get("run_id"):
                    raise ValueError("Orphaned or malformed progress event.")
                self.db.execute("""INSERT OR IGNORE INTO progress_events
                    (id,work_id,run_id,recorded_at,old_progress,new_progress,note)
                    VALUES(?,?,?,?,?,?,?)""",
                    (event["id"], work_id, event["run_id"], event.get("recorded_at") or now(),
                     int(event.get("old_progress",0)), int(event.get("new_progress",0)),
                     str(event.get("note") or "")))
            # Older v1 backups have no shelf data; preserve shelves on merge.
            shelf_map = {}
            for shelf in data.get("shelves", []):
                name = str(shelf.get("name") or "").strip()
                if not name or not shelf.get("id"):
                    raise ValueError("Malformed shelf in backup.")
                existing_shelf = self.db.execute(
                    "SELECT id FROM shelves WHERE name=? COLLATE NOCASE", (name,)
                ).fetchone()
                new_shelf = existing_shelf["id"] if existing_shelf else shelf["id"]
                shelf_map[shelf["id"]] = new_shelf
                if not existing_shelf:
                    self.db.execute(
                        "INSERT INTO shelves VALUES(?,?,?)",
                        (new_shelf, name, shelf.get("created_at") or now())
                    )
            for link in data.get("work_shelves", []):
                work_id = mapping.get(link.get("work_id"))
                shelf_id = shelf_map.get(link.get("shelf_id"))
                if not work_id or not shelf_id:
                    raise ValueError("Orphaned shelf assignment.")
                self.db.execute(
                    "INSERT OR IGNORE INTO work_shelves VALUES(?,?)", (work_id, shelf_id)
                )
            for goal in data.get("reading_goals", []):
                try:
                    year = int(goal["year"])
                    books = int(goal["books_target"])
                    chapters = int(goal["chapters_target"])
                    pages = int(goal["pages_target"])
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValueError("Malformed reading goal in backup.") from exc
                if not 1900 <= year <= 2200 or min(books, chapters, pages) < 0:
                    raise ValueError("Invalid goal targets in backup.")
                if books > 100000 or chapters > 10000000 or pages > 100000000:
                    raise ValueError("Goal targets exceed limits.")
                self.db.execute(
                    """INSERT OR IGNORE INTO reading_goals
                    (year,books_target,chapters_target,pages_target,updated_at)
                    VALUES(?,?,?,?,?)""",
                    (year, books, chapters, pages, goal.get("updated_at") or now()),
                )
        return count
