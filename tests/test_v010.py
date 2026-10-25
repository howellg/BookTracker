"""BookTracker v0.10: reading plans, safe local links, and schema v7 portability."""
from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from booktracker.database import Library
from booktracker.integrations import export_file, import_file

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")


@pytest.fixture
def library(tmp_path):
    lib=Library(tmp_path/"local.sqlite3")
    yield lib
    lib.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    current=QApplication.instance() or QApplication([])
    assert current is not None
    return current


def add_work(library,title="A Flower for the Soul",**changes):
    return library.add_work({
        "title":title, "author":"Fiction Writer", "kind":"fanfiction",
        "progress_unit":"chapters",**changes
    })


def test_queue_order_priority_and_idempotence(library):
    one=add_work(library,"First")
    two=add_work(library,"Second")
    three=add_work(library,"Third")
    first=library.enqueue_work(one,priority="next",note="Read now")
    library.enqueue_work(two,priority="low",target_date="2026-11-01")
    library.enqueue_work(three)
    assert library.enqueue_work(one)==first
    assert [r["work_id"] for r in library.list_queue()]==[one,two,three]
    assert library.get_queue_entry(one)["priority"]=="next"
    assert library.queue_summary(today="2026-11-02")=={
        "total":3,"overdue":1,"due_today":0,"next":1,"in_progress":0
    }
    assert library.queue_summary(today="2026-11-01")["due_today"]==1
    assert library.move_queue_entry(three,-1)
    assert [r["work_id"] for r in library.list_queue()]==[one,three,two]
    assert library.move_queue_entry(three,-1)
    assert [r["work_id"] for r in library.list_queue()]==[three,one,two]
    assert library.move_queue_entry(three,-1) is False
    library.update_queue_entry(three,priority="high",target_date="2026-12-01",
                               note="Priority changed")
    assert library.get_queue_entry(three)["note"]=="Priority changed"
    assert library.list_queue(priority="high")[0]["work_id"]==three
    assert library.list_queue(search="priority changed")[0]["work_id"]==three
    assert library.remove_from_queue(one)
    assert [r["work_id"] for r in library.list_queue()]==[three,two]
    assert [r["sort_order"] for r in library.list_queue()]==[0,1]
    assert library.remove_from_queue(one) is False


@pytest.mark.parametrize("bad",["tomorrow","2026-02-30","2026/01/01","2201-01-01"])
def test_queue_rejects_bad_dates(library,bad):
    work=add_work(library)
    with pytest.raises(ValueError,match="date"):
        library.enqueue_work(work,target_date=bad)
    assert library.list_queue()==[]


@pytest.mark.parametrize("bad",["urgent","",1,"later","blocked"])
def test_queue_rejects_invalid_priorities(library,bad):
    work=add_work(library)
    if bad=="":
        # Empty is intentionally a neutral default when fields are missing.
        assert library.enqueue_work(work,priority=bad)
    else:
        with pytest.raises(ValueError,match="Priority"):
            library.enqueue_work(work,priority=bad)


def test_queue_work_removal_and_reading_history_independent(library):
    work=add_work(library,status="reading")
    library.record_progress_on(work,14,date="2026-10-12")
    before=library.history(work)
    library.enqueue_work(work,priority="high")
    library.remove_from_queue(work)
    assert library.history(work)==before
    assert library.active_run(work)["current_progress"]==14
    library.enqueue_work(work)
    library.delete_work(work)
    assert library.list_queue()==[]


def test_queue_search_escapes_sql_wildcards(library):
    one=add_work(library,"Title %")
    two=add_work(library,"Title Other")
    library.enqueue_work(one)
    library.enqueue_work(two)
    assert len(library.list_queue(search="%"))==1


def test_local_file_attach_relink_rename_and_detach(library,tmp_path):
    work=add_work(library)
    original=tmp_path/"My Novel.epub"
    original.write_bytes(b"fake EPUB data")
    ident=library.add_local_file(work,original,label="Book on SSD")
    assert library.add_local_file(work,original)==ident
    assert library.get_local_file(ident)["filename"]=="My Novel.epub"
    assert library.list_local_files(work)[0]["available"] is True
    library.rename_local_file(ident,"Downloaded fanfiction")
    assert library.get_local_file(ident)["label"]=="Downloaded fanfiction"
    original.unlink()
    assert library.list_local_files(work)[0]["available"] is False
    new=tmp_path/"My Novel moved.epub"
    new.write_bytes(b"moved file")
    library.relink_local_file(ident,new)
    assert library.list_local_files(work)[0]["available"] is True
    assert library.get_local_file(ident)["size_bytes"]==len(b"moved file")
    assert library.remove_local_file(ident)
    assert new.read_bytes()==b"moved file"  # Unlink never deletes real files.
    assert not library.remove_local_file(ident)


