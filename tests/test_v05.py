"""Regression coverage for v0.5: reading goals, EPUB, series, backups, CSV."""
from __future__ import annotations

import csv
import json
import os
import zipfile

import pytest

from booktracker.database import Library
from booktracker.epub_import import read_epub_metadata
from booktracker.integrations import export_file, import_file, import_goodreads

CONTAINER = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf"
media-type="application/oebps-package+xml"/></rootfiles></container>"""
OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>The Final Empire</dc:title>
<dc:creator>Brandon Sanderson</dc:creator>
<dc:language>en</dc:language>
<dc:identifier>urn:isbn:9780765311788</dc:identifier>
<dc:date>2006-07-25</dc:date>
<dc:subject>Epic Fantasy</dc:subject>
<dc:subject>Magic, Allomancy</dc:subject>
<dc:description>&lt;p&gt;A &lt;b&gt;fantasy&lt;/b&gt; adventure.&lt;/p&gt;</dc:description>
<meta name="calibre:series" content="Mistborn"/>
<meta name="cover" content="cover-image"/>
</metadata>
<manifest><item id="cover-image" href="images/cover.png" media-type="image/png"/></manifest>
</package>"""


def make_epub(filename, *, package=OPF, cover=b"", container=CONTAINER):
    with zipfile.ZipFile(filename, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        if cover:
            archive.writestr("OEBPS/images/cover.png", cover)


@pytest.fixture
def library(tmp_path):
    db = Library(tmp_path / "test.sqlite3")
    yield db
    db.close()


def test_epub_reads_metadata_offline(tmp_path):
    epub = tmp_path / "book.epub"
    make_epub(epub)
    book = read_epub_metadata(epub)
    assert book["title"] == "The Final Empire"
    assert book["author"] == "Brandon Sanderson"
    assert book["isbn"] == "9780765311788"
    assert book["series"] == "Mistborn"
    assert book["description"] == "A fantasy adventure."
    assert book["tags"] == ["Epic Fantasy", "Magic, Allomancy"]
    assert book["progress_unit"] == "pages"
    assert book["published_at"] == "2006-07-25"


def test_epub_cover_is_relocatable_and_renders(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage

    from booktracker.covers import CoverCache, cover_url_for, is_local_cover
    image = QImage(80, 110, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc8844"))
    fixture = tmp_path / "test-cover.png"
    assert image.save(str(fixture))
    epub = tmp_path / "with-cover.epub"
    make_epub(epub, cover=fixture.read_bytes())
    book = read_epub_metadata(epub, cover_dir=tmp_path / "imported_covers")
    token = book["cover_url"]
    assert is_local_cover(token)
    assert cover_url_for(book) == token
    cover = CoverCache(local_covers=tmp_path / "imported_covers", folder=tmp_path / "cache")
    assert cover.get(token) is not None
    assert not cover.get(token).isNull()
    assert read_epub_metadata(epub, cover_dir=tmp_path / "imported_covers")["cover_url"] == token
    assert len(list((tmp_path / "imported_covers").glob("*.png"))) == 1


def test_epub_detects_fanfiction_source(tmp_path):
    package = OPF.replace(
        "<dc:date>2006-07-25</dc:date>",
        "<dc:date>2006-07-25</dc:date>"
        "<dc:source>https://archiveofourown.org/works/123456</dc:source>",
    )
    epub = tmp_path / "fanfic.epub"
    make_epub(epub, package=package)
    book = read_epub_metadata(epub)
    assert book["kind"] == "fanfiction"
    assert book["source_id"] == "123456"
    assert book["source_site"] == "AO3"
    assert book["progress_unit"] == "chapters"


def test_bad_epubs_fail_without_extracting(tmp_path):
    missing = tmp_path / "missing.epub"
    missing.write_text("not a zip")
    with pytest.raises(ValueError, match="invalid or unreadable"):
        read_epub_metadata(missing)
    no_container = tmp_path / "no-container.epub"
    with zipfile.ZipFile(no_container, "w") as z:
        z.writestr("META-INF/other.xml", "stuff")
    with pytest.raises(ValueError):
        read_epub_metadata(no_container)
    unsafe = tmp_path / "unsafe.epub"
    make_epub(
        unsafe,
        container=CONTAINER.replace("OEBPS/content.opf", "../secrets.opf")
    )
    with pytest.raises(ValueError, match="unsafe internal path"):
        read_epub_metadata(unsafe)


def test_goal_progress_uses_actual_dates(library):
    library.add_work({"title": "Unknown historical", "status": "completed"})
    library.add_work({
        "title": "Known read", "status": "completed", "finished_at": "2025-08-01"
    })
    fic = library.add_work({"title": "Web Serial", "kind": "web_novel",
                            "progress_unit": "chapters"})
    library.set_progress(fic, 70, date="2025-07-10")
    library.set_progress(fic, 90, date="2025-07-11")
    book = library.add_work({"title": "Print", "progress_unit": "pages"})
    library.set_progress(book, 145, date="2025-07-12")
    library.save_goal(2025, books=12, chapters=200, pages=2000)
    assert library.goal_progress(2025) == {
        "year": 2025, "books": 1, "chapters": 90, "pages": 145
    }
    assert library.goal_progress(2026) == {
        "year": 2026, "books": 0, "chapters": 0, "pages": 0
    }
    assert library.get_goal(2025)["books_target"] == 12


def test_goal_preserves_previous_year_when_starting_reread(library):
    work_id = library.add_work({
        "title": "Repeat", "status": "completed", "finished_at": "2025-02-01"
    })
    library.start_reading(work_id, reread=True, date="2026-04-01")
    assert library.goal_progress(2025)["books"] == 1
    assert library.goal_progress(2026)["books"] == 0
    library.set_status(work_id, "completed", date="2026-05-01")
    assert library.goal_progress(2026)["books"] == 1


def test_goals_json_xml_roundtrip_and_merge_protection(library, tmp_path):
    library.save_goal(2026, books=40, chapters=1500, pages=6000)
    library.add_work({"title": "The Way of Kings", "series": "Stormlight"})
    for suffix in (".json", ".xml"):
        path = tmp_path / ("export" + suffix)
        export_file(library, path)
        dest = Library(tmp_path / ("other" + suffix + ".sqlite3"))
        try:
            assert import_file(dest, path) == 1
            assert dest.get_goal(2026)["books_target"] == 40
            dest.save_goal(2026, books=90)
            assert import_file(dest, path) == 0
            assert dest.get_goal(2026)["books_target"] == 90
            assert import_file(dest, path, strategy="replace") == 1
            assert dest.get_goal(2026)["books_target"] == 40
        finally:
            dest.close()


def test_old_version_2_json_import_still_works(library, tmp_path):
    library.add_work({"title": "Elantris"})
    payload = library.export_data()
    payload["schema_version"] = 2
    payload.pop("reading_goals")
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(payload))
    dest = Library(tmp_path / "dest.db")
    try:
        assert import_file(dest, path) == 1
        assert len(dest.list_works()) == 1
    finally:
        dest.close()


def test_series_and_tags_are_exact_filters(library):
    library.add_work({"title": "The Way of Kings", "series": "Stormlight Archive",
                      "tags": ["Epic, Fantasy", "magic"]})
    library.add_work({"title": "Words of Radiance", "series": "Stormlight Archive",
                      "tags": ["Fantasy"]})
    library.add_work({"title": "Elantris", "series": "Standalone", "tags": ["Fantasy"]})
    assert len(library.list_works(series="STORMLIGHT ARCHIVE")) == 2
    assert [w["title"] for w in library.list_works(tag="Epic, Fantasy")] == ["The Way of Kings"]
    assert len(library.list_works(tag="fantasy")) == 2
    assert library.series_summary()[1]["total"] == 2
    assert library.list_works(search="%") == []
    assert library.list_works(search="_") == []


def test_csv_tags_with_commas_survive_roundtrip(library, tmp_path):
    library.add_work({"title": "My Story", "author": "Me",
                      "tags": ["No beta, we die like heroes", "Time Travel"]})
    path = tmp_path / "catalog.csv"
    export_file(library, path)
    restored = Library(tmp_path / "csv.db")
    try:
        assert import_file(restored, path) == 1
        assert restored.list_works()[0]["tags"] == [
            "No beta, we die like heroes", "Time Travel"
        ]
    finally:
        restored.close()


def test_goodreads_date_added_not_reading_started(library, tmp_path):
    path = tmp_path / "goodreads.csv"
    with path.open("w", encoding="utf-8", newline="") as out:
        columns = ["Title", "Author", "Book Id", "Exclusive Shelf",
                   "Date Added", "Date Read", "Number of Pages"]
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        writer.writerow({"Title": "Warbreaker", "Author": "Brandon Sanderson",
                         "Book Id": "12", "Exclusive Shelf": "read",
                         "Date Added": "2021/01/14", "Date Read": "",
                         "Number of Pages": "600"})
        writer.writerow({"Title": "Elantris", "Author": "Brandon Sanderson",
                         "Book Id": "13", "Exclusive Shelf": "read",
                         "Date Added": "2022/01/15", "Date Read": "2023/06/11",
                         "Number of Pages": "700"})
    assert import_goodreads(library, path) == (2, 0)
    books = {w["title"]: w for w in library.list_works()}
    assert books["Warbreaker"]["started_at"] == ""
    assert books["Warbreaker"]["finished_at"] == ""
    assert library.runs(books["Warbreaker"]["id"]) == []
    assert books["Elantris"]["started_at"] == ""
    assert books["Elantris"]["finished_at"] == "2023-06-11"


def test_portable_bundle_restores_local_covers_and_goals(tmp_path):
    import hashlib

    from PySide6.QtGui import QColor, QImage

    from booktracker.covers import CoverCache

    source = Library(tmp_path / "original" / "library.db")
    local_covers = source.path.parent / "imported_covers"
    image = QImage(60, 90, QImage.Format.Format_RGB32)
    image.fill(QColor("darkcyan"))
    cover_file = tmp_path / "image.png"
    assert image.save(str(cover_file))
    epub_file = tmp_path / "volume.epub"
    make_epub(epub_file, cover=cover_file.read_bytes())
    book_data = read_epub_metadata(epub_file, cover_dir=local_covers)
    assert "cover_url" in book_data
    work_id = source.add_work(book_data)
    shelf = source.create_shelf("Mistborn")
    source.set_shelf_membership([work_id], shelf)
    source.save_goal(2026, books=40)
    bundle = tmp_path / "backup.btbackup"
    try:
        export_file(source, bundle)
        with zipfile.ZipFile(bundle) as z:
            assert "library.json" in z.namelist()
            cover_member = "covers/" + book_data["cover_url"].split(":")[1]
            assert cover_member in z.namelist()
            saved = z.read(cover_member)
            assert hashlib.sha256(saved).hexdigest() == cover_member.split("/")[-1][:-4]

        dest = Library(tmp_path / "destination" / "library.db")
        try:
            assert import_file(dest, bundle) == 1
            assert dest.get_goal(2026)["books_target"] == 40
            assert len(dest.list_shelves()) == 1
            restored = dest.list_works()[0]
            assert restored["cover_url"] == book_data["cover_url"]
            covers = CoverCache(local_covers=dest.path.parent / "imported_covers")
            assert covers.get(restored["cover_url"]) is not None
            assert import_file(dest, bundle) == 0
        finally:
            dest.close()
    finally:
        source.close()


def test_corrupt_portable_cover_is_rejected_without_changing_db(tmp_path):
    source = Library(tmp_path / "library.db")
    try:
        token = "booktracker-cover:" + "a" * 64 + ".png"
        source.add_work({"title": "Book", "cover_url": token})
        work = source.export_data()
        output = tmp_path / "corrupt.btbackup"
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("library.json", json.dumps(work))
            z.writestr("covers/" + "a" * 64 + ".png", b"not-a-valid-image")
        restored = Library(tmp_path / "dest.db")
        try:
            restored.add_work({"title": "Existing"})
            with pytest.raises(ValueError, match="checksum mismatch"):
                import_file(restored, output, strategy="replace")
            assert [w["title"] for w in restored.list_works()] == ["Existing"]
        finally:
            restored.close()
    finally:
        source.close()


def test_missing_cover_prevents_incomplete_portable_export(tmp_path):
    library = Library(tmp_path / "test.db")
    try:
        library.add_work({
            "title": "Broken cover",
            "cover_url": "booktracker-cover:" + "f" * 64 + ".png",
        })
        with pytest.raises(ValueError, match="missing"):
            export_file(library, tmp_path / "backup.btbackup")
        assert not (tmp_path / "backup.btbackup").exists()
    finally:
        library.close()


def test_portable_rejects_malicious_oversized_manifest(tmp_path, monkeypatch):
    import booktracker.portable_backup as portable
    monkeypatch.setattr(portable, "MAX_MANIFEST_BYTES", 100)
    library = Library(tmp_path / "db.sqlite3")
    try:
        target = tmp_path / "too-large.btbackup"
        with zipfile.ZipFile(target, "w") as z:
            # Sparse-ish compressed data, but the manifest's uncompressed
            # file size exceeds the documented limit.
            z.writestr("library.json", b"x" * 101)
        with pytest.raises(ValueError, match="manifest is too large"):
            import_file(library, target)
    finally:
        library.close()


def test_existing_epub_import_enriches_without_changing_personal_fields(tmp_path):
    library = Library(tmp_path / "lib.sqlite3")
    try:
        original = library.add_work({
            "title": "The Final Empire", "author": "Brandon Sanderson",
            "status": "completed", "notes": "Read this long ago",
            "rating": 5, "favorite": True,
            "publication_status": "unknown",
        })
        extra = {
            "title": "The Final Empire", "author": "Brandon Sanderson",
            "description": "An epic fantasy book.",
            "series": "Mistborn", "isbn": "9780765311788",
            "cover_url": "booktracker-cover:" + "a" * 64 + ".png",
            "publication_status": "completed",
            "tags": ["High Fantasy"],
            "language": "eng",
        }
        report = library.bulk_add_works(
            [extra], status="plan_to_read", enrich_duplicates=True
        )
        assert report["added"] == 0
        assert report["enriched"] == 1
        assert report["skipped"] == 0
        book = library.get_work(original)
        assert book["description"] == "An epic fantasy book."
        assert book["cover_url"].startswith("booktracker-cover:")
        assert book["series"] == "Mistborn"
        assert book["tags"] == ["High Fantasy"]
        assert book["status"] == "completed"
        assert book["notes"] == "Read this long ago"
        assert book["favorite"] == 1
        assert book["rating"] == 5
        assert book["finished_at"] == ""
        assert library.runs(original) == []
        again = library.bulk_add_works(
            [extra], status="plan_to_read", enrich_duplicates=True
        )
        assert again["enriched"] == 0
        assert again["skipped"] == 1
    finally:
        library.close()


def test_gui_dashboard_series_filters_and_goal_saving(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from booktracker.gui import MainWindow, WorkEditor

    app = QApplication.instance() or QApplication([])
    assert app is not None
    db = Library(tmp_path / "gui.sqlite3")
    work = db.add_work({
        "title": "Words of Radiance", "author": "Brandon Sanderson",
        "series": "Stormlight", "tags": ["Epic Fantasy"], "status": "reading"
    })
    window = MainWindow(db)
    try:
        assert len(window.nav_buttons) == 4
        assert window.series_filter.count() == 2
        assert window.tag_filter.count() == 2
        window.navigate(3)
        assert window.series_table.rowCount() == 1
        window.open_series_from_table(window.series_table.item(0, 0))
        assert window.series_filter.currentData() == "Stormlight"
        assert window.table.rowCount() == 1
        window.navigate(2)
        window.goal_year.setValue(2026)
        window.goal_inputs["books"].setValue(24)
        window.goal_inputs["pages"].setValue(20000)
        window.save_reading_goals()
        assert db.get_goal(2026)["books_target"] == 24
        assert db.get_goal(2026)["pages_target"] == 20000
        assert window.current_reads.count() >= 1
        window.open_current_read(window.current_reads.item(0))
        assert window.selected_work()["id"] == work
        editor = WorkEditor(window)
        try:
            assert editor.unit.currentData() == "pages"
            editor.fill_metadata({"title": "Mistborn", "series": "Mistborn",
                                  "author": "Brandon Sanderson"})
            assert editor.series.text() == "Mistborn"
        finally:
            editor.close()
    finally:
        window.close()
