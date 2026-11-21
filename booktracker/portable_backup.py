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
            z.writestr("integrity.json", json.dumps({
                "format": "BookTracker-full", "version": 1,
                "library_sha256": hashlib.sha256(library_json).hexdigest(),
            }))
            for name, source in source_files:
                z.write(source, "covers/" + name)
        # Verify the fully written archive *before* replacing the last backup.
        _verify_archive(staging)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def _verify_archive(path):
    """Validate a full .btbackup without modifying user files or databases."""
    with zipfile.ZipFile(path) as z:
        entries = z.namelist()
        if len(entries) > MAX_COVER_COUNT + 4:
            raise ValueError("Portable backup has too many archive members.")
        if len(set(entries)) != len(entries):
            raise ValueError("Portable backup contains duplicate member names.")
        if "library.json" not in entries:
            raise ValueError("Portable backup has no library.json.")
        unexpected = [
            name for name in entries
            if name not in ("library.json", "integrity.json", "sync.json")
            and not (name.startswith("covers/")
                     and len(name) == len("covers/") + 68
                     and name.endswith(".png")
                     and all(c in "0123456789abcdef" for c in name[7:-4]))
        ]
        if unexpected:
            raise ValueError("Portable backup contains unexpected archive members.")
        manifest = z.getinfo("library.json")
        if manifest.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("Portable backup manifest is too large.")
        body = z.read(manifest)
        if "sync.json" in entries:
            sync_info = z.getinfo("sync.json")
            if sync_info.file_size > 4096:
                raise ValueError("Sync envelope is too large.")
            header = json.loads(z.read(sync_info))
            if (not isinstance(header, dict)
                    or header.get("protocol") != "booktracker-sync"
                    or header.get("version") != 1
                    or header.get("library_sha256") != hashlib.sha256(body).hexdigest()):
                raise ValueError("Sync envelope checksum mismatch.")
        if "integrity.json" in entries:
            integrity_info = z.getinfo("integrity.json")
            if integrity_info.file_size > 2048:
                raise ValueError("Backup integrity manifest is too large.")
            check = json.loads(z.read(integrity_info))
            if (not isinstance(check, dict)
                    or check.get("format") != "BookTracker-full"
                    or check.get("version") != 1
                    or check.get("library_sha256") != hashlib.sha256(body).hexdigest()):
                raise ValueError("Portable backup library checksum mismatch.")
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("works"), list):
            raise ValueError("Portable backup has invalid library data.")
        covers = {
            work.get("cover_url", "")
            for work in data["works"] if isinstance(work, dict)
            and is_local_cover(work.get("cover_url") or "")
        }
        if len(covers) > MAX_COVER_COUNT:
            raise ValueError("Portable backup has too many covers.")
        total = 0
        for token in covers:
            name = _cover_name(token)
            try:
                info = z.getinfo("covers/" + name)
            except KeyError as exc:
                raise ValueError(f"Backup is missing cover {name}.") from exc
            total += info.file_size
            if info.file_size > MAX_COVER_BYTES or total > MAX_TOTAL_COVER_BYTES:
                raise ValueError("Portable backup cover limits exceeded.")
            if hashlib.sha256(z.read(info)).hexdigest() != name[:-4]:
                raise ValueError(f"Backup cover checksum mismatch: {name}")
        return {
            "kind": "portable", "path": str(path), "ok": True,
            "works": len(data["works"]), "covers": len(covers),
            "schema_version": data.get("schema_version"),
            "manifest_checksum": "verified" if "integrity.json" in entries
            else "legacy / not present",
            "size_bytes": Path(path).stat().st_size,
        }


def verify_portable(path):
    """Public read-only verification, safe to run against an imported backup."""
    try:
        return _verify_archive(path)
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile,
            zipfile.LargeZipFile, RuntimeError, UnicodeError) as exc:
        return {"kind": "portable", "path": str(path), "ok": False,
                "error": str(exc)[:500]}


def import_portable(library: Library, path: str | Path, *, strategy="merge", postmerge=None, sync_mode=False) -> int:
    """Restore a portable backup, validating every embedded cover before use."""
    checked = verify_portable(path)
    if not checked["ok"]:
        raise ValueError(checked.get("error", "Invalid portable backup."))
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.namelist()
            if len(entries) > MAX_COVER_COUNT + 2:
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
    return library.import_data(data, strategy=strategy, postmerge=postmerge, sync_mode=sync_mode)
