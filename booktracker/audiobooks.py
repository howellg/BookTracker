"""Offline audiobook discovery and AudiobookAI Studio interoperability.

Never imports AudiobookAI's Python modules, runs a synthesis engine, modifies
its database, or reads entire M4B files. External media metadata is untrusted.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

MAX_SCAN_FILES = 15000
MAX_CANDIDATES = 1200
MAX_DEPTH = 5
MAX_DB_ROWS = 10000
MAX_JSON_BYTES = 3_000_000

_PART_PATTERNS = (
    re.compile(r"^(?P<title>.+?)\s*-\s*Part\((?P<part>\d+)\)(?: \(\d+\))?$", re.IGNORECASE),
    re.compile(r"^(?P<title>.+?)\s*-\s*Part (?P<part>\d+)(?: \(Sections [^)]+\))?(?: \(\d+\))?$", re.IGNORECASE),
    re.compile(
        r"^(?P<title>.+?)[_ -]part_(?P<part>\d+)_sections_\d+(?:-\d+)?"
        r"(?:_[a-f0-9]{8,})?$", re.IGNORECASE
    ),
)
_LEGACY_SECTIONS = re.compile(r"[_ (]sections[_ (]+(\d+)[-–](\d+)", re.IGNORECASE)


def identify_output(filename: str) -> tuple[str, int, bool, int, int]:
    """Return (title, part number, complete, section first, section last).

    AudiobookAI current, legacy and archive conventions are recognized.
    Generic filenames are allowed but never given an invented part number.
    """
    stem = Path(filename).stem
    complete = bool(re.search(r"[_ -]COMPLETE$", stem, re.IGNORECASE))
    if complete:
        stem = re.sub(r"[_ -]COMPLETE$", "", stem, flags=re.IGNORECASE)
    part = 0
    first = last = 0
    if not complete:
        for pattern in _PART_PATTERNS:
            found = pattern.fullmatch(stem)
            if found:
                stem = found.group("title")
                part = min(int(found.group("part")), 1000000)
                break
        match = _LEGACY_SECTIONS.search(Path(filename).stem)
        if match:
            first, last = map(int, match.groups())
            if not (1 <= first <= last <= 10000000):
                first = last = 0
    title = re.sub(r"[_\s]+", " ", stem).strip(" -.")[:500]
    return title or Path(filename).stem[:500], part, complete, first, last


def _metadata_string(value, limit=500):
    return str(value or "").replace("\x00", "").strip()[:limit]


def probe_m4b(path: str | Path, *, timeout=15) -> dict:
    """Inspect an M4B with ffprobe. No audio decoded or transcoded.

    Absence of ffprobe is nonfatal and produces explicitly unknown metadata.
    """
    file = Path(path).expanduser()
    if file.suffix.lower() != ".m4b" or not file.is_file():
        raise ValueError("Choose an available M4B audiobook file.")
    result = {
        "duration_ms": 0, "chapter_count": 0, "codec": "",
        "title": "", "author": "", "narrator": "", "chapters": [],
        "probe_status": "unavailable",
    }
    if not shutil.which("ffprobe"):
        result["probe_status"] = "ffprobe missing"
        return result
    command = [
        "ffprobe", "-hide_banner", "-v", "error",
        "-show_format", "-show_streams", "-show_chapters",
        "-of", "json", str(file),
    ]
    try:
        process = subprocess.run(
            command, capture_output=True, timeout=max(1, min(30, int(timeout))),
            check=False,
        )
        if process.returncode:
            result["probe_status"] = "Could not read audio metadata"
            return result
        if len(process.stdout) > MAX_JSON_BYTES:
            result["probe_status"] = "Metadata exceeds safety limit"
            return result
        parsed = json.loads(process.stdout)
        if not isinstance(parsed, dict):
            return result
        audio = next(
            (stream for stream in parsed.get("streams", [])
             if isinstance(stream, dict) and stream.get("codec_type") == "audio"),
            None,
        )
        if not audio:
            result["probe_status"] = "No audio stream detected"
            return result
        tags = {
            str(k).casefold(): str(v) for k,v in
            (parsed.get("format", {}).get("tags") or {}).items()
        }
        # AudiobookAI full archives use title, artist, and composer=narrator.
        for key,value in (audio.get("tags") or {}).items():
            tags.setdefault(str(key).casefold(), str(value))
        duration = float(parsed.get("format", {}).get("duration") or 0)
        if 0 < duration < 10**9:
            result["duration_ms"] = round(duration * 1000)
        result["codec"] = _metadata_string(audio.get("codec_name"), 60)
        result["title"] = _metadata_string(tags.get("album") or tags.get("title"))
        result["author"] = _metadata_string(
            tags.get("album_artist") or tags.get("artist")
        )
        result["narrator"] = _metadata_string(tags.get("composer"))
        chapters = []
        raw_chapters = parsed.get("chapters") or []
        if not isinstance(raw_chapters,list) or len(raw_chapters)>100000:
            result["probe_status"] = "Invalid chapter list"
            return result
        for entry in raw_chapters:
            if not isinstance(entry,dict):
                continue
            try:
                start = float(entry.get("start_time") or 0)
                end = float(entry.get("end_time") or 0)
            except (ValueError,TypeError):
                continue
            if not 0 <= start <= end < 10**9:
                continue
            chapters.append({
                "start_ms": round(start*1000),
                "end_ms": round(end*1000),
                "title": _metadata_string(
                    (entry.get("tags") or {}).get("title"), 250
                ),
            })
        result["chapters"] = chapters
        result["chapter_count"] = len(chapters)
        result["probe_status"] = "ok"
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError,
            json.JSONDecodeError, OverflowError):
        result["probe_status"] = "Could not read audio metadata"
    return result


def _candidate(
    *, path, title="", author="", narrator="", part=0, complete=False,
    section_first=0, section_last=0, origin="folder", studio_id=0,
    source_name="",
) -> dict:
    raw_path = Path(path).expanduser()
    filename = raw_path.name
    if not filename or raw_path.suffix.lower() != ".m4b":
        raise ValueError("Only M4B audiobook outputs may be imported.")
    parsed_title, guessed_part, guessed_complete, guessed_first, guessed_last = identify_output(filename)
    available = raw_path.is_file() and not raw_path.is_symlink()
    if available:
        resolved = raw_path.resolve(strict=True)
        size = resolved.stat().st_size
        clean_path = str(resolved)
    else:
        size = 0
        clean_path = ""
    return {
        "filename":filename, "path":clean_path,"available":available,
        "size_bytes":size, "title":_metadata_string(title or parsed_title),
        "author":_metadata_string(author),
        "narrator":_metadata_string(narrator, 180),
        "part_number":int(part or guessed_part or 0),
        "is_complete":bool(complete or guessed_complete),
        "section_first":int(section_first or guessed_first or 0),
        "section_last":int(section_last or guessed_last or 0),
        "origin":str(origin), "studio_book_id":int(studio_id or 0),
        "source_name":_metadata_string(source_name or "M4B", 180),
        "duration_ms":0,"chapter_count":0,"codec":"","probe_status":"not checked",
        "chapters":[],
    }


def _enhance(record: dict) -> dict:
    """Probe available files, preferring explicit AudiobookAI ledger metadata."""
    if record["available"]:
        info = probe_m4b(record["path"])
        record.update({
            k:info[k] for k in (
                "duration_ms", "chapter_count", "codec", "probe_status", "chapters"
            )
        })
        # Tagged title may mean a *chapter* in some generated part files;
        # prefer the Studio ledger when present.
        if not record["title"]:
            record["title"] = info["title"]
        if not record["author"]:
            record["author"] = info["author"]
        if not record["narrator"]:
            record["narrator"] = info["narrator"]
    else:
        record["probe_status"] = "Missing local file"
    return record


def scan_audiobookai_database(path: str | Path, *, cancel=None) -> dict:
    """Read AudiobookAI Studio's completed-parts ledger via read-only SQLite.

    No AudiobookAI code, queue edits, MySQL, or writeable SQLite connections.
    Missing completed files can be carried into BookTracker as relinkable hints.
    """
    file = Path(path).expanduser()
    if not file.is_file() or file.suffix.lower() not in (".sqlite3",".db",".sqlite"):
        raise ValueError("Choose an existing AudiobookAI Studio SQLite database.")
    db = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    records = []
    try:
        db.execute("PRAGMA query_only=ON")
        tables = {
            entry[0] for entry in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"books","completed_parts"}.issubset(tables):
            raise ValueError("This is not an AudiobookAI Studio completed-parts database.")
        query = """SELECT c.id,c.book_id,c.part_number,c.indices,c.output_path,
                          c.output_name,c.voice_path,c.size_bytes,
                          b.title AS book_title,b.author AS book_author
                   FROM completed_parts c JOIN books b ON b.id=c.book_id
                   ORDER BY b.title COLLATE NOCASE,c.part_number,c.id
                   LIMIT ?"""
        rows=db.execute(query,(MAX_DB_ROWS+1,)).fetchall()
        if len(rows)>MAX_DB_ROWS:
            raise ValueError("AudiobookAI has too many completed exports for one import.")
        seen=set()
        for row in rows:
            if cancel and cancel():
                return {"records":[],"cancelled":True,"skipped":0}
            raw=str(row["output_path"] or "")
            if not raw or not Path(raw).is_absolute() or not raw.lower().endswith(".m4b"):
                continue
            try:
                indices=json.loads(row["indices"] or "[]")
                if not isinstance(indices,list) or len(indices)>100000:
                    indices=[]
                valid_indices=[
                    number for number in indices if type(number) is int and 0 < number < 10000000
                ]
                first=min(valid_indices) if valid_indices else 0
                last=max(valid_indices) if valid_indices else 0
                part=max(0,int(row["part_number"] or 0))
                voice = Path(row["voice_path"]).stem if row["voice_path"] else ""
                record=_candidate(
                    path=raw,title=row["book_title"],author=row["book_author"],
                    narrator=voice,part=part,section_first=first,section_last=last,
                    studio_id=row["book_id"],origin="audiobookai",
                    source_name="AudiobookAI completed part",
                )
                if record["path"] in seen and record["path"]:
                    continue
                if record["path"]:
                    seen.add(record["path"])
                records.append(_enhance(record))
            except (ValueError,TypeError,OSError):
                continue
        # The archive catalog may describe full exports that were not part of
        # completed_parts. Its keys include device-local paths; do not export them.
        catalog=file.parent/"archive"/"catalog.json"
        if catalog.is_file() and catalog.stat().st_size <= MAX_JSON_BYTES:
            try:
                data=json.loads(catalog.read_text(encoding="utf-8"))
                # Link archive editions to their original Studio book when
                # the catalog's source EPUB exactly matches the Studio record.
                studio_paths={
                    str(Path(row["path"]).resolve(strict=False)): row["id"]
                    for row in db.execute("SELECT id,path FROM books")
                }
                for catalog_key,item in (data.get("editions") or {}).items():
                    if cancel and cancel():
                        return {"records":[],"cancelled":True,"skipped":0}
                    if not isinstance(item,dict) or not item.get("last_export"):
                        continue
                    raw=Path(str(item["last_export"]))
                    if not raw.is_absolute() or raw.suffix.lower()!=".m4b":
                        continue
                    if str(raw.resolve(strict=False)) in seen:
                        continue
                    epub=str(catalog_key).split("\n",1)[0]
                    book_id=studio_paths.get(
                        str(Path(epub).resolve(strict=False)),0
                    )
                    record=_candidate(
                        path=raw,title=item.get("title"),author=item.get("author"),
                        narrator=item.get("narrator"),complete=True,
                        studio_id=book_id,source_name="AudiobookAI archival edition",
                        origin="archive",
                    )
                    if record["path"]:
                        seen.add(record["path"])
                    records.append(_enhance(record))
                    if len(records)>=MAX_DB_ROWS:
                        break
            except (OSError,TypeError,ValueError,json.JSONDecodeError):
                pass
        return {"records":records,"cancelled":False,"skipped":0}
    finally:
        db.close()


def scan_m4b_folder(directory: str | Path, *, cancel=None, max_files=MAX_SCAN_FILES) -> dict:
    """Bounded directory traversal; no symlink recursion, file writes or networking."""
    root = Path(directory).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose an audiobook directory.")
    max_files=int(max_files)
    if not 1<=max_files<=MAX_SCAN_FILES:
        raise ValueError("Invalid audiobook scan size.")
    candidates=[]
    scanned=0
    for parent,folders,files in os.walk(root, followlinks=False):
        if cancel and cancel():
            return {"records":[],"cancelled":True,"skipped":0}
        base=Path(parent)
        depth=len(base.relative_to(root).parts)
        folders[:]=[] if depth>=MAX_DEPTH else sorted(
            folder for folder in folders if not (base/folder).is_symlink()
        )
        for name in sorted(files):
            scanned+=1
            if scanned>max_files:
                raise ValueError("Folder contains too many files; choose a smaller folder.")
            if not name.casefold().endswith(".m4b") or (base/name).is_symlink():
                continue
            candidates.append(base/name)
            if len(candidates)>MAX_CANDIDATES:
                raise ValueError(
                    "Too many M4B files for one import. Choose a smaller folder."
                )
    records=[]
    for file in candidates:
        if cancel and cancel():
            return {"records":[],"cancelled":True,"skipped":scanned}
        item=_candidate(path=file,source_name="M4B folder")
        info=probe_m4b(item["path"])
        # On ordinary M4Bs, embedded album/title and author tags are better
        # than a raw filename. On AudiobookAI parts, the part suffix matters.
        item.update({
            k:info[k] for k in (
                "duration_ms","chapter_count","codec","probe_status","chapters"
            )
        })
        if not item["part_number"] and not item["is_complete"]:
            item["title"] = info["title"] or item["title"]
        item["author"] = info["author"]
        item["narrator"] = info["narrator"]
        if not item["narrator"] and file.parent.name.lower() not in (
            "output","audio","m4b","export","exports"
        ):
            # Folder names are not reliable identity evidence; do not assign.
            pass
        records.append(item)
    return {"records":records,"cancelled":False,"skipped":scanned-len(candidates)}


def group_candidates(records: list[dict]) -> list[dict]:
    """Return independent source-book groups for explicit GUI matching."""
    buckets=defaultdict(list)
    for index,item in enumerate(records):
        identity=(str(item.get("title") or "").casefold().strip(),
                  str(item.get("author") or "").casefold().strip(),
                  item.get("studio_book_id")
                  if item.get("origin") in ("audiobookai","archive") else 0)
        buckets[identity].append((index,item))
    groups=[]
    for key,items in buckets.items():
        groups.append({
            "key":key, "title":items[0][1]["title"],
            "author":items[0][1]["author"],
            "indices":[index for index,_ in items],
            "count":len(items),
            "missing":sum(not entry.get("available",False) for _,entry in items),
            "total_duration_ms":sum(int(entry.get("duration_ms") or 0)
                                    for _,entry in items
                                    if not entry.get("is_complete")),
            "has_complete":any(entry.get("is_complete",False) for _,entry in items),
        })
    return sorted(groups,key=lambda g:(g["title"].casefold(),g["author"].casefold()))
