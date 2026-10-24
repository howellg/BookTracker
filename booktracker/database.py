"""Local-first SQLite storage for works, rereads, and progress history."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
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
        """)
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
            if fields.get("status") in ("reading", "caught_up", "completed"):
                run_id = self._begin_run(work_id, fields.get("started_at") or timestamp)
                if fields.get("status") == "completed":
                    self.db.execute("UPDATE reading_runs SET finished_at=?,is_active=0 WHERE id=?",
                                    (fields.get("finished_at") or timestamp, run_id))
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

    def list_works(self, search="", status="all", kind="all", favorites=False, sort="updated") -> list[dict]:
        sql = """SELECT w.*, COALESCE((
            SELECT current_progress FROM reading_runs
            WHERE work_id=w.id ORDER BY started_at DESC, rowid DESC LIMIT 1
        ),0) AS progress
        FROM works w WHERE 1=1"""
        args = []
        if search:
            sql += " AND (w.title LIKE ? OR w.author LIKE ? OR w.fandom LIKE ? OR w.tags LIKE ? OR w.series LIKE ?)"
            args.extend([f"%{search}%"] * 5)
        if status != "all":
            sql += " AND w.status=?"
            args.append(status)
        if kind != "all":
            sql += " AND w.kind=?"
            args.append(kind)
        if favorites:
            sql += " AND w.favorite=1"
        order = {
            "updated": "w.updated_at DESC", "title": "w.title COLLATE NOCASE",
            "author": "w.author COLLATE NOCASE", "added": "w.added_at DESC",
            "rating": "w.rating DESC NULLS LAST",
        }
        sql += " ORDER BY " + order.get(sort, order["updated"])
        rows = [dict(r) for r in self.db.execute(sql, args)]
        for r in rows:
            r["tags"] = json.loads(r["tags"])
        return rows

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

    def export_data(self):
        return {"format": "booktracker", "schema_version": 1, "exported_at": now(),
                "works": [dict(row) for row in self.db.execute("SELECT * FROM works")],
                "reading_runs": [dict(row) for row in self.db.execute("SELECT * FROM reading_runs")],
                "progress_events": [dict(row) for row in self.db.execute("SELECT * FROM progress_events")]}

    def import_data(self, data, *, strategy="merge"):
        """Transactional import; duplicate event IDs are skipped when merging."""
        if data.get("format") != "booktracker" or data.get("schema_version") != 1:
            raise ValueError("Unsupported BookTracker backup format or schema version.")
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
                    fields["updated_at"] = now()
                    keys = ", ".join(f"{k}=?" for k in fields)
                    self.db.execute(f"UPDATE works SET {keys} WHERE id=?",
                                    (*fields.values(), target_id))
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
        return count
