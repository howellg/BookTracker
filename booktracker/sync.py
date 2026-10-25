"""Safety-first BookTracker shared-folder snapshot synchronization (no DB-file sharing).

Each device publishes a complete, immutable, portable archive to an external
folder (e.g. Syncthing). Peer imports have an explicit review/consent step.
Changes are classified with locally retained three-way baselines, and local
deletions never get silently recreated by an older peer snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .database import WORK_FIELDS, Library, _book_identity, now
from .portable_backup import (
    MAX_MANIFEST_BYTES,
    export_portable,
    import_portable,
)

FILE_RE = re.compile(r"^booktracker-([0-9a-f-]{36})-(\d{9})\.btsync$")
MAX_SNAPSHOT_BYTES = 700_000_000
MAX_PEERS = 24
RELEVANT_TABLES = (
    "works", "reading_runs", "progress_events", "reading_sessions",
    "annotations", "shelves", "work_shelves", "reading_goals",
    "saved_views", "work_sources", "work_editions", "source_checks",
    "reading_queue", "local_files", "serial_follows", "serial_releases",
    "audio_metadata", "epub_positions", "epub_bookmarks",
)
KEYS = {
    "works": ("id",), "reading_runs": ("id",), "progress_events": ("id",),
    "reading_sessions": ("id",), "annotations": ("id",), "shelves": ("id",),
    "work_shelves": ("work_id","shelf_id"), "reading_goals": ("year",),
    "saved_views": ("id",), "work_sources": ("id",), "work_editions": ("id",),
    "source_checks": ("id",), "reading_queue": ("id",),
    "local_files": ("id",), "serial_follows": ("work_id",),
    "serial_releases": ("id",), "audio_metadata": ("file_id",),
    "epub_positions": ("file_id",), "epub_bookmarks": ("id",),
}
# DB must still validate and import these rows; only non-identity fields
# explicitly supported here may be updated in place after safe comparison.
EDITABLE = {
    "works": set(WORK_FIELDS) - {"source_site","source_id","isbn","source_url"},
    "reading_sessions": {"read_date","minutes","mode","note"},
    "annotations": {"kind","location","location_unit","body"},
    "progress_events": {"recorded_at","new_progress","note"},
    "reading_goals": {"books_target","chapters_target","pages_target"},
    "serial_follows": {"notify","seen_units"},
    "saved_views": {"filters_json"},
    "epub_positions": {"section_index","section_href","scroll_ratio"},
    "epub_bookmarks": {"label"},
}
IGNORE = {"updated_at","created_at","added_at","followed_at","checked_at"}

def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",",":"))

def _pack(data):
    raw=_json(data).encode("utf-8")
    if len(raw)>MAX_MANIFEST_BYTES:
        raise ValueError("Sync baseline too large.")
    return zlib.compress(raw,6)

def _unpack(raw):
    if not raw:
        return {}
    decomp=zlib.decompressobj()
    result=decomp.decompress(raw,MAX_MANIFEST_BYTES+1)
    if len(result)>MAX_MANIFEST_BYTES or decomp.unconsumed_tail:
        raise ValueError("Sync baseline exceeds safety limit.")
    return json.loads(result+decomp.flush())

def _key(table,row):
    return tuple(str(row.get(col)) for col in KEYS[table])

def _rows(data,table):
    return {_key(table,row):row for row in data.get(table,[])}

def _baseline(data):
    return {table:data.get(table,[]) for table in RELEVANT_TABLES}

def device_info(library:Library):
    row=library.db.execute("SELECT * FROM sync_device WHERE singleton=1").fetchone()
    return dict(row)

def set_device_name(library:Library,name):
    name=str(name).strip()
    if not 1<=len(name)<=64 or any(ord(c)<32 for c in name):
        raise ValueError("Computer name must contain 1–64 printable characters.")
    with library.transaction():
        library.db.execute("UPDATE sync_device SET display_name=? WHERE singleton=1",(name,))

def new_device_identity(library:Library):
    """Call only after explicit user confirmation for cloned databases.

    Start independent source revisions and baselines. It never changes works,
    reading history, file metadata or covers.
    """
    device_id=str(uuid.uuid4())
    with library.transaction():
        library.db.execute(
            "UPDATE sync_device SET device_id=?,published_revision=0 WHERE singleton=1",
            (device_id,)
        )
        library.db.execute("DELETE FROM sync_peers")
    return device_id


def _directory(folder, *, create=False):
    path=Path(folder).expanduser()
    if create:
        path.mkdir(parents=True,exist_ok=True)
    path=path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Choose a shared sync folder.")
    return path

def _file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(256*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()

def publish(library:Library,folder):
    """Publish a new immutable revision via same-folder atomic rename.

    No live SQLite DB/WAL files are copied. Never remove peer-owned files.
    """
    root=_directory(folder,create=True)
    database=library.path.resolve(strict=True)
    if root in database.parents:
        raise ValueError(
            "The sync folder contains your live SQLite database. "
            "Choose a separate shared subfolder, not the database directory."
        )
    device=device_info(library)
    device_id=device["device_id"]
    revision=int(device["published_revision"])+1
    target=root/f"booktracker-{device_id}-{revision:09d}.btsync"
    if target.exists():
        raise ValueError("A revision already exists. Check for a copied device identity.")
    # Export uses the existing archive validation and checksums for embedded covers.
    with tempfile.TemporaryDirectory(prefix=".bt-sync-",dir=root) as scratch:
        temp=Path(scratch)
        backup=temp/"full.btbackup"
        export_portable(library,backup)
        with zipfile.ZipFile(backup) as reader:
            library_hash=hashlib.sha256(reader.read("library.json")).hexdigest()
        # Move into a hidden pending file and append the sync envelope while
        # still invisible to peer scanners. No modification after publication.
        pending=temp/"publish.btsync"
        os.replace(backup,pending)
        meta={
            "protocol":"booktracker-sync","version":1,
            "device_id":device_id,"device_name":device["display_name"],
            "revision":revision,"created_at":now(),
            "library_sha256":library_hash,
        }
        with zipfile.ZipFile(pending,"a",compression=zipfile.ZIP_DEFLATED) as zipout:
            zipout.writestr("sync.json",_json(meta))
        if pending.stat().st_size>MAX_SNAPSHOT_BYTES:
            raise ValueError("Sync export exceeds size limit.")
        # SQLite published counter commits BEFORE revealing the file, to avoid
        # reusing a revision after a crash. A gap is harmless.
        with library.transaction():
            updated=library.db.execute(
                """UPDATE sync_device SET published_revision=?
                   WHERE singleton=1 AND published_revision=?""",
                (revision,device["published_revision"])
            )
            if updated.rowcount!=1:
                raise ValueError("Another publish operation changed this device's revision.")
        os.replace(pending,target)
    own=sorted(root.glob(f"booktracker-{device_id}-*.btsync"))
    for previous in own[:-3]:
        try:
            previous.unlink()
        except OSError:
            pass
    return target

@dataclass
class PeerSnapshot:
    path:Path
    device_id:str
    device_name:str
    revision:int
    archive_hash:str
    data:dict

def read_snapshot(path):
    file=Path(path)
    match=FILE_RE.fullmatch(file.name)
    if not match or not file.is_file() or file.is_symlink():
        raise ValueError("Not a supported BookTracker sync snapshot.")
    if file.stat().st_size>MAX_SNAPSHOT_BYTES:
        raise ValueError("Sync file exceeds safe size limit.")
    try:
        with zipfile.ZipFile(file) as z:
            names=z.namelist()
            if names.count("sync.json")!=1 or names.count("library.json")!=1:
                raise ValueError("Sync archive has missing or duplicate manifests.")
            if z.getinfo("sync.json").file_size>4096:
                raise ValueError("Sync envelope is too large.")
            if z.getinfo("library.json").file_size>MAX_MANIFEST_BYTES:
                raise ValueError("Sync library is too large.")
            header=json.loads(z.read("sync.json"))
            raw=z.read("library.json")
        if not isinstance(header,dict) or header.get("protocol")!="booktracker-sync" or header.get("version")!=1:
            raise ValueError("Unknown sync format.")
        uid=str(uuid.UUID(str(header.get("device_id"))))
        revision=header.get("revision")
        if (uid!=match.group(1) or type(revision) is not int or
            not 1<=revision<=999999999 or revision!=int(match.group(2))):
            raise ValueError("Sync file name and envelope disagree.")
        label=str(header.get("device_name") or "")[:64].strip()
        if not label:
            raise ValueError("Sync device name is empty.")
        if hashlib.sha256(raw).hexdigest()!=header.get("library_sha256"):
            raise ValueError("Sync library checksum mismatch.")
        data=json.loads(raw)
        if not isinstance(data,dict) or data.get("format")!="booktracker":
            raise ValueError("Invalid BookTracker content.")
        if data.get("schema_version") not in (9,10,11):
            raise ValueError("Peer snapshot requires BookTracker v0.13+.")
        for table in RELEVANT_TABLES:
            if data.get("schema_version")<11 and table in (
                "epub_positions","epub_bookmarks"
            ):
                data.setdefault(table,[])
            if not isinstance(data.get(table),list):
                raise ValueError(f"Invalid sync table: {table}.")
        return PeerSnapshot(file,uid,label,revision,_file_hash(file),data)
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        raise ValueError("Unreadable or damaged sync snapshot.") from exc

def available_peers(library:Library, folder):
    """Show latest valid snapshot per peer; report broken candidates."""
    root=_directory(folder)
    mine=device_info(library)["device_id"]
    latest={}
    errors=[]
    files=sorted(root.glob("booktracker-*.btsync"))
    if len(files)>10000:
        raise ValueError("Sync folder contains too many snapshots.")
    for file in files:
        match=FILE_RE.fullmatch(file.name)
        if not match or match.group(1)==mine:
            continue
        if match.group(1) in latest and int(match.group(2))<latest[match.group(1)].revision:
            continue
        try:
            peer=read_snapshot(file)
            if peer.revision>latest.get(peer.device_id,peer).revision or peer.device_id not in latest:
                latest[peer.device_id]=peer
        except ValueError as exc:
            errors.append((file.name,str(exc)))
    if len(latest)>MAX_PEERS:
        raise ValueError("Too many devices in one shared sync folder.")
    return sorted(latest.values(),key=lambda p:p.device_name.casefold()),errors

@dataclass
class Change:
    table:str
    key:tuple
    field:str
    local:object
    remote:object
    kind:str="conflict"

@dataclass
class SyncPlan:
    peer:PeerSnapshot
    local_hash:str
    previous_revision:int
    payload:dict
    safe:list=field(default_factory=list)
    conflicts:list=field(default_factory=list)
    new_rows:int=0
    skipped_deletions:int=0

def _logical(v):
    return v

def plan(library:Library,peer:PeerSnapshot):
    if peer.device_id==device_info(library)["device_id"]:
        raise ValueError("Cannot synchronize a device with itself.")
    state=library.db.execute(
        "SELECT * FROM sync_peers WHERE device_id=?",(peer.device_id,)
    ).fetchone()
    previous=state["revision"] if state else 0
    if peer.revision<=previous:
        raise ValueError("No newer peer snapshot is available.")
    current=library.export_data()
    previous_remote=_unpack(state["remote_baseline"]) if state else {}
    previous_local=_unpack(state["local_baseline"]) if state else {}
    data=copy.deepcopy(peer.data)
    result=SyncPlan(peer,_json(_baseline(current)),previous,data)
    local_removed={}
    peer_removed={}
    for table in RELEVANT_TABLES:
        c=_rows(current,table)
        r=_rows(peer.data,table)
        bl=_rows(previous_local,table)
        br=_rows(previous_remote,table)
        local_removed[table]={key for key in bl if key not in c}
        peer_removed[table]={key for key in br if key not in r}
        for key in peer_removed[table]:
            if key in c:
                result.conflicts.append(Change(table,key,"(deleted by peer)",
                                               "Exists locally","Deleted remotely","deletion"))
    # Two independent computers can create different UUIDs for the *same*
    # title and author. Never automatically collapse them: this might be
    # separate editions or unrelated works. Show the possible duplication
    # clearly before the user consents to adding a separate library entry.
    known_works=_rows(current,"works")
    for row in peer.data["works"]:
        if (str(row.get("id")),) in known_works:
            continue
        if library.find_match(row):
            continue  # ISBN/source evidence supports the existing match.
        possible=[
            existing for existing in known_works.values()
            if _book_identity(existing["title"])==_book_identity(row.get("title",""))
            and _book_identity(existing["author"])==_book_identity(row.get("author",""))
        ]
        if possible:
            result.conflicts.append(Change(
                "works",(str(row["id"]),),"(possible duplicate)",
                possible[0]["title"]+" — "+possible[0]["author"],
                row.get("title","")+" — "+row.get("author",""),
                "duplicate",
            ))
    # Prevent a remote stale snapshot from recreating a locally deleted work
    # or a locally deleted subsidiary record. Do not delete existing local data.
    exclusions={}
    for table in RELEVANT_TABLES:
        c=_rows(current,table)
        br=_rows(previous_remote,table)
        bl=_rows(previous_local,table)
        exclude=set()
        for row in data[table]:
            key=_key(table,row)
            if key in local_removed[table]:
                exclude.add(key)
                result.conflicts.append(Change(
                    table,key,"(deleted locally)","Deleted locally",
                    "Still exists on peer","deletion"
                ))
                continue
            # An item never seen locally and first seen on peer is additive.
            if key not in c and key not in bl:
                result.new_rows+=1
        exclusions[table]=exclude
    # Filter dependents when their parent was explicitly deleted locally.
    for table in RELEVANT_TABLES:
        keep=[]
        for row in data[table]:
            if _key(table,row) in exclusions[table]:
                continue
            if table!="works" and "work_id" in row and (
                str(row["work_id"]),) in exclusions["works"]:
                continue
            if table=="progress_events" and (
                str(row.get("run_id")),) in exclusions["reading_runs"]:
                continue
            if table=="source_checks" and (
                str(row.get("source_id")),) in exclusions["work_sources"]:
                continue
            if table=="serial_releases" and row.get("source_id") and (
                str(row["source_id"]),) in exclusions["work_sources"]:
                continue
            if table in ("audio_metadata","epub_positions","epub_bookmarks") and (
                str(row.get("file_id")),) in exclusions["local_files"]:
                continue
            if table=="work_shelves" and (
                str(row.get("shelf_id")),) in exclusions["shelves"]:
                continue
            keep.append(row)
        data[table]=keep
    result.skipped_deletions=sum(map(len,exclusions.values()))
    # Compare remote and local against separate baselines from the *last
    # successfully applied* peer state. Both edits => explicit conflict.
    for table,allowed in EDITABLE.items():
        local=_rows(current,table)
        remote=_rows(data,table)
        prior_local=_rows(previous_local,table)
        prior_remote=_rows(previous_remote,table)
        for key,rr in remote.items():
            lr=local.get(key)
            if lr is None:
                continue
            before_local=prior_local.get(key)
            before_remote=prior_remote.get(key)
            for column in sorted(allowed):
                if column in IGNORE or column not in rr:
                    continue
                rv=rr[column]
                lv=lr.get(column)
                if _logical(lv)==_logical(rv):
                    continue
                if before_remote is not None and _logical(rv)==_logical(before_remote.get(column)):
                    continue
                if before_local is not None and _logical(lv)==_logical(before_local.get(column)):
                    result.safe.append(Change(table,key,column,lv,rv,"safe"))
                else:
                    result.conflicts.append(Change(table,key,column,lv,rv))
    # Unmanaged mutable records (e.g. an edition/source edited on each device)
    # are additive, not silently rewritten. Show discrepancies explicitly.
    for table in ("reading_runs","work_editions","work_sources",
                  "reading_queue","local_files","audio_metadata","shelves"):
        local=_rows(current,table)
        remote=_rows(data,table)
        prior_remote=_rows(previous_remote,table)
        for key,rr in remote.items():
            ll=local.get(key)
            if ll is None:
                continue
            columns=set(rr)&set(ll)-set(KEYS[table])-IGNORE
            if any(rr[col]!=ll[col] and (
                key not in prior_remote or rr[col]!=prior_remote[key].get(col)
            ) for col in columns if col!="path"):
                result.conflicts.append(Change(
                    table,key,"(record)", "Keep local metadata",
                    "Peer has different metadata","review"
                ))
    return result

def _row_update(library, change, mapping, data):
    if change.field not in EDITABLE.get(change.table,set()):
        return
    table=change.table
    field=change.field
    key=change.key
    if table=="works":
        target=mapping.get(key[0],key[0])
        cleaned=library._clean({field:change.remote})
        value=cleaned[field]
        if field in ("kind","status","publication_status","progress_unit"):
            allowed={"kind":("book","fanfiction","web_novel","other"),
                     "status":("plan_to_read","reading","caught_up","completed","paused","dropped"),
                     "publication_status":("unknown","ongoing","completed","hiatus"),
                     "progress_unit":("chapters","pages","percent")}
            if value not in allowed[field]:
                raise ValueError("Invalid synced book field.")
        library.db.execute(
            f"UPDATE works SET {field}=?,updated_at=? WHERE id=?",
            (value,now(),target)
        )
        return
    if table=="progress_events" and field in ("new_progress","recorded_at","note"):
        if field=="new_progress":
            value=int(change.remote)
            if not 0<=value<=10_000_000:
                raise ValueError("Invalid synced progress position.")
        elif field=="recorded_at":
            value=library._reading_date(change.remote)
        else:
            value=str(change.remote)[:10000]
    elif table=="reading_sessions":
        row=next(row for row in data[table] if _key(table,row)==key)
        day,minutes,mode,note=library._session_values(
            row.get("read_date"),row.get("minutes"),row.get("mode"),row.get("note")
        )
        value={"read_date":day,"minutes":minutes,"mode":mode,"note":note}[field]
    elif table=="annotations":
        row=next(row for row in data[table] if _key(table,row)==key)
        values=library._annotation_values(
            row.get("kind"),row.get("body",""),
            row.get("location"),row.get("location_unit",""),
        )
        value=dict(zip(("kind","location","location_unit","body"),values))[field]
    elif table=="reading_goals":
        value=int(change.remote)
        if not 0<=value<=100_000_000:
            raise ValueError("Invalid synced reading goal.")
    elif table=="serial_follows":
        value=int(change.remote)
        if field=="notify" and value not in (0,1):
            raise ValueError("Invalid following preference.")
        if field=="seen_units" and not 0<=value<=10_000_000:
            raise ValueError("Invalid acknowledged chapter count.")
    elif table=="epub_positions":
        row=next(row for row in data[table] if _key(table,row)==key)
        idx,href,ratio=library._epub_location(
            row.get("section_index"),row.get("section_href"),row.get("scroll_ratio")
        )
        value={"section_index":idx,"section_href":href,"scroll_ratio":ratio}[field]
    elif table=="epub_bookmarks":
        value=library._epub_bookmark_label(change.remote)
    elif table=="saved_views":
        library.validate_view_filters(json.loads(change.remote))
        value=change.remote
    else:
        return
    where=" AND ".join(f"{col}=?" for col in KEYS[table])
    library.db.execute(
        f"UPDATE {table} SET {field}=? WHERE {where}",
        (value,*key)
    )

def apply(library:Library,review:SyncPlan, *, use_peer=()):
    """Apply one reviewed snapshot, atomically committing peer baselines.

    use_peer is a sequence of indexes into preview conflicts; review-only
    metadata/deletion warnings are deliberately never applied as destructive
    changes.
    """
    if _file_hash(review.peer.path)!=review.peer.archive_hash:
        raise ValueError("Peer snapshot changed after preview. Review again.")
    if _json(_baseline(library.export_data()))!=review.local_hash:
        raise ValueError("Library changed after preview. Refresh sync review.")
    prior=library.db.execute(
        "SELECT revision FROM sync_peers WHERE device_id=?",(review.peer.device_id,)
    ).fetchone()
    if (prior["revision"] if prior else 0)!=review.previous_revision:
        raise ValueError("Peer was already processed. Refresh review.")
    selections=set(use_peer)
    if any(type(index) is not int or not 0<=index<len(review.conflicts) for index in selections):
        raise ValueError("Invalid conflict selection.")
    if any(review.conflicts[i].kind!="conflict" for i in selections):
        raise ValueError("Deletions and unsupported edits cannot be auto-applied.")
    backup=library.path.parent/"backups"
    backup.mkdir(parents=True,exist_ok=True)
    filename="BookTracker-before-sync-"+datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")+".sqlite3"
    with sqlite3.connect(backup/filename) as dest:
        library.db.backup(dest)
    update=review.safe+[review.conflicts[i] for i in sorted(selections)]
    # Library.import_data is already atomic. postmerge runs inside its own
    # transaction, so even revision bookkeeping rolls back on failure.
    def after_import(mapping):
        touched=set()
        for change in update:
            _row_update(library,change,mapping,review.payload)
            if change.table=="progress_events":
                row=library.db.execute(
                    "SELECT run_id FROM progress_events WHERE id=?",change.key
                ).fetchone()
                if row:
                    touched.add(row["run_id"])
        # Import_data ignores old run progress, while unioning new milestone
        # events. Reconcile only runs that gained a new event.
        for row in review.payload["progress_events"]:
            run=library.db.execute(
                "SELECT 1 FROM reading_runs WHERE id=?",(row["run_id"],)
            ).fetchone()
            if run:
                touched.add(row["run_id"])
        for run_id in touched:
            if library.db.execute(
                "SELECT 1 FROM progress_events WHERE run_id=? LIMIT 1",
                (run_id,)
            ).fetchone():
                library._reconcile_run_events(run_id)
        local_after=_baseline(library.export_data())
        remote_before=_baseline(review.peer.data)
        library.db.execute(
            """INSERT INTO sync_peers
            (device_id,display_name,revision,remote_baseline,
             local_baseline,last_applied_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET
              display_name=excluded.display_name,
              revision=excluded.revision,
              remote_baseline=excluded.remote_baseline,
              local_baseline=excluded.local_baseline,
              last_applied_at=excluded.last_applied_at""",
            (review.peer.device_id,review.peer.device_name,review.peer.revision,
             _pack(remote_before),_pack(local_after),now())
        )
    imported=import_portable(
        library,review.peer.path,strategy="merge",postmerge=after_import,
        sync_mode=True
    ) if review.payload==review.peer.data else _apply_filtered(
        library,review.peer.path,review.payload,after_import
    )
    return {"new_works":imported,"safe_updates":len(review.safe),
            "chosen_conflicts":len(selections),
            "kept_local":len(review.conflicts)-len(selections),
            "backup":str(backup/filename)}

def _apply_filtered(library,source,data,callback):
    # If local deletions mean the peer manifest must be filtered, import
    # metadata without touching the peer archive; imported covers already
    # resident in user-created media remain local. Copied cover tokens are
    # valid only if their corresponding cover bytes are installed.
    # Import covers from the original archive after validating them by
    # pointing import_portable through an ephemeral filtered archive.
    with tempfile.TemporaryDirectory(prefix=".bt-sync-import-") as temp:
        archive=Path(temp)/"filtered.btbackup"
        # All non-library members come from the original, unmodified archive.
        # A filtered book catalog can exclude covers; orphaned cover payloads
        # remain harmless in the archive and are not consumed by the importer.
        with zipfile.ZipFile(source) as zin,zipfile.ZipFile(
            archive,"w",compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for entry in zin.infolist():
                if entry.filename in ("library.json","sync.json"):
                    continue
                if not entry.filename.startswith("covers/"):
                    continue
                if entry.file_size>5_000_000:
                    raise ValueError("Sync cover exceeds limit.")
                zout.writestr(entry,zin.read(entry))
            zout.writestr("library.json",_json(data))
        return import_portable(library,archive,postmerge=callback,sync_mode=True)
