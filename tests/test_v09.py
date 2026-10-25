"""v0.9: source mirrors, book editions, manual update checks and v6 backups."""
from __future__ import annotations

import sqlite3

import pytest

from booktracker.database import Library
from booktracker.integrations import export_file, import_file


@pytest.fixture
def library(tmp_path):
    db=Library(tmp_path/"library.db")
    yield db
    db.close()


def story(library, **values):
    return library.add_work({
        "title":"The Wandering Inn","author":"pirateaba","kind":"web_novel",
        "status":"reading","progress_unit":"chapters",**values,
    })


def test_source_crossposts_primary_switching_and_history(library):
    work=story(library)
    library.record_progress_on(work,50,date="2026-08-01",note="Saved")
    ao3=library.add_source(work,"https://archiveofourown.org/works/123456/chapter/2")
    rr=library.add_source(work,"https://www.royalroad.com/fiction/10000/story",label="Royal Road")
    assert len(library.list_sources(work))==2
    assert library.get_source(ao3)["site"]=="AO3"
    assert library.get_source(ao3)["external_id"]=="123456"
    assert library.get_work(work)["source_site"]=="AO3"
    library.set_primary_source(rr)
    assert library.get_work(work)["source_site"]=="Royal Road"
    assert library.get_work(work)["source_id"]=="10000"
    assert library.get_work(work)["source_url"].startswith("https://www.royalroad.com/")
    assert library.active_run(work)["current_progress"]==50
    assert len(library.history(work))==1
    library.remove_source(rr)
    assert library.get_work(work)["source_site"]=="AO3"
    library.remove_source(ao3)
    assert library.get_work(work)["source_url"]==""
    assert library.get_work(work)["source_id"]==""
    assert len(library.history(work))==1


def test_source_identity_dedup_and_conflict(library):
    one=story(library)
    two=library.add_work({"title":"Other","author":"Other"})
    first=library.add_source(one,"https://archiveofourown.org/works/1234")
    second=library.add_source(one,"https://archiveofourown.org/works/1234/chapters/5678")
    assert first==second
    assert len(library.list_sources(one))==1
    with pytest.raises(ValueError,match="different work"):
        library.add_source(two,"https://archiveofourown.org/works/1234")
    assert len(library.list_sources())==1


@pytest.mark.parametrize("invalid",[
    "http://example.com/book","file:///etc/passwd","https://user:pass@site.com/path",
    "https://evil.example.com:1234/path","https://example.com:bad/path",
])
def test_invalid_source_url_rejected(library,invalid):
    key=story(library)
    with pytest.raises(ValueError,match="HTTPS"):
        library.add_source(key,invalid)
    assert library.list_sources(key)==[]


def test_unknown_provider_saved_but_not_assumed_supported(library):
    key=story(library)
    source=library.add_source(key,"https://novels.example.org/series/my-story")
    assert library.get_source(source)["site"]=="novels.example.org"
    assert library.get_source(source)["external_id"]==""
    assert library.get_work(key)["source_site"]=="novels.example.org"


def test_source_updates_never_erase_personal_history(library):
    key=story(library,total_units=50,status="caught_up")
    src=library.add_source(key,"https://archiveofourown.org/works/123123")
    library.record_progress_on(key,50,date="2026-02-01")
    assert library.record_source_check(src,observed_units=60)=="updated"
    assert library.get_work(key)["total_units"]==60
    assert library.get_work(key)["status"]=="caught_up"
    assert library.active_run(key)["current_progress"]==50
    assert library.source_update_summary()[0]["unread"]==10
    assert library.record_source_check(src,observed_units=55)=="unchanged"
    assert library.get_source(src)["published_units"]==60
    assert library.get_work(key)["total_units"]==60
    assert library.record_source_check(src,error="HTTP 403 Cloudflare")=="error"
    assert library.get_source(src)["last_error"]=="HTTP 403 Cloudflare"
    assert len(library.list_source_checks(src))==3
    assert library.record_source_check(src,observed_units=60)=="unchanged"
    assert library.get_source(src)["last_error"]==""
    assert library.active_run(key)["current_progress"]==50


