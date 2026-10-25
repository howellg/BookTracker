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
        CREATE TABLE IF NOT EXISTS saved_views (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            filters_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reading_sessions (
            id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            read_date TEXT NOT NULL,
            minutes INTEGER NOT NULL CHECK(minutes BETWEEN 1 AND 1440),
            mode TEXT NOT NULL CHECK(mode IN ('reading', 'listening')),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_date ON reading_sessions(read_date);
        CREATE INDEX IF NOT EXISTS idx_sessions_work ON reading_sessions(work_id,read_date);
        CREATE TABLE IF NOT EXISTS annotations (
            id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('bookmark','quote','note')),
            location INTEGER,
            location_unit TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(location IS NULL OR location BETWEEN 0 AND 10000000)
        );
        CREATE INDEX IF NOT EXISTS idx_annotations_work ON annotations(work_id,created_at);
        """)
        self.db.execute("PRAGMA user_version=5")
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
                   shelf_id=None, series=None, tag=None, author=None,
                   quality=None) -> list[dict]:
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
                    " OR w.series LIKE ? ESCAPE '\\' OR w.notes LIKE ? ESCAPE '\\'"
                    " OR w.description LIKE ? ESCAPE '\\' OR w.isbn LIKE ? ESCAPE '\\')")
            args.extend([f"%{escaped}%"] * 8)
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
        quality_sql = {
            "missing_cover": "w.cover_url=''",
            "missing_author": "w.author=''",
            "missing_description": "w.description=''",
            "missing_source": "w.source_url=''",
            "missing_series": "w.series=''",
            "unknown_dates": "w.status='completed' AND w.finished_at=''",
        }
        if quality in quality_sql:
            sql += " AND (" + quality_sql[quality] + ")"
        if quality == "serial_backlog":
            sql += (""" AND w.kind IN ('fanfiction','web_novel')
                AND w.progress_unit='chapters' AND w.total_units>0
                AND w.status IN ('reading','caught_up') AND (
                SELECT COALESCE(current_progress,0) FROM reading_runs r
                WHERE r.work_id=w.id ORDER BY started_at DESC,rowid DESC LIMIT 1
                ) BETWEEN 1 AND w.total_units-1""")
        if quality == "incomplete":
            sql += (" AND (w.cover_url='' OR w.author='' OR w.description=''"
                    " OR w.source_url='')")
        order = {
            "updated": "w.updated_at DESC", "title": "w.title COLLATE NOCASE",
            "author": "w.author COLLATE NOCASE", "added": "w.added_at DESC",
            "rating": "w.rating DESC NULLS LAST",
            "finished": "w.finished_at DESC",
            "series": "w.series COLLATE NOCASE, w.title COLLATE NOCASE",
            "progress": "progress DESC, w.title COLLATE NOCASE",
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
        """Search milestones by book, author, note and machine-local activity year."""
        sql = """SELECT e.*, w.title,w.author,w.progress_unit FROM progress_events e
            JOIN works w ON w.id=e.work_id WHERE 1=1"""
        args = []
        if work_id:
            sql += " AND e.work_id=?"
            args.append(work_id)
        if search.strip():
            escaped = (
                search.strip().replace("\\", "\\\\")
                .replace("%", "\\%").replace("_", "\\_")
            )
            sql += (" AND (w.title LIKE ? ESCAPE '\\' OR w.author LIKE ? ESCAPE '\\'"
                    " OR e.note LIKE ? ESCAPE '\\')")
            args.extend(["%" + escaped + "%"] * 3)
        if year is not None:
            year = int(year)
            if not 1900 <= year <= 2200:
                raise ValueError("History year is outside the supported range.")
        sql += " ORDER BY e.recorded_at DESC,e.rowid DESC"
        count = max(1, min(10000, int(limit)))
        if year is None:
            sql += " LIMIT ?"
            args.append(count)
        entries = []
        for row in self.db.execute(sql, args):
            if year is not None:
                day = self.activity_day(row["recorded_at"])
                if not day or int(day[:4]) != year:
                    continue
            entries.append(dict(row))
            if len(entries) >= count:
                break
        return entries

    def history_years(self):
        return sorted({
            int(day[:4]) for row in self.db.execute(
                "SELECT recorded_at FROM progress_events"
            ) if (day := self.activity_day(row["recorded_at"]))
        }, reverse=True)

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

    def batch_edit(self, work_ids, *, status=None, favorite=None, add_tags=None,
                   remove_tags=None, rating=None, publication_status=None,
                   series=None):
        """Edit several works atomically without manufacturing reading dates or runs."""
        work_ids = list(dict.fromkeys(work_ids))
        if status is not None and status not in STATUSES:
            raise ValueError("Invalid status.")
        if favorite is not None and not isinstance(favorite, bool):
            raise ValueError("Favorite must be True or False.")
        tags = [str(tag).strip() for tag in (add_tags or []) if str(tag).strip()]
        removed = [str(tag).strip().casefold() for tag in (remove_tags or [])
                   if str(tag).strip()]
        if rating is not None:
            rating = float(rating)
            if not 0 <= rating <= 5:
                raise ValueError("Rating must be between 0 and 5.")
        if publication_status is not None and publication_status not in PUBLICATION:
            raise ValueError("Unsupported publication status.")
        if series is not None and len(str(series).strip()) > 200:
            raise ValueError("Series name cannot exceed 200 characters.")
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
                if tags or removed:
                    result = [tag for tag in book["tags"] if tag.casefold() not in removed]
                    result = list(dict.fromkeys(result + tags))
                    changes["tags"] = json.dumps(result, ensure_ascii=False)
                if rating is not None:
                    changes["rating"] = rating
                if publication_status is not None:
                    changes["publication_status"] = publication_status
                if series is not None:
                    changes["series"] = str(series).strip()
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
        """Only dated activity, interpreted on the machine-local calendar day."""
        year = int(year)
        if not 1900 <= year <= 2200:
            raise ValueError("Choose a valid reading-goal year.")
        label = str(year)
        work_ids = set()
        for row in self.db.execute("SELECT id,finished_at FROM works"):
            if self.activity_day(row["finished_at"]) and (
                self.activity_day(row["finished_at"])[:4] == label
            ):
                work_ids.add(row["id"])
        for row in self.db.execute(
            "SELECT work_id,finished_at FROM reading_runs WHERE finished_at!=''"
        ):
            finished = self.activity_day(row["finished_at"])
            if finished and finished[:4] == label:
                work_ids.add(row["work_id"])
        daily = self.daily_activity(year=year)
        return {
            "year": year, "books": len(work_ids),
            "chapters": sum(day["chapters"] for day in daily.values()),
            "pages": sum(day["pages"] for day in daily.values()),
        }

    def recent_activity(self, limit=10):
        return [dict(row) for row in self.db.execute(
            """SELECT e.recorded_at,e.new_progress,e.old_progress,
            e.note,w.id AS work_id,w.title,w.progress_unit FROM progress_events e
            JOIN works w ON w.id=e.work_id
            ORDER BY e.recorded_at DESC,e.rowid DESC LIMIT ?""",
            (max(1, min(100, int(limit))),)
        )]

    # Versioned custom saved library filters; they travel with JSON/XML backups.
    VIEW_FILTERS = (
        "search", "status", "kind", "favorites", "sort",
        "shelf_id", "series", "tag", "author", "quality"
    )

    @classmethod
    def validate_view_filters(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("Saved view filters must be a dictionary.")
        unknown = set(raw) - set(cls.VIEW_FILTERS)
        if unknown:
            raise ValueError(f"Unknown saved view filter: {min(unknown)}")
        result = {}
        for key in cls.VIEW_FILTERS:
            value = raw.get(key)
            if key == "favorites":
                if value is not None and not isinstance(value, bool):
                    raise ValueError("Favorites filter must be true or false.")
            elif value is not None and not isinstance(value, str):
                raise ValueError(f"Saved view {key} must be text.")
            if isinstance(value, str) and len(value) > 512:
                raise ValueError("Saved view filter is too long.")
            result[key] = value
        if result["status"] not in (None, "all", *STATUSES):
            raise ValueError("Unsupported saved status filter.")
        if result["kind"] not in (None, "all", *KINDS):
            raise ValueError("Unsupported saved type filter.")
        if result["sort"] not in (None, "updated", "title", "author", "added",
                                  "rating", "finished", "series", "progress"):
            raise ValueError("Unsupported saved sorting.")
        if result["quality"] not in (None, "all", "missing_cover", "missing_author",
                                     "missing_description", "missing_source",
                                     "missing_series", "incomplete",
                                     "serial_backlog", "unknown_dates"):
            raise ValueError("Unsupported quality filter.")
        return result

    def save_view(self, name, filters):
        name = str(name).strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("View name must contain between 1 and 80 characters.")
        values = self.validate_view_filters(filters)
        if self.db.execute(
            "SELECT 1 FROM saved_views WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone():
            raise ValueError("A saved view already uses this name.")
        view_id = new_id()
        with self.transaction():
            self.db.execute(
                "INSERT INTO saved_views VALUES(?,?,?,?)",
                (view_id, name, json.dumps(values, ensure_ascii=False), now())
            )
        return view_id

    def list_views(self):
        return [
            {**dict(row), "filters": json.loads(row["filters_json"])}
            for row in self.db.execute(
                "SELECT * FROM saved_views ORDER BY name COLLATE NOCASE"
            )
        ]

    def delete_view(self, view_id):
        with self.transaction():
            self.db.execute("DELETE FROM saved_views WHERE id=?", (view_id,))

    def set_published_units(self, work_id, count, *, publication_status=None):
        count = int(count)
        if not 0 <= count <= 10_000_000:
            raise ValueError("Published units must be between 0 and 10 million.")
        if publication_status is not None and publication_status not in PUBLICATION:
            raise ValueError("Unknown publication status.")
        with self.transaction():
            values = {"total_units": count, "updated_at": now()}
            if publication_status is not None:
                values["publication_status"] = publication_status
            set_sql = ", ".join(f"{field}=?" for field in values)
            cursor = self.db.execute(
                f"UPDATE works SET {set_sql} WHERE id=?",
                (*values.values(), work_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(work_id)

    def catch_up_to_published(self, work_id):
        work = self.get_work(work_id)
        if not work:
            raise KeyError(work_id)
        if work["progress_unit"] not in ("chapters", "pages"):
            raise ValueError("This work isn't tracked by chapters or pages.")
        if work["total_units"] <= 0:
            raise ValueError("Set a known published chapter/page count first.")
        run = self.active_run(work_id)
        progress = run["current_progress"] if run else 0
        if progress > work["total_units"]:
            raise ValueError("Published count is less than current progress.")
        if progress < work["total_units"]:
            self.set_progress(work_id, work["total_units"])
        self.set_status(work_id, "caught_up")
        return work["total_units"] - progress

    def quality_counts(self):
        works = self.list_works()
        counts = {key: 0 for key in (
            "missing_cover", "missing_author", "missing_description",
            "missing_source", "missing_series", "serial_backlog", "unknown_dates",
        )}
        for work in works:
            if not work["cover_url"]:
                counts["missing_cover"] += 1
            if not work["author"]:
                counts["missing_author"] += 1
            if not work["description"]:
                counts["missing_description"] += 1
            if not work["source_url"]:
                counts["missing_source"] += 1
            if not work["series"]:
                counts["missing_series"] += 1
            if self._is_serial_backlog(work):
                counts["serial_backlog"] += 1
            if work["status"] == "completed" and not work["finished_at"]:
                counts["unknown_dates"] += 1
        counts["total"] = len(works)
        return counts

    @staticmethod
    def _is_serial_backlog(work):
        return (
            work["kind"] in ("fanfiction", "web_novel")
            and work["progress_unit"] == "chapters"
            and work["total_units"] > 0
            and 0 < work["progress"] < work["total_units"]
            and work["status"] in ("reading", "caught_up")
        )

    def duplicate_candidates(self):
        """Potential duplicates for review only; never automatically delete works."""
        groups = {}
        for work in self.list_works():
            if work["title"] and work["author"]:
                key = (_book_identity(work["title"]), _book_identity(work["author"]))
                groups.setdefault(key, []).append(work)
        return [
            sorted(works, key=lambda w: w["added_at"])
            for works in groups.values() if len(works) > 1
        ]

    def rename_tag(self, old, new):
        old, new = str(old).strip(), str(new).strip()
        if not old or not new or len(new) > 100:
            raise ValueError("Tag names must contain 1–100 characters.")
        changed = 0
        with self.transaction():
            for work in self.list_works():
                tags = work["tags"]
                if not any(tag.casefold() == old.casefold() for tag in tags):
                    continue
                result = []
                keys = set()
                for tag in tags:
                    target = new if tag.casefold() == old.casefold() else tag
                    if target.casefold() not in keys:
                        result.append(target)
                        keys.add(target.casefold())
                self.db.execute(
                    "UPDATE works SET tags=?,updated_at=? WHERE id=?",
                    (json.dumps(result, ensure_ascii=False), now(), work["id"])
                )
                changed += 1
        return changed

    def delete_tag(self, name):
        name = str(name).strip()
        if not name:
            raise ValueError("Choose a tag to remove.")
        count = 0
        with self.transaction():
            for work in self.list_works():
                result = [tag for tag in work["tags"] if tag.casefold() != name.casefold()]
                if len(result) != len(work["tags"]):
                    self.db.execute(
                        "UPDATE works SET tags=?,updated_at=? WHERE id=?",
                        (json.dumps(result, ensure_ascii=False), now(), work["id"])
                    )
                    count += 1
        return count

    def monthly_activity(self, year):
        """Known dated milestones; missing historical dates remain excluded."""
        year = int(year)
        if not 1900 <= year <= 2200:
            raise ValueError("Choose a valid statistics year.")
        months = [
            {"month": n, "chapters": 0, "pages": 0, "reading_days": 0,
             "events": 0, "completed": 0, "reading_minutes": 0,
             "listening_minutes": 0, "sessions": 0}
            for n in range(1, 13)
        ]
        for iso_day, activity in self.daily_activity(year=year).items():
            month = int(iso_day[5:7]) - 1
            row = months[month]
            row["reading_days"] += 1
            row["chapters"] += activity["chapters"]
            row["pages"] += activity["pages"]
            row["events"] += activity["milestones"]
            row["sessions"] += activity["sessions"]
            row["reading_minutes"] += activity["reading_minutes"]
            row["listening_minutes"] += activity["listening_minutes"]
        # A distinct work counts once per month for each known completion date.
        for work in self.list_works():
            dates = {self.activity_day(run["finished_at"])
                     for run in self.runs(work["id"]) if run["finished_at"]}
            if work["status"] == "completed" and work["finished_at"]:
                dates.add(self.activity_day(work["finished_at"]))
            for finished in dates:
                if finished and finished[:4] == str(year):
                    months[int(finished[5:7]) - 1]["completed"] += 1
        return months

    # Private reading/listening sessions are independent of chapter/page milestones.
    # Duration never estimates progress, and milestones never estimate reading time.
    @staticmethod
    def _day(value):
        from datetime import date as local_date

        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, local_date):
            value = value.isoformat()
        value = str(value or "").strip()
        try:
            parsed = local_date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Use a valid date formatted YYYY-MM-DD.") from exc
        if not 1900 <= parsed.year <= 2200:
            raise ValueError("Date must be between 1900 and 2200.")
        return parsed.isoformat()

    @staticmethod
    def _session_values(read_date, minutes, mode, note):
        day = Library._day(read_date)
        try:
            number = int(minutes)
            if str(minutes).strip() != str(number):
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ValueError("Reading duration must be a whole number of minutes.") from exc
        if not 1 <= number <= 1440:
            raise ValueError("Session duration must be between 1 and 1440 minutes.")
        if mode not in ("reading", "listening"):
            raise ValueError("Choose Reading or Listening.")
        comment = str(note or "").strip()
        if len(comment) > 10000:
            raise ValueError("Session notes must be under 10,000 characters.")
        return day, number, mode, comment

    def add_session(self, work_id, *, read_date, minutes, mode="reading", note=""):
        if not self.get_work(work_id):
            raise ValueError("Choose a book that exists in the library.")
        day, minutes, mode, note = self._session_values(read_date, minutes, mode, note)
        session_id = new_id()
        stamp = now()
        with self.transaction():
            self.db.execute(
                """INSERT INTO reading_sessions
                (id,work_id,read_date,minutes,mode,note,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (session_id, work_id, day, minutes, mode, note, stamp, stamp),
            )
        return session_id

    def get_session(self, session_id):
        row = self.db.execute(
            """SELECT s.*,w.title,w.author FROM reading_sessions s
            JOIN works w ON w.id=s.work_id WHERE s.id=?""", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, *, work_id=None, read_date=None, mode=None, limit=1000):
        sql = """SELECT s.*,w.title,w.author FROM reading_sessions s
                 JOIN works w ON w.id=s.work_id WHERE 1=1"""
        args = []
        if work_id is not None:
            sql += " AND s.work_id=?"
            args.append(work_id)
        if read_date is not None:
            sql += " AND s.read_date=?"
            args.append(self._day(read_date))
        if mode is not None:
            if mode not in ("reading", "listening"):
                raise ValueError("Unknown reading session mode.")
            sql += " AND s.mode=?"
            args.append(mode)
        sql += " ORDER BY s.read_date DESC,s.created_at DESC,s.id DESC LIMIT ?"
        args.append(max(1, min(50000, int(limit))))
        return [dict(row) for row in self.db.execute(sql, args)]

    def update_session(self, session_id, *, read_date, minutes, mode="reading", note=""):
        values = self._session_values(read_date, minutes, mode, note)
        with self.transaction():
            cursor = self.db.execute(
                """UPDATE reading_sessions SET read_date=?,minutes=?,mode=?,
                note=?,updated_at=? WHERE id=?""",
                (*values, now(), session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Reading session does not exist.")

    def delete_session(self, session_id):
        with self.transaction():
            self.db.execute("DELETE FROM reading_sessions WHERE id=?", (session_id,))

    @staticmethod
    def _annotation_values(kind, body, location, location_unit):
        if kind not in ("bookmark", "quote", "note"):
            raise ValueError("Annotation must be a bookmark, quote or note.")
        body = str(body or "").strip()
        if kind in ("quote", "note") and not body:
            raise ValueError("Quotes and notes need text.")
        if len(body) > 20000:
            raise ValueError("Annotations must be under 20,000 characters.")
        if location in ("", None):
            location = None
        else:
            try:
                number = int(location)
                if str(number) != str(location).strip():
                    raise ValueError()
                location = number
            except (TypeError, ValueError) as exc:
                raise ValueError("Location must be a whole-number chapter or page.") from exc
            if not 0 <= location <= 10000000:
                raise ValueError("Location is outside the supported range.")
        if location_unit not in ("", *UNITS):
            raise ValueError("Unknown bookmark location unit.")
        if location is None:
            location_unit = ""
        elif not location_unit:
            location_unit = "chapters"
        return kind, location, location_unit, body

    def add_annotation(self, work_id, *, kind, body="", location=None,
                       location_unit=""):
        if not self.get_work(work_id):
            raise ValueError("Annotation must refer to an existing book.")
        values = self._annotation_values(kind, body, location, location_unit)
        ident = new_id()
        stamp = now()
        with self.transaction():
            self.db.execute(
                """INSERT INTO annotations
                (id,work_id,kind,location,location_unit,body,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (ident, work_id, *values, stamp, stamp),
            )
        return ident

    def get_annotation(self, annotation_id):
        row = self.db.execute(
            """SELECT a.*,w.title,w.author FROM annotations a
            JOIN works w ON w.id=a.work_id WHERE a.id=?""", (annotation_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_annotations(self, *, work_id=None, kind=None, search="", limit=5000):
        sql = """SELECT a.*,w.title,w.author FROM annotations a
                 JOIN works w ON w.id=a.work_id WHERE 1=1"""
        args = []
        if work_id is not None:
            sql += " AND a.work_id=?"
            args.append(work_id)
        if kind is not None:
            if kind not in ("bookmark", "quote", "note"):
                raise ValueError("Invalid annotation type.")
            sql += " AND a.kind=?"
            args.append(kind)
        if search.strip():
            escaped = (
                search.strip().replace("\\", "\\\\")
                .replace("%", "\\%").replace("_", "\\_")
            )
            sql += (" AND (a.body LIKE ? ESCAPE '\\' OR w.title LIKE ? ESCAPE '\\'"
                    " OR w.author LIKE ? ESCAPE '\\')")
            args.extend([f"%{escaped}%"] * 3)
        sql += " ORDER BY a.updated_at DESC,a.id DESC LIMIT ?"
        args.append(max(1, min(50000, int(limit))))
        return [dict(row) for row in self.db.execute(sql, args)]

    def update_annotation(self, annotation_id, *, kind, body="", location=None,
                          location_unit=""):
        fields = self._annotation_values(kind, body, location, location_unit)
        with self.transaction():
            cursor = self.db.execute(
                """UPDATE annotations SET kind=?,location=?,location_unit=?,
                body=?,updated_at=? WHERE id=?""",
                (*fields, now(), annotation_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Annotation does not exist.")

    def delete_annotation(self, annotation_id):
        with self.transaction():
            self.db.execute("DELETE FROM annotations WHERE id=?", (annotation_id,))

    @staticmethod
    def activity_day(timestamp):
        """Convert offset-aware event timestamps to machine-local calendar dates.

        Naive timestamps and YYYY-MM-DD historical dates retain the entered day.
        """
        try:
            parsed = datetime.fromisoformat(str(timestamp).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        if not 1900 <= parsed.year <= 2200:
            return None
        return parsed.date().isoformat()

    def milestones_on(self, read_date):
        from datetime import timedelta

        day = self._day(read_date)
        parsed = date_class.fromisoformat(day)
        nearby = [
            (parsed + timedelta(days=i)).isoformat()
            for i in (-1, 0, 1)
        ]
        return [
            dict(row) for row in self.db.execute(
                """SELECT e.*,w.title,w.author,w.progress_unit
                   FROM progress_events e JOIN works w ON w.id=e.work_id
                   WHERE SUBSTR(e.recorded_at,1,10) IN (?,?,?)
                   ORDER BY e.recorded_at DESC,e.rowid DESC""",
                nearby,
            )
            if self.activity_day(row["recorded_at"]) == day
        ]

    def daily_activity(self, *, year=None, month=None):
        """Activity derived solely from dated events and explicitly logged sessions.

        Entries are keyed by ISO local calendar date. A progress milestone counts
        as one activity event, a session counts once, and reading days use their
        union. Session minutes are never inferred from milestones.
        """
        if year is not None and not 1900 <= int(year) <= 2200:
            raise ValueError("Year outside supported range.")
        if month is not None and (year is None or not 1 <= int(month) <= 12):
            raise ValueError("Month requires a year and must be 1–12.")
        prefix = (
            f"{int(year):04d}-{int(month):02d}" if month is not None
            else f"{int(year):04d}" if year is not None else None
        )
        result = {}

        def day_bucket(day):
            return result.setdefault(day, {
                "date": day, "milestones": 0, "sessions": 0,
                "reading_minutes": 0, "listening_minutes": 0,
                "chapters": 0, "pages": 0,
            })
        statement = """SELECT e.recorded_at,e.old_progress,e.new_progress,
            w.progress_unit AS unit FROM progress_events e
            JOIN works w ON w.id=e.work_id"""
        for row in self.db.execute(statement):
            day = self.activity_day(row["recorded_at"])
            if not day or (prefix and not day.startswith(prefix)):
                continue
            item = day_bucket(day)
            item["milestones"] += 1
            if row["unit"] in ("chapters", "pages"):
                item[row["unit"]] += max(
                    0, row["new_progress"] - row["old_progress"]
                )
        sessions = """SELECT read_date,mode,SUM(minutes) AS duration,
                  COUNT(*) AS total FROM reading_sessions WHERE 1=1"""
        args = []
        if prefix:
            sessions += " AND SUBSTR(read_date,1,?)=?"
            args.extend((len(prefix), prefix))
        sessions += " GROUP BY read_date,mode"
        for row in self.db.execute(sessions, args):
            item = day_bucket(row["read_date"])
            item["sessions"] += row["total"]
            item["reading_minutes" if row["mode"] == "reading"
                 else "listening_minutes"] += row["duration"]
        return dict(sorted(result.items()))

    def reading_streak(self, *, today=None):
        from datetime import date as local_date
        from datetime import timedelta

        today = local_date.fromisoformat(self._day(today or local_date.today()))
        active_days = set(self.daily_activity())
        current = today if today.isoformat() in active_days else today - timedelta(days=1)
        streak = 0
        while current.isoformat() in active_days:
            streak += 1
            current -= timedelta(days=1)
        return streak

    def session_totals(self, *, year=None):
        sql = """SELECT mode,COALESCE(SUM(minutes),0) AS minutes,COUNT(*) AS sessions
                FROM reading_sessions WHERE 1=1"""
        args = []
        if year is not None:
            if not 1900 <= int(year) <= 2200:
                raise ValueError("Year outside supported range.")
            sql += " AND SUBSTR(read_date,1,4)=?"
            args.append(str(int(year)))
        sql += " GROUP BY mode"
        result = {"reading_minutes": 0, "listening_minutes": 0, "sessions": 0}
        for item in self.db.execute(sql, args):
            result[item["mode"] + "_minutes"] = item["minutes"]
            result["sessions"] += item["sessions"]
        return result

    def export_data(self):
        return {"format": "booktracker", "schema_version": 5, "exported_at": now(),
                "works": [dict(row) for row in self.db.execute("SELECT * FROM works")],
                "reading_runs": [dict(row) for row in self.db.execute("SELECT * FROM reading_runs")],
                "progress_events": [dict(row) for row in self.db.execute("SELECT * FROM progress_events")],
                "shelves": [dict(row) for row in self.db.execute("SELECT * FROM shelves")],
                "work_shelves": [dict(row) for row in self.db.execute("SELECT * FROM work_shelves")],
                "reading_goals": [dict(row) for row in self.db.execute("SELECT * FROM reading_goals")],
                "saved_views": [dict(row) for row in self.db.execute("SELECT * FROM saved_views")],
                "reading_sessions": [dict(row) for row in self.db.execute("SELECT * FROM reading_sessions")],
                "annotations": [dict(row) for row in self.db.execute("SELECT * FROM annotations")]}

    def import_data(self, data, *, strategy="merge"):
        """Transactional import; duplicate event IDs are skipped when merging."""
        if data.get("format") != "booktracker" or data.get("schema_version") not in (1, 2, 3, 4, 5):
            raise ValueError("Unsupported BookTracker backup format or schema version.")
        if data["schema_version"] >= 2 and (
            not isinstance(data.get("shelves"), list)
            or not isinstance(data.get("work_shelves"), list)
        ):
            raise ValueError("Backup is missing shelf information.")
        if data["schema_version"] >= 3 and not isinstance(data.get("reading_goals"), list):
            raise ValueError("Backup is missing reading goals.")
        if data["schema_version"] >= 4 and not isinstance(data.get("saved_views"), list):
            raise ValueError("Backup is missing saved library views.")
        if data["schema_version"] >= 5 and (
            not isinstance(data.get("reading_sessions"), list)
            or not isinstance(data.get("annotations"), list)
        ):
            raise ValueError("Backup is missing sessions or annotations.")
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
                self.db.execute("DELETE FROM saved_views")
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
            for item in data.get("saved_views", []):
                try:
                    view_id = str(item["id"]).strip()
                    name = str(item["name"]).strip()
                    filters = self.validate_view_filters(json.loads(item["filters_json"]))
                    if filters["shelf_id"] in shelf_map:
                        filters["shelf_id"] = shelf_map[filters["shelf_id"]]
                except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    raise ValueError("Malformed saved view in backup.") from exc
                if not view_id or not 1 <= len(name) <= 80:
                    raise ValueError("Malformed saved view name or ID.")
                self.db.execute(
                    "INSERT OR IGNORE INTO saved_views VALUES(?,?,?,?)",
                    (view_id, name, json.dumps(filters, ensure_ascii=False),
                     item.get("created_at") or now()),
                )
            for session in data.get("reading_sessions", []):
                if not isinstance(session, dict) or not session.get("id"):
                    raise ValueError("Malformed reading session.")
                work_id = mapping.get(session.get("work_id"))
                if not work_id:
                    raise ValueError("Orphaned reading session.")
                day, minutes, mode, comment = self._session_values(
                    session.get("read_date"), session.get("minutes"),
                    session.get("mode"), session.get("note", ""),
                )
                self.db.execute(
                    """INSERT OR IGNORE INTO reading_sessions
                    (id,work_id,read_date,minutes,mode,note,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (session["id"], work_id, day, minutes, mode, comment,
                     session.get("created_at") or now(),
                     session.get("updated_at") or now()),
                )
            for item in data.get("annotations", []):
                if not isinstance(item, dict) or not item.get("id"):
                    raise ValueError("Malformed book annotation.")
                work_id = mapping.get(item.get("work_id"))
                if not work_id:
                    raise ValueError("Orphaned book annotation.")
                fields = self._annotation_values(
                    item.get("kind"), item.get("body", ""),
                    item.get("location"), item.get("location_unit", ""),
                )
                self.db.execute(
                    """INSERT OR IGNORE INTO annotations
                    (id,work_id,kind,location,location_unit,body,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (item["id"], work_id, *fields,
                     item.get("created_at") or now(),
                     item.get("updated_at") or now()),
                )
        return count