@pytest.mark.parametrize("ext",["epub","m4b","pdf","mobi","azw3","cbz","mp3","flac","ogg","wav","txt"])
def test_common_book_and_audio_file_types_supported(library,tmp_path,ext):
    work=add_work(library)
    file=tmp_path/("book."+ext)
    file.write_bytes(b"not decoded or altered")
    ident=library.add_local_file(work,file)
    assert library.get_local_file(ident)["format"]==ext
    assert file.read_bytes()==b"not decoded or altered"


@pytest.mark.parametrize("suffix",["exe","sh","py","dll","desktop","tar","zip"])
def test_executable_or_unrelated_file_types_not_accepted(library,tmp_path,suffix):
    work=add_work(library)
    filename=tmp_path/("file."+suffix)
    filename.write_text("sample")
    with pytest.raises(ValueError,match="Unsupported"):
        library.add_local_file(work,filename)
    assert library.list_local_files(work)==[]


def test_local_file_path_validation_and_duplicate_relink(library,tmp_path):
    work=add_work(library)
    a=tmp_path/"one.m4b"
    b=tmp_path/"two.m4b"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    x=library.add_local_file(work,a)
    y=library.add_local_file(work,b)
    with pytest.raises(ValueError,match="already linked"):
        library.relink_local_file(y,a)
    assert library.get_local_file(y)["filename"]=="two.m4b"
    with pytest.raises(ValueError):
        library.add_local_file(work,tmp_path/"absent.epub")
    assert library.get_local_file(x) is not None
    library.delete_work(work)
    assert library.list_local_files()==[]


@pytest.mark.parametrize("suffix",[".json",".xml",".btbackup"])
def test_v7_portable_queue_and_file_metadata_roundtrip(library,tmp_path,suffix):
    first=add_work(library,"The Wandering Inn",status="reading")
    second=add_work(library,"The Way of Kings")
    library.record_progress_on(first,25,date="2026-06-01")
    library.enqueue_work(first,priority="next",target_date="2026-12-01",
                         note="Keep reading the arc")
    library.enqueue_work(second,priority="low")
    audio=tmp_path/"narration.m4b"
    audio.write_bytes(b"\0"*200)
    file_id=library.add_local_file(first,audio,label="AudiobookAI output")
    output=tmp_path/("transfer"+suffix)
    export_file(library,output)
    if suffix==".json":
        raw=output.read_text()
        assert str(tmp_path) not in raw
        assert "narration.m4b" in raw
    if suffix==".btbackup":
        with zipfile.ZipFile(output) as archive:
            assert "library.json" in archive.namelist()
            assert not any(".m4b" in name for name in archive.namelist())
    other=Library(tmp_path/("other"+suffix+".sqlite3"))
    try:
        assert import_file(other,output)==2
        assert len(other.list_queue())==2
        assert other.list_queue()[0]["priority"]=="next"
        assert other.get_queue_entry(first)["target_date"]=="2026-12-01"
        assert other.get_local_file(file_id)["path"]==""
        assert not other.list_local_files(first)[0]["available"]
        assert other.list_local_files(first)[0]["label"]=="AudiobookAI output"
        assert import_file(other,output)==0
        assert len(other.list_queue())==2
        assert len(other.list_local_files(first))==1
        local_copy=tmp_path/("restored"+suffix+".m4b")
        local_copy.write_bytes(b"m4b")
        other.relink_local_file(file_id,local_copy)
        assert import_file(other,output)==0
        assert other.get_local_file(file_id)["path"]==str(local_copy.resolve())
        other.update_queue_entry(first,priority="high",note="Local plan")
        assert import_file(other,output)==0
        assert other.get_queue_entry(first)["note"]=="Local plan"
        assert import_file(other,output,strategy="replace")==2
        assert other.get_queue_entry(first)["note"]=="Keep reading the arc"
        assert other.get_local_file(file_id)["path"]==""
        assert len(other.history(first))==1
    finally:
        other.close()


def test_import_v6_data_without_queue_is_backward_compatible(library,tmp_path):
    work=add_work(library)
    backup=library.export_data()
    backup["schema_version"]=6
    backup.pop("reading_queue")
    backup.pop("local_files")
    restored=Library(tmp_path/"old-backup.sqlite3")
    try:
        assert restored.import_data(backup)==1
        assert restored.get_work(work) is not None
        assert restored.list_queue()==[]
        assert restored.list_local_files()==[]
    finally:
        restored.close()