def test_unknown_source_chapters_does_not_reset_known_count(library):
    key=story(library,total_units=90)
    src=library.add_source(key,"https://www.royalroad.com/fiction/9999/example")
    assert library.record_source_check(src,observed_units=0)=="unknown"
    assert library.get_work(key)["total_units"]==90
    assert library.get_source(src)["published_units"]==0


def test_editions_preserve_progress_and_can_choose_preferred(library):
    work=library.add_work({
        "title":"The Way of Kings","author":"Brandon Sanderson",
        "kind":"book","status":"reading","progress_unit":"pages",
    })
    lib=library
    lib.record_progress_on(work,100,date="2025-05-01")
    ebook=lib.add_edition(work,{
        "format":"ebook","isbn":"9780765326355","title":"Ebook","pages":1007,
        "publisher":"Tor","published_at":"2010-08-31","language":"eng"
    })
    audio=lib.add_edition(work,{
        "format":"audiobook","title":"Unabridged Audio","notes":"Michael Kramer",
        "publisher":"Macmillan Audio"
    })
    assert len(lib.list_editions(work))==2
    assert lib.get_work(work)["isbn"]=="9780765326355"
    assert lib.get_work(work)["total_units"]==1007
    lib.set_preferred_edition(audio)
    assert lib.get_work(work)["isbn"]==""
    assert lib.active_run(work)["current_progress"]==100
    assert len(lib.history(work))==1
    lib.update_edition(audio,{"format":"audiobook","title":"New Audio Edition",
                              "publisher":"Macmillan","notes":"Full cast"})
    assert lib.get_edition(audio)["title"]=="New Audio Edition"
    lib.remove_edition(audio)
    assert lib.get_edition(ebook)["is_preferred"]==1
    assert lib.get_work(work)["isbn"]=="9780765326355"


def test_duplicate_edition_isbn_rejected(library):
    work=story(library)
    library.add_edition(work,{"format":"print","isbn":"9781234567890"})
    with pytest.raises(ValueError):
        library.add_edition(work,{"format":"ebook","isbn":"9781234567890"})


@pytest.mark.parametrize("suffix",[".json",".xml",".btbackup"])
def test_v6_backup_roundtrip_and_nonoverwriting_merge(library,tmp_path,suffix):
    work=story(library,total_units=80)
    a=library.add_source(work,"https://archiveofourown.org/works/56789")
    b=library.add_source(work,"https://www.royalroad.com/fiction/4321/x")
    library.set_primary_source(b)
    edition=library.add_edition(work,{"format":"ebook","title":"EPUB copy","notes":"Personal note"})
    library.record_source_check(a,observed_units=100)
    library.record_source_check(b,error="Blocked by Cloudflare")
    library.record_progress_on(work,50,date="2025-12-01")
    path=tmp_path/("backup"+suffix)
    export_file(library,path)
    other=Library(tmp_path/("restored"+suffix+".db"))
    try:
        assert import_file(other,path)==1
        assert len(other.list_sources(work))==2
        assert len(other.list_editions(work))==1
        assert len(other.list_source_checks())==2
        assert other.get_edition(edition)["notes"]=="Personal note"
        assert other.get_work(work)["source_site"]=="Royal Road"
        assert import_file(other,path)==0
        assert len(other.list_source_checks())==2
        other.update_edition(edition,{"format":"ebook","title":"Local change",
                                     "notes":"Keep mine"})
        assert import_file(other,path)==0
        assert other.get_edition(edition)["notes"]=="Keep mine"
        assert import_file(other,path,strategy="replace")==1
        assert other.get_edition(edition)["notes"]=="Personal note"
        assert len(other.history(work))==1
    finally:
        other.close()


