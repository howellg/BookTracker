"""Portable full-library backup: versioned JSON plus local EPUB cover images."""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

from .covers import is_local_cover
from .database import Library

MAX_MANIFEST_BYTES = 80_000_000
MAX_COVER_BYTES = 5_000_000
MAX_TOTAL_COVER_BYTES = 500_000_000
MAX_COVER_COUNT = 20_000


def _cover_name(token: str) -> str:
    if not is_local_cover(token):
        raise ValueError("Invalid BookTracker local cover identifier.")
    return token.split(":", 1)[1]


def export_portable(library: Library, path: str | Path):
    """Write an atomic .btbackup ZIP of reading data and referenced covers."""
    target = Path(path)
    payload = library.export_data()
    covers = {
        item["cover_url"] for item in payload["works"]
        if is_local_cover(item.get("cover_url") or "")
    }
    if len(covers) > MAX_COVER_COUNT:
        raise ValueError("Too many locally imported cover images for one backup.")
    library_json = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(library_json) > MAX_MANIFEST_BYTES:
        raise ValueError("The reading library is too large for a single portable backup.")
    folder = library.path.parent / "imported_covers"
    total_bytes = 0
    source_files = []
    for token in sorted(covers):
        name = _cover_name(token)
        source = folder / name
        if not source.is_file():
            raise ValueError(f"An imported cover is missing: {name}")
        size = source.stat().st_size
        total_bytes += size
        if size > MAX_COVER_BYTES or total_bytes > MAX_TOTAL_COVER_BYTES:
            raise ValueError("Imported cover images exceed the backup size limit.")
        digest = hashlib.sha256()
        with source.open("rb") as f:
            for chunk in iter(lambda: f.read(131072), b""):
                digest.update(chunk)
        if digest.hexdigest() != name[:-4]:
            raise ValueError(f"An imported cover failed its integrity check: {name}")
        source_files.append((name, source))

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".pending")
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=5, allowZip64=True) as z:
            z.writestr("library.json", library_json)
            for name, source in source_files:
                z.write(source, "covers/" + name)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def import_portable(library: Library, path: str | Path, *, strategy="merge") -> int:
    """Restore a portable backup, validating every embedded cover before use."""
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.namelist()
            if len(entries) > MAX_COVER_COUNT + 1:
                raise ValueError("Portable backup contains too many entries.")
            if "library.json" not in entries:
                raise ValueError("Portable backup has no library.json.")
            manifest = z.getinfo("library.json")
            if manifest.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("Portable backup manifest is too large.")
            data = json.loads(z.read("library.json"))
            if not isinstance(data, dict) or not isinstance(data.get("works"), list):
                raise ValueError("Portable backup has invalid library data.")
            covers = {
                work.get("cover_url", "")
                for work in data["works"] if isinstance(work, dict)
                and is_local_cover(work.get("cover_url") or "")
            }
            folder = library.path.parent / "imported_covers"
            total = 0
            for token in sorted(covers):
                name = _cover_name(token)
                member = "covers/" + name
                try:
                    info = z.getinfo(member)
                except KeyError as exc:
                    raise ValueError(f"Backup is missing cover {name}.") from exc
                total += info.file_size
                if info.file_size > MAX_COVER_BYTES or total > MAX_TOTAL_COVER_BYTES:
                    raise ValueError("Portable backup cover limits exceeded.")
                raw = z.read(info)
                if hashlib.sha256(raw).hexdigest() != name[:-4]:
                    raise ValueError(f"Backup cover checksum mismatch: {name}")
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / name
                if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != name[:-4]:
                    staging = dest.with_suffix(".pending")
                    try:
                        staging.write_bytes(raw)
                        os.replace(staging, dest)
                    finally:
                        staging.unlink(missing_ok=True)
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError("Invalid or corrupted BookTracker portable backup.") from exc

    # SQLite merges and replacements stay atomic. On invalid library metadata
    # only unreferenced, validated image files may be left behind.
    return library.import_data(data, strategy=strategy)