def test_invalid_local_file_path_in_backup_rolls_back(library):
    work=add_work(library)
    lib=library.export_data()
    lib["local_files"]=[{
        "id":"malicious-link","work_id":work,
        "filename":"narration.m4b","format":"m4b",
        "path":"/tmp/private/narration.m4b",
        "size_bytes":500, "label":"Bad",
    }]
    with pytest.raises(ValueError,match="machine-specific"):
        library.import_data(lib,strategy="replace")
    assert library.get_work(work) is not None
    assert library.list_local_files()==[]


def test_malformed_queue_backup_rolls_back(library):
    work=add_work(library)
    backup=library.export_data()
    backup["reading_queue"]=[{
        "id":"bad","work_id":"not-a-work","sort_order":0,
        "priority":"normal","target_date":"","note":"",
    }]
    with pytest.raises(ValueError,match="Orphaned"):
        library.import_data(backup,strategy="replace")
    assert library.get_work(work) is not None


def test_migration_snapshot_and_idempotence(tmp_path):

    path=tmp_path/"prior-version.db"
    first=Library(path)
    book=add_work(first,notes="Important existing notes")
    first.close()
    with sqlite3.connect(path) as old:
        old.execute("DROP TABLE reading_queue")
        old.execute("DROP TABLE local_files")
        old.execute("PRAGMA user_version=6")
    migrated=Library(path)
    try:
        snapshots=list((tmp_path/"backups").glob("BookTracker-before-v013-*.sqlite3"))
        assert len(snapshots)==1
        with sqlite3.connect(snapshots[0]) as read:
            assert read.execute("PRAGMA user_version").fetchone()[0]==6
        assert migrated.db.execute("PRAGMA user_version").fetchone()[0]==9
        assert migrated.get_work(book)["notes"]=="Important existing notes"
        migrated.enqueue_work(book)
    finally:
        migrated.close()
    again=Library(path)
    try:
        assert len(again.list_queue())==1
        assert len(list((tmp_path/"backups").glob("BookTracker-before-v013-*.sqlite3")))==1
    finally:
        again.close()