def test_legacy_v5_backup_populates_primary_source(library,tmp_path):
    original=story(library,source_site="Royal Road",source_id="333",
                   source_url="https://www.royalroad.com/fiction/333/fantasy")
    old=library.export_data()
    old["schema_version"]=5
    for field in ("work_sources","work_editions","source_checks"):
        old.pop(field)
    other=Library(tmp_path/"legacy.db")
    try:
        assert other.import_data(old)==1
        assert len(other.list_sources(original))==1
        assert other.get_work(original)["source_id"]=="333"
    finally:
        other.close()


def test_v5_db_migrates_legacy_isbn_and_site_safely(tmp_path):
    path=tmp_path/"old.db"
    original=Library(path)
    work=original.add_work({
        "title":"An Old Novel","author":"A",
        "isbn":"9781234567890", "total_units":600,
        "progress_unit":"pages", "source_site":"Open Library",
        "source_id":"/works/OL123W",
        "source_url":"https://openlibrary.org/works/OL123W",
    })
    original.close()
    with sqlite3.connect(path) as con:
        con.execute("DROP TABLE source_checks")
        con.execute("DROP TABLE work_sources")
        con.execute("DROP TABLE work_editions")
        con.execute("PRAGMA user_version=5")
    reloaded=Library(path)
    try:
        assert reloaded.db.execute("PRAGMA user_version").fetchone()[0]==7
        assert len(reloaded.list_sources(work))==1
        assert len(reloaded.list_editions(work))==1
        assert reloaded.list_editions(work)[0]["pages"]==600
    finally:
        reloaded.close()
    again=Library(path)
    try:
        assert len(again.list_sources(work))==1
        assert len(again.list_editions(work))==1
    finally:
        again.close()


def test_invalid_v6_restore_rolls_back_existing(library):
    work=story(library)
    backup=library.export_data()
    backup["work_sources"]=[{
        "id":"broken","work_id":"unknown","url":"https://example.org",
        "site":"","external_id":"","label":"","published_units":0
    }]
    with pytest.raises(ValueError):
        library.import_data(backup,strategy="replace")
    assert library.get_work(work) is not None
    assert library.list_sources(work)==[]


def test_new_work_with_source_immediately_creates_primary_link(library):
    key=story(
        library,source_site="AO3",source_id="555",
        source_url="https://archiveofourown.org/works/555"
    )
    links=library.list_sources(key)
    assert len(links)==1
    assert links[0]["is_primary"]==1
    assert links[0]["site"]=="AO3"
    assert links[0]["external_id"]=="555"


def test_editing_main_source_url_reconciles_links(library):
    key=story(library,source_url="https://archiveofourown.org/works/111")
    initial=library.list_sources(key)[0]["id"]
    library.update_work(key,{
        "source_url":"https://www.royalroad.com/fiction/8000/story",
        "source_site":"Royal Road", "source_id":"8000",
    })
    links=library.list_sources(key)
    assert len(links)==2
    assert next(l for l in links if l["is_primary"])["site"]=="Royal Road"
    library.update_work(key,{"source_url":""})
    assert not any(l["is_primary"] for l in library.list_sources(key))
    assert library.get_work(key)["source_site"]==""
    assert library.get_source(initial) is not None


def test_source_url_collision_rolls_back_main_work_edit(library):
    a=story(library,source_url="https://archiveofourown.org/works/99")
    b=library.add_work({"title":"Other Work","author":"Someone"})
    with pytest.raises(ValueError):
        library.update_work(b,{
            "source_url":"https://archiveofourown.org/works/99",
            "source_site":"AO3","source_id":"99",
        })
    assert library.get_work(b)["source_url"]==""
    assert library.get_work(a)["source_site"]=="AO3"