def test_gui_queue_and_file_manager_app_launch(app,library,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from booktracker.gui import LocalFilesDialog, MainWindow, QueueEntryDialog

    first=add_work(library,"First",status="reading")
    second=add_work(library,"Second")
    library.record_progress_on(first,10,date="2026-10-21")
    library.enqueue_work(first,priority="next")
    library.enqueue_work(second,priority="low")
    main=MainWindow(library)
    try:
        assert len(main.nav_buttons)==13
        main.navigate(10)
        assert main.queue_table.rowCount()==2
        assert main.queue_summary_label.text().startswith("2 queued")
        main.queue_table.selectRow(0)
        assert main.selected_queue_work_id()==first
        main.move_queued(1)
        assert library.list_queue()[0]["work_id"]==second
        main.open_queue_book()
        assert main.pages.currentIndex()==0
        assert main.selected_work()["id"]==first
        dialog=QueueEntryDialog(main,library,existing=library.get_queue_entry(first))
        assert dialog.book.currentData()==first
        assert dialog.priority.currentData()=="next"
        dialog.close()
        source=tmp_path/"local.epub"
        source.write_bytes(b"ebook")
        manager=LocalFilesDialog(main,library,first)
        try:
            monkeypatch.setattr(
                QFileDialog,"getOpenFileNames",
                lambda *args,**kwargs:([str(source)],"EPUB")
            )
            manager.attach()
            assert manager.file_table.rowCount()==1
            assert manager.file_table.item(0,3).text()=="Available"
            source.unlink()
            manager.refresh()
            assert "Missing" in manager.file_table.item(0,3).text()
            replacement=tmp_path/"backup.epub"
            replacement.write_bytes(b"replacement")
            monkeypatch.setattr(
                QFileDialog,"getOpenFileName",
                lambda *args,**kwargs:(str(replacement),"EPUB")
            )
            manager.relink()
            assert manager.file_table.item(0,3).text()=="Available"
        finally:
            manager.close()
    finally:
        main.close()


def test_compact_sidebar_navigation_stays_accessible(app,library):
    from booktracker.gui import MainWindow

    window=MainWindow(library)
    try:
        window.resize(960,630)
        window.show()
        app.processEvents()
        assert len(window.nav_buttons)==13
        assert window.nav_buttons[-3].text().endswith("Reading queue")
        assert window.nav_buttons[-2].text().endswith("Reading habits")
        assert window.nav_buttons[-1].text().endswith("Audiobooks")
        window.navigate(10)
        assert window.pages.currentIndex()==10
    finally:
        window.close()


def test_bulk_relink_from_folder_matches_exact_filenames(library,tmp_path):
    first=add_work(library,"Work A")
    second=add_work(library,"Work B")
    old=tmp_path/"originals"
    old.mkdir()
    old_a=old/"Harry Potter.m4b"
    old_b=old/"Series.epub"
    old_a.write_bytes(b"audio")
    old_b.write_bytes(b"epub")
    aid=library.add_local_file(first,old_a)
    bid=library.add_local_file(second,old_b)
    old_a.unlink()
    old_b.unlink()
    new=tmp_path/"new-computer"
    (new/"subfolder"/"series").mkdir(parents=True)
    (new/"subfolder"/"Harry Potter.m4b").write_bytes(b"audio content")
    (new/"subfolder"/"series"/"Series.epub").write_bytes(b"ebook content")
    result=library.relink_from_directory(new)
    assert result=={"relinked":2,"missing":0,"ambiguous":0,"scanned":2}
    assert Path(library.get_local_file(aid)["path"]).is_file()
    assert Path(library.get_local_file(bid)["path"]).is_file()
    assert library.relink_from_directory(new)["relinked"]==0
    assert (new/"subfolder"/"Harry Potter.m4b").read_bytes()==b"audio content"


def test_bulk_relink_does_not_guess_ambiguous_name(library,tmp_path):
    key=add_work(library)
    file=tmp_path/"book.epub"
    file.write_bytes(b"old")
    ident=library.add_local_file(key,file)
    file.unlink()
    library_one=tmp_path/"copies"/"A"
    library_two=tmp_path/"copies"/"B"
    library_one.mkdir(parents=True)
    library_two.mkdir(parents=True)
    (library_one/"book.epub").write_bytes(b"one")
    (library_two/"book.epub").write_bytes(b"two")
    summary=library.relink_from_directory(tmp_path/"copies")
    assert summary["ambiguous"]==1
    assert summary["relinked"]==0
    assert library.get_local_file(ident)["path"]==str(file.resolve())


def test_relink_scan_limit_rolls_back(library,tmp_path):
    key=add_work(library)
    previous=tmp_path/"missing.m4b"
    previous.write_bytes(b"old")
    fid=library.add_local_file(key,previous)
    previous.unlink()
    folder=tmp_path/"many"
    folder.mkdir()
    (folder/"missing.m4b").write_bytes(b"new")
    (folder/"other.m4b").write_bytes(b"new")
    with pytest.raises(ValueError,match="exceeded"):
        library.relink_from_directory(folder,max_files=1)
    assert library.get_local_file(fid)["path"]==str(previous.resolve())


def test_relink_skips_symlinked_candidate(library,tmp_path):
    key=add_work(library)
    initial=tmp_path/"narration.m4b"
    initial.write_bytes(b"test")
    library.add_local_file(key,initial)
    initial.unlink()
    root=tmp_path/"recovery"
    root.mkdir()
    external=tmp_path/"outside.m4b"
    external.write_bytes(b"outside")
    (root/"narration.m4b").symlink_to(external)
    result=library.relink_from_directory(root)
    assert result["missing"]==1
    assert not library.list_local_files(key)[0]["available"]


def test_local_symlink_does_not_link_executable_target(library,tmp_path):
    key=add_work(library)
    target=tmp_path/"program.sh"
    target.write_text("#!/bin/sh")
    fake=tmp_path/"fake.epub"
    fake.symlink_to(target)
    with pytest.raises(ValueError,match="target type"):
        library.add_local_file(key,fake)


def test_plan_dialog_does_not_accept_unrecognized_title(app,library,monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.gui import QueueEntryDialog

    key=add_work(library)
    dialog=QueueEntryDialog(None,library)
    seen=[]
    monkeypatch.setattr(
        QMessageBox,"warning",
        lambda *_args:seen.append(True)
    )
    try:
        dialog.book.setEditText("A completely unrelated book title")
        dialog.validate_accept()
        assert seen
        assert not dialog.result()
        assert library.get_queue_entry(key) is None
    finally:
        dialog.close()


def test_relink_manager_folder_button(app,library,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from booktracker.gui import LocalFilesDialog

    key=add_work(library)
    missing=tmp_path/"a.epub"
    missing.write_bytes(b"original")
    fid=library.add_local_file(key,missing)
    missing.unlink()
    folder=tmp_path/"relinked"
    folder.mkdir()
    (folder/"a.epub").write_bytes(b"replacement")
    monkeypatch.setattr(
        QFileDialog,"getExistingDirectory",
        lambda *_args,**_kwargs:str(folder)
    )
    monkeypatch.setattr(
        QMessageBox,"information",lambda *_args,**_kwargs:None
    )
    dialog=LocalFilesDialog(None,library,key)
    try:
        dialog.relink_folder()
        assert library.get_local_file(fid)["path"]==str((folder/"a.epub").resolve())
        assert dialog.file_table.item(0,3).text()=="Available"
    finally:
        dialog.close()


def test_epub_import_auto_attaches_file_and_skips_duplicate_links(
    app,library,tmp_path,monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.gui import MainWindow

    epub=tmp_path/"Harry Potter Story.epub"
    epub.write_bytes(b"fake but readable through mocked metadata")
    monkeypatch.setattr(
        "booktracker.gui.read_epub_metadata",
        lambda *_args,**_kwargs:{
            "title":"Harry Potter Story","author":"Writer",
            "kind":"fanfiction","source_site":"AO3","source_id":"56111",
            "source_url":"https://archiveofourown.org/works/56111",
            "progress_unit":"chapters","total_units":42
        }
    )
    scanned=MainWindow._scan_epub_files([str(epub)],tmp_path/"covers")
    assert scanned["works"][0]["_local_file_path"]==str(epub.resolve())
    messages=[]
    monkeypatch.setattr(
        QMessageBox,"question",
        lambda *_args,**_kwargs:QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(
        QMessageBox,"information",
        lambda *_args:messages.append(True)
    )
    ui=MainWindow(library)
    try:
        ui.finish_epub_import(scanned,"completed")
        records=library.list_works()
        assert len(records)==1
        assert records[0]["status"]=="completed"
        assert records[0]["finished_at"]==""
        assert len(library.list_local_files(records[0]["id"]))==1
        assert library.list_local_files(records[0]["id"])[0]["available"]
        ui.finish_epub_import(scanned,"completed")
        assert len(library.list_local_files(records[0]["id"]))==1
        assert len(library.list_works())==1
        assert len(messages)==2
    finally:
        ui.close()


def test_v7_merge_remaps_queue_and_file_links_to_existing_work(tmp_path):
    source=Library(tmp_path/"source.db")
    work=source.add_work({
        "title":"The Way of Kings","author":"Brandon Sanderson",
        "isbn":"9781000000011",
    })
    source.enqueue_work(work,priority="high",note="From laptop")
    epub=tmp_path/"stormlight.epub"
    epub.write_bytes(b"metadata test")
    file_id=source.add_local_file(work,epub)
    backup=tmp_path/"cross-device.json"
    export_file(source,backup)
    source.close()
    target=Library(tmp_path/"desktop.db")
    try:
        existing=target.add_work({
            "title":"Stormlight One","author":"Brandon Sanderson",
            "isbn":"9781000000011"
        })
        assert import_file(target,backup)==0
        assert len(target.list_works())==1
        assert target.get_queue_entry(existing)["priority"]=="high"
        assert target.get_queue_entry(existing)["note"]=="From laptop"
        assert target.get_local_file(file_id)["work_id"]==existing
        assert target.get_local_file(file_id)["path"]==""
    finally:
        target.close()


def test_queue_start_reading_action_creates_real_run(app,library):
    from booktracker.gui import MainWindow

    work=add_work(library,"Read Me",status="plan_to_read")
    library.enqueue_work(work)
    ui=MainWindow(library)
    try:
        ui.navigate(10)
        ui.queue_table.selectRow(0)
        ui.start_queue_book()
        assert library.get_work(work)["status"]=="reading"
        assert library.active_run(work) is not None
        assert library.get_queue_entry(work) is None
        assert library.history(work)==[]
    finally:
        ui.close()


def test_catalog_csv_escapes_spreadsheet_formula_fields(library,tmp_path):
    import csv

    title='=HYPERLINK("https://example.org","Open")'
    author='+SUM(1,2)'
    library.add_work({"title":title,"author":author})
    output=tmp_path/"catalog.csv"
    export_file(library,output)
    with output.open(newline="",encoding="utf-8-sig") as stream:
        row=next(csv.DictReader(stream))
    assert row["title"]=="'"+title
    assert row["author"]=="'"+author
    structured=tmp_path/"catalog.json"
    export_file(library,structured)
    data=json.loads(structured.read_text())
    assert data["works"][0]["title"]==title
    assert data["works"][0]["author"]==author