def test_migration_backup_created_before_updating_schema(tmp_path):
    path=tmp_path/"library.sqlite3"
    legacy=Library(path)
    key=legacy.add_work({"title":"Legacy Test","notes":"Needs preservation"})
    legacy.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE source_checks")
        connection.execute("DROP TABLE work_sources")
        connection.execute("DROP TABLE work_editions")
        connection.execute("PRAGMA user_version=5")
    db=Library(path)
    try:
        files=list((tmp_path/"backups").glob("BookTracker-before-v010-*.sqlite3"))
        assert files
        with sqlite3.connect(files[-1]) as raw:
            assert raw.execute("PRAGMA user_version").fetchone()[0]==5
            assert raw.execute(
                "SELECT notes FROM works WHERE id=?",(key,)
            ).fetchone()[0]=="Needs preservation"
        assert db.get_work(key)["notes"]=="Needs preservation"
    finally:
        db.close()


def test_catalog_dialog_source_and_edition_workflows(tmp_path,monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication

    from booktracker.gui import CatalogManagerDialog, EditionEditor, MainWindow

    app=QApplication.instance() or QApplication([])
    assert app is not None
    db=Library(tmp_path/"gui.db")
    key=db.add_work({
        "title":"A Flower for the Soul","kind":"fanfiction",
        "author":"TheBlack'sResurgence","progress_unit":"chapters",
        "status":"reading",
    })
    db.record_progress_on(key,22,date="2026-06-01")
    screen=MainWindow(db)
    try:
        assert len(screen.nav_buttons)==12
        manager=CatalogManagerDialog(screen,db,key)
        try:
            assert manager.sources.rowCount()==0
            monkeypatch.setattr(
                "booktracker.gui.QInputDialog.getText",
                lambda *args,**kwargs:("https://archiveofourown.org/works/99999",True)
            )
            manager.add_source()
            assert manager.sources.rowCount()==1
            source_id=manager.selected_source_id()
            assert db.get_source(source_id)["is_primary"]==1
            manager.check_success(source_id,{"total_units":31})
            assert db.get_work(key)["total_units"]==31
            assert manager.sources.item(0,2).text()=="31"
            assert db.active_run(key)["current_progress"]==22
            manager.check_failed(source_id,"Cloudflare HTTP 403")
            assert db.get_source(source_id)["last_error"].startswith("Cloudflare")
            ed=db.add_edition(key,{"format":"ebook","title":"Saved EPUB"})
            manager.refresh()
            assert manager.editions.rowCount()==1
            assert manager.selected_edition_id()==ed
            assert EditionEditor(screen, db.get_edition(ed)).values()["format"]=="ebook"
            screen.navigate(9)
            assert screen.update_table.rowCount()==1
            assert screen.update_table.item(0,1).text()=="9"
            assert screen.update_counts.text().startswith("1 tracked")
        finally:
            manager.close()
    finally:
        screen.close()


def test_bulk_import_crossposted_story_attaches_secondary_mirror(library):
    work=story(
        library,
        source_site="AO3",source_id="30001",
        source_url="https://archiveofourown.org/works/30001"
    )
    library.record_progress_on(work,20,date="2025-08-01")
    result=library.bulk_add_works([{
        "title":"The Wandering Inn","author":"pirateaba",
        "kind":"web_novel","source_site":"Royal Road",
        "source_id":"50000",
        "source_url":"https://www.royalroad.com/fiction/50000/the-wandering-inn",
    }],status="completed")
    assert result["added"]==0
    assert len(library.list_sources(work))==2
    assert library.get_work(work)["source_site"]=="AO3"
    assert library.get_work(work)["status"]=="reading"
    assert library.active_run(work)["current_progress"]==20
    assert library.find_match({
        "source_site":"Royal Road","source_id":"50000"
    })==work


def test_bulk_import_extra_isbn_creates_nonpreferred_edition(library):
    key=library.add_work({
        "title":"The Way of Kings","author":"Brandon Sanderson",
        "isbn":"9781000000001","progress_unit":"pages"
    })
    response=library.bulk_add_works([{
        "title":"The Way of Kings","author":"Brandon Sanderson",
        "isbn":"9781000000002","progress_unit":"pages",
        "total_units":1000
    }],status="plan_to_read")
    assert response["added"]==0
    editions=library.list_editions(key)
    assert {e["isbn"] for e in editions}=={"9781000000001","9781000000002"}
    assert sum(e["is_preferred"] for e in editions)==1
    assert library.get_work(key)["isbn"]=="9781000000001"
    assert library.find_match({"isbn":"9781000000002"})==key


def test_bulk_import_new_works_have_linked_sources_immediately(library):
    report=library.bulk_add_works([{
        "title":"Brand new fic","author":"Author",
        "kind":"fanfiction","source_site":"AO3","source_id":"8811",
        "source_url":"https://archiveofourown.org/works/8811",
    }],status="plan_to_read")
    assert report["added"]==1
    work=report["added_ids"][0]
    assert library.list_sources(work)[0]["site"]=="AO3"


def test_dialog_manual_chapter_update_records_provenance(tmp_path,monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication

    from booktracker.gui import CatalogManagerDialog

    application=QApplication.instance() or QApplication([])
    assert application is not None
    db=Library(tmp_path/"manual.db")
    try:
        key=story(db,total_units=15)
        source=db.add_source(key,"https://archiveofourown.org/works/200099")
        dialog=CatalogManagerDialog(None,db,key)
        try:
            monkeypatch.setattr(
                "booktracker.gui.QInputDialog.getInt",
                lambda *args,**kwargs:(25,True)
            )
            dialog.set_manual_chapters()
            assert db.get_work(key)["total_units"]==25
            assert db.get_source(source)["published_units"]==25
            audit=db.list_source_checks(source)
            assert audit[0]["observed_units"]==25
            assert "Manually recorded" in audit[0]["message"]
        finally:
            dialog.close()
    finally:
        db.close()


def test_open_library_links_retain_work_identity(library):
    work=library.add_work({"title":"A Book","author":"Writer"})
    source=library.add_source(work,"https://openlibrary.org/works/OL123456W")
    assert library.get_source(source)["site"]=="Open Library"
    assert library.get_source(source)["external_id"]=="/works/OL123456W"
    assert library.find_match({
        "source_site":"Open Library","source_id":"/works/OL123456W"
    })==work


def test_saved_ao3_html_updates_chapter_count_without_network(tmp_path,monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication

    from booktracker.gui import CatalogManagerDialog

    app=QApplication.instance() or QApplication([])
    assert app is not None
    db=Library(tmp_path/"saved-html.db")
    try:
        key=db.add_work({
            "title":"Story","author":"Writer","kind":"fanfiction",
            "progress_unit":"chapters","status":"reading"
        })
        db.record_progress_on(key,10,date="2026-01-01")
        link=db.add_source(key,"https://archiveofourown.org/works/123456")
        saved=tmp_path/"story.html"
        saved.write_text(
            '<html><h2 class="title heading">Story</h2>'
            '<h3 class="byline"><a rel="author">Writer</a></h3>'
            '<dl class="stats"><dt class="chapters">Chapters:</dt>'
            '<dd class="chapters">24/30</dd>'
            '<dt class="words">Words:</dt><dd class="words">100,000</dd></dl>'
            '</html>',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "booktracker.gui.QFileDialog.getOpenFileName",
            lambda *args,**kwargs:(str(saved),"HTML")
        )
        dialog=CatalogManagerDialog(None,db,key)
        try:
            dialog.import_saved_html()
            assert db.get_work(key)["total_units"]==24
            assert db.active_run(key)["current_progress"]==10
            assert db.get_source(link)["published_units"]==24
            assert "locally" in db.list_source_checks(link)[0]["message"]
        finally:
            dialog.close()
    finally:
        db.close()
