"""v0.13 offline AudiobookAI completed-part interoperability and M4B catalogue."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from booktracker.audiobooks import (
    _candidate,
    group_candidates,
    identify_output,
    probe_m4b,
    scan_audiobookai_database,
    scan_m4b_folder,
)
from booktracker.database import Library
from booktracker.integrations import export_file, import_file


@pytest.fixture
def db(tmp_path):
    value=Library(tmp_path/"BookTracker.sqlite3")
    yield value
    value.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    current=QApplication.instance() or QApplication([])
    assert current
    return current


def make_work(db,title="A New Player in the Game",author="Some Writer"):
    return db.add_work({
        "title":title,"author":author,"kind":"fanfiction",
        "status":"reading","progress_unit":"chapters",
    })


def make_studio(tmp_path,*,missing=False):
    root=tmp_path/"AudiobookAI"/"output"
    root.mkdir(parents=True)
    dbfile=root/"audiobookai_studio.sqlite3"
    connection=sqlite3.connect(dbfile)
    connection.executescript("""
    CREATE TABLE books (id INTEGER PRIMARY KEY,path TEXT,title TEXT,author TEXT,
                        sections INTEGER,words INTEGER,added TEXT);
    CREATE TABLE completed_parts (
        id INTEGER PRIMARY KEY,book_id INTEGER,source_job_id INTEGER,
        part_number INTEGER,indices TEXT,output_name TEXT,
        output_path TEXT,voice_path TEXT,finished TEXT,size_bytes INTEGER
    );
    """)
    connection.execute(
        "INSERT INTO books VALUES(42,'/some/book.epub',?,?,300,50000,'2026-10-01')",
        ("A New Player in the Game","Some Writer")
    )
    out=root/"generated"/"A New Player in the Game - Part(1).m4b"
    out.parent.mkdir()
    if not missing:
        out.write_bytes(b"M4B payload")
    connection.execute(
        "INSERT INTO completed_parts VALUES(1,42,52,1,?,?,?,?,'2026-10-20',2048)",
        (
            json.dumps([1,2,3]),
            "A New Player in the Game - Part(1)",
            str(out),"/voices/Michael Kramer.wav",
        )
    )
    connection.commit()
    connection.close()
    return dbfile,out


def candidate(path,*,title="A New Player in the Game",author="Some Writer",
              part=1,missing=False,full=False):
    values=_candidate(
        path=path,title=title,author=author,part=part,complete=full,
        narrator="Michael Kramer",section_first=1,section_last=3,
        origin="audiobookai",studio_id=42
    )
    values["duration_ms"]=320000
    values["chapter_count"]=3
    values["codec"]="aac"
    values["probe_status"]="ok" if not missing else "Missing local file"
    return values


@pytest.mark.parametrize(("name","title","part","complete","first","last"),[
    ("The Way of Kings - Part(12).m4b","The Way of Kings",12,False,0,0),
    ("A New Player in the Game - Part 3 (Sections 12-19).m4b",
     "A New Player in the Game",3,False,12,19),
    ("A_New_Player_in_the_Game_part_01_sections_123-137_32d13cf858be.m4b",
     "A New Player in the Game",1,False,123,137),
    ("The Way of Kings_COMPLETE.m4b","The Way of Kings",0,True,0,0),
    ("A Flower for the Soul.m4b","A Flower for the Soul",0,False,0,0),
])
def test_real_audiobookai_output_names(name,title,part,complete,first,last):
    assert identify_output(name)==(title,part,complete,first,last)


def test_ffprobe_metadata_and_chapters_from_file(tmp_path,monkeypatch):
    path=tmp_path/"Book.m4b"
    path.write_bytes(b"test content")
    fake={
        "format":{
            "duration":"8640.55",
            "tags":{
                "title":"Book","artist":"Brandon Sanderson",
                "composer":"Michael Kramer"
            }
        },
        "streams":[{"codec_type":"audio","codec_name":"aac"}],
        "chapters":[
            {"start_time":"0","end_time":"10","tags":{"title":"Intro"}},
            {"start_time":"10","end_time":"30","tags":{"title":"Chapter 1"}},
        ]
    }
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps(fake).encode())
    monkeypatch.setattr("booktracker.audiobooks.shutil.which",lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(subprocess,"run",run)
    data=probe_m4b(path)
    assert data["probe_status"]=="ok"
    assert data["duration_ms"]==8640550
    assert data["chapter_count"]==2
    assert data["title"]=="Book"
    assert data["author"]=="Brandon Sanderson"
    assert data["narrator"]=="Michael Kramer"
    assert data["codec"]=="aac"
    assert data["chapters"][1]["start_ms"]==10000
    assert calls[0][0][-1]==str(path)
    assert calls[0][1]["timeout"]<=30
    assert not calls[0][1].get("shell",False)


def test_ffprobe_missing_and_corrupt_does_not_guess_duration(tmp_path,monkeypatch):
    book=tmp_path/"not-a-real-file.m4b"
    book.write_bytes(b"invalid")
    monkeypatch.setattr("booktracker.audiobooks.shutil.which",lambda _:None)
    no_probe=probe_m4b(book)
    assert no_probe["duration_ms"]==0
    assert no_probe["probe_status"]=="ffprobe missing"
    monkeypatch.setattr("booktracker.audiobooks.shutil.which",lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        subprocess,"run",
        lambda *_,**__:SimpleNamespace(returncode=1,stdout=b"")
    )
    failure=probe_m4b(book)
    assert failure["probe_status"]=="Could not read audio metadata"
    assert failure["duration_ms"]==0


def test_readonly_studio_import_and_missing_receipts(tmp_path,monkeypatch):
    studio,part=make_studio(tmp_path)
    monkeypatch.setattr(
        "booktracker.audiobooks.probe_m4b",
        lambda _:{"duration_ms":5000,"chapter_count":3,"codec":"aac",
                  "title":"","author":"","narrator":"","chapters":[],
                  "probe_status":"ok"}
    )
    result=scan_audiobookai_database(studio)
    assert len(result["records"])==1
    row=result["records"][0]
    assert row["title"]=="A New Player in the Game"
    assert row["author"]=="Some Writer"
    assert row["narrator"]=="Michael Kramer"
    assert row["studio_book_id"]==42
    assert row["part_number"]==1
    assert row["section_first"]==1
    assert row["section_last"]==3
    assert row["duration_ms"]==5000
    assert row["available"]
    with sqlite3.connect(studio) as connection:
        assert connection.execute("SELECT COUNT(*) FROM completed_parts").fetchone()[0]==1
    part.unlink()
    again=scan_audiobookai_database(studio)
    assert not again["records"][0]["available"]
    assert again["records"][0]["probe_status"]=="Missing local file"


def test_studio_rejects_unrelated_sqlite(tmp_path):
    path=tmp_path/"foreign.db"
    sqlite3.connect(path).close()
    with pytest.raises(ValueError,match="not an AudiobookAI"):
        scan_audiobookai_database(path)


def test_studio_handles_cancel_without_touching_db(tmp_path):
    path,_=make_studio(tmp_path)
    result=scan_audiobookai_database(path,cancel=lambda:True)
    assert result["cancelled"]
    assert result["records"]==[]


def test_studio_archival_catalog_last_export(tmp_path,monkeypatch):
    file,_=make_studio(tmp_path,missing=True)
    archive=file.parent/"archive"
    archive.mkdir()
    archive_file=tmp_path/"archive"/"A New Player in the Game_COMPLETE.m4b"
    archive_file.parent.mkdir()
    archive_file.write_bytes(b"archive")
    (archive/"catalog.json").write_text(json.dumps({
        "version":1,"editions":{
            "/some/book.epub\n/narrator":{
                "title":"A New Player in the Game","author":"Some Writer",
                "narrator":"Michael Kramer",
                "last_export":str(archive_file)
            }
        }
    }))
    monkeypatch.setattr(
        "booktracker.audiobooks.probe_m4b",
        lambda _:{"duration_ms":1000,"chapter_count":1,"codec":"aac",
                  "title":"","author":"","narrator":"","chapters":[],
                  "probe_status":"ok"}
    )
    output=scan_audiobookai_database(file)
    assert len(output["records"])==2
    assert output["records"][1]["origin"]=="archive"
    assert output["records"][1]["is_complete"]
    assert output["records"][1]["duration_ms"]==1000
    assert output["records"][1]["studio_book_id"]==42
    assert len(group_candidates(output["records"]))==1


def test_folder_scan_bounds_symlinks_and_duplicate_titles(tmp_path,monkeypatch):
    root=tmp_path/"Audiobooks"
    inner=root/"narrator"/"parts"
    inner.mkdir(parents=True)
    (inner/"First Book - Part(1).m4b").write_bytes(b"a")
    (inner/"First Book - Part(2).m4b").write_bytes(b"b")
    (root/"shortcut.m4b").symlink_to(inner/"First Book - Part(1).m4b")
    deep=root/"one"/"two"/"three"/"four"/"five"/"six"
    deep.mkdir(parents=True)
    (deep/"ignored.m4b").write_bytes(b"d")
    monkeypatch.setattr(
        "booktracker.audiobooks.probe_m4b",
        lambda _:{"duration_ms":60_000,"chapter_count":2,"codec":"aac",
                  "title":"","author":"","narrator":"","chapters":[],
                  "probe_status":"ok"}
    )
    outcome=scan_m4b_folder(root)
    assert len(outcome["records"])==2
    assert [r["part_number"] for r in outcome["records"]]==[1,2]
    assert len(group_candidates(outcome["records"]))==1
    assert scan_m4b_folder(root,cancel=lambda:True)["cancelled"]
    with pytest.raises(ValueError,match="too many files"):
        scan_m4b_folder(root,max_files=1)


def test_import_existing_missing_dedup_no_reading_history(db,tmp_path):
    work=make_work(db)
    db.record_progress_on(work,17,date="2026-10-20")
    before=db.history(work)
    path=tmp_path/"A New Player in the Game - Part(1).m4b"
    path.write_bytes(b"fake audio")
    missing=tmp_path/"A New Player in the Game - Part(2).m4b"
    rows=[
        candidate(path),
        candidate(missing,part=2,missing=True),
    ]
    groups=group_candidates(rows)
    assert len(groups)==1
    stats=db.import_audio_files(rows,{groups[0]["key"]:work})
    assert stats=={"linked":2,"already_linked":0,"missing":1,"created":0,"unmatched":0}
    assert len(db.list_audio_files())==2
    assert db.audio_summary()[0]["missing_files"]==1
    assert db.audio_summary()[0]["files"]==2
    assert db.get_work(work)["status"]=="reading"
    assert db.active_run(work)["current_progress"]==17
    assert db.history(work)==before
    rerun=db.import_audio_files(rows,{groups[0]["key"]:work})
    assert rerun["linked"]==0
    assert rerun["already_linked"]==2
    assert len(db.list_local_files(work))==2
    missing.write_bytes(b"now available")
    rerun=db.import_audio_files(rows,{groups[0]["key"]:work})
    assert len(db.list_audio_files())==2
    assert rerun["linked"]==0


def test_import_create_book_once_and_never_claim_read(db,tmp_path):
    sample=tmp_path/"A Flower for the Soul - Part(1).m4b"
    sample.write_bytes(b"M4B")
    rows=[candidate(sample,title="A Flower for the Soul",
                    author="TheBlack'sResurgence")]
    key=group_candidates(rows)[0]["key"]
    stats=db.import_audio_files(rows,{key:"create"})
    assert stats["created"]==1
    created=db.list_works()
    assert len(created)==1
    assert created[0]["status"]=="plan_to_read"
    assert created[0]["progress"]==0
    assert db.history(created[0]["id"])==[]
    assert db.list_sessions()==[]
    stats=db.import_audio_files(rows,{key:"create"})
    assert stats["created"]==0
    assert stats["already_linked"]==1


def test_media_full_and_parts_not_double_counted(db,tmp_path):
    key=make_work(db)
    files=[]
    for number in (1,3):
        part=tmp_path/f"A New Player in the Game - Part({number}).m4b"
        part.write_bytes(b"data")
        files.append(candidate(part,part=number))
    whole=tmp_path/"A New Player in the Game_COMPLETE.m4b"
    whole.write_bytes(b"data")
    large=candidate(whole,part=0,full=True)
    large["duration_ms"]=600000
    files.append(large)
    grp=group_candidates(files)[0]
    db.import_audio_files(files,{grp["key"]:key})
    summary=db.audio_summary()[0]
    assert summary["parts"]==2
    assert summary["complete"]==1
    assert summary["missing_parts"]==[2]
    assert summary["duration_ms"]==640000
    assert summary["chapters"]==3


@pytest.mark.parametrize("ext",[".json",".xml",".btbackup"])
def test_v9_portability_without_media_bytes_or_paths(db,tmp_path,ext):
    key=make_work(db)
    original=tmp_path/"A New Player in the Game - Part(1).m4b"
    original.write_bytes(b"User-owned audio data should not appear in backup")
    rows=[candidate(original)]
    db.import_audio_files(rows,{group_candidates(rows)[0]["key"]:key})
    dest=tmp_path/("library"+ext)
    export_file(db,dest)
    assert str(original).encode() not in dest.read_bytes()
    assert b"User-owned audio data should not appear" not in dest.read_bytes()
    restored=Library(tmp_path/("other"+ext+".sqlite3"))
    try:
        assert import_file(restored,dest)==1
        assert restored.get_work(key)
        files=restored.list_audio_files()
        assert len(files)==1
        assert files[0]["path"]==""
        assert not files[0]["available"]
        assert files[0]["duration_ms"]==320000
        assert files[0]["part_number"]==1
        assert import_file(restored,dest)==0
        assert len(restored.list_audio_files())==1
        assert import_file(restored,dest,strategy="replace")==1
        assert len(restored.list_audio_files())==1
        assert dest.is_file()
    finally:
        restored.close()


def test_v8_export_backward_import(db,tmp_path):
    key=make_work(db)
    data=db.export_data()
    data["schema_version"]=8
    data.pop("audio_metadata")
    copy=Library(tmp_path/"copy.sqlite3")
    try:
        assert copy.import_data(data)==1
        assert copy.get_work(key)
        assert copy.list_audio_files()==[]
    finally:
        copy.close()


def test_invalid_v9_metadata_rollback(db,tmp_path):
    key=make_work(db)
    backup=db.export_data()
    backup["audio_metadata"]=[{
        "file_id":"missing-audio","duration_ms":60000
    }]
    with pytest.raises(ValueError,match="Orphaned audiobook"):
        db.import_data(backup,strategy="replace")
    assert db.get_work(key)


def test_malicious_m4b_filename_rejected(db):
    key=make_work(db)
    values={
        "filename":"../dangerous.m4b","path":"","title":"Book",
        "author":"Writer","studio_book_id":0
    }
    group=group_candidates([values])[0]
    with pytest.raises(ValueError,match="Invalid M4B"):
        db.import_audio_files([values],{group["key"]:key})
    assert db.list_audio_files()==[]


def test_linked_audio_unlink_cascades_metadata(db,tmp_path):
    key=make_work(db)
    original=tmp_path/"Book.m4b"
    original.write_bytes(b"m4b")
    data=[candidate(original)]
    db.import_audio_files(data,{group_candidates(data)[0]["key"]:key})
    file_id=db.list_audio_files()[0]["id"]
    assert db.remove_local_file(file_id)
    assert db.list_audio_files()==[]
    assert original.exists()


def test_v8_schema_migration_creates_wal_safe_v013_snapshot(tmp_path):
    path=tmp_path/"BookTracker.sqlite3"
    previous=Library(path)
    key=make_work(previous)
    previous.close()
    with sqlite3.connect(path) as old:
        old.execute("DROP TABLE audio_metadata")
        old.execute("PRAGMA user_version=8")
    upgraded=Library(path)
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0]==10
        assert upgraded.get_work(key)
        snapshots=list((tmp_path/"backups").glob("BookTracker-before-v014-*.sqlite3"))
        assert len(snapshots)==1
        with sqlite3.connect(snapshots[0]) as snap:
            assert snap.execute("PRAGMA user_version").fetchone()[0]==8
            assert snap.execute("SELECT COUNT(*) FROM works").fetchone()[0]==1
    finally:
        upgraded.close()
    again=Library(path)
    try:
        assert len(list((tmp_path/"backups").glob("BookTracker-before-v014-*.sqlite3")))==1
    finally:
        again.close()



def test_import_preview_unique_title_author_and_manual_choice(app,db,tmp_path):
    from booktracker.audio_gui import AudioImportPreview

    first=make_work(db)
    make_work(db,"A New Player in the Game","Different Author")
    original=tmp_path/"A New Player in the Game - Part(1).m4b"
    original.write_bytes(b"small")
    rows=[
        candidate(original),
        candidate(
            tmp_path/"Other Audiobook - Part(1).m4b",
            title="Other Audiobook",author="",part=1,missing=True
        ),
    ]
    dialog=AudioImportPreview(None,db,rows)
    try:
        assert dialog.group_table.rowCount()==2
        group_to_choice={
            group["title"]:dialog.choices[i].currentData()
            for i,group in enumerate(dialog.groups)
        }
        assert group_to_choice["A New Player in the Game"]==first
        assert group_to_choice["Other Audiobook"] is None
        assert dialog.file_list.rowCount()==1
        assert dialog.include_missing.isChecked()
        assert len(dialog.selected_records())==2
        dialog.include_missing.setChecked(False)
        assert len(dialog.selected_records())==1
        assert dialog.choices[0].findData("create")>=0
    finally:
        dialog.close()


def test_audio_library_screen_group_detail_and_reread_independence(app,db,tmp_path):
    from booktracker.gui import MainWindow

    work=make_work(db)
    part=tmp_path/"A New Player in the Game - Part(1).m4b"
    part.write_bytes(b"sample")
    rows=[candidate(part)]
    db.import_audio_files(rows,{group_candidates(rows)[0]["key"]:work})
    db.record_progress_on(work,11,date="2026-10-20")
    window=MainWindow(db)
    try:
        assert len(window.nav_buttons)==14
        window.navigate(12)
        page=window.audio_page
        assert window.pages.currentIndex()==12
        assert page.groups.rowCount()==1
        assert page.files.rowCount()==1
        assert page.current_group()["work_id"]==work
        assert page.current_file()["filename"].endswith(".m4b")
        assert "5:20" in page.summary.text()
        page.show_missing.setChecked(True)
        assert page.groups.rowCount()==0
        page.show_missing.setChecked(False)
        assert page.groups.rowCount()==1
        page.search.setText("Michael Kramer")
        assert page.groups.rowCount()==1
        page.search.setText("nonmatching")
        assert page.groups.rowCount()==0
        page.search.setText("")
        page.open_book()
        assert window.pages.currentIndex()==0
        assert window.selected_work()["id"]==work
        assert db.active_run(work)["current_progress"]==11
    finally:
        window.close()


def test_audio_gui_preview_save_and_no_inferred_history(app,db,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.audio_gui import AudioImportPreview
    from booktracker.gui import MainWindow

    work=make_work(db)
    path=tmp_path/"A New Player in the Game - Part(1).m4b"
    path.write_bytes(b"audio")
    rows=[candidate(path)]
    monkeypatch.setattr(
        AudioImportPreview,"exec",
        lambda self:self.DialogCode.Accepted
    )
    monkeypatch.setattr(
        QMessageBox,"information",lambda *_args,**_kwargs:None
    )
    window=MainWindow(db)
    try:
        window.audio_page.scan_complete({"records":rows})
        assert len(db.list_audio_files())==1
        assert window.audio_page.groups.rowCount()==1
        assert db.get_work(work)["status"]=="reading"
        assert db.list_sessions()==[]
        assert db.history(work)==[]
        window.audio_page.scan_complete({"records":rows})
        assert len(db.list_audio_files())==1
    finally:
        window.close()


def test_audio_scan_thread_discovery_with_fake_probe(app,tmp_path,monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer

    from booktracker.audio_gui import AudioScanThread

    file=tmp_path/"Epic - Part(1).m4b"
    file.write_bytes(b"audio")
    monkeypatch.setattr(
        "booktracker.audiobooks.probe_m4b",
        lambda _:{"duration_ms":50000,"chapter_count":3,"codec":"aac",
                  "title":"","author":"","narrator":"","chapters":[],
                  "probe_status":"ok"}
    )
    worker=AudioScanThread("folder",tmp_path)
    results=[]
    failures=[]
    loop=QEventLoop()
    worker.found.connect(results.append)
    worker.failed.connect(failures.append)
    worker.finished.connect(loop.quit)
    timer=QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(5000)
    worker.start()
    loop.exec()
    assert worker.wait(5000)
    assert not failures
    assert results[0]["records"][0]["duration_ms"]==50000
    assert results[0]["records"][0]["part_number"]==1


def test_audio_import_atomic_when_one_file_invalid(db,tmp_path):
    work=make_work(db)
    first=tmp_path/"A New Player in the Game - Part(1).m4b"
    first.write_bytes(b"audio")
    rows=[candidate(first)]
    broken=candidate(tmp_path/"A New Player in the Game - Part(2).m4b")
    broken["filename"]="../bad.m4b"
    rows.append(broken)
    group=group_candidates(rows)[0]
    with pytest.raises(ValueError,match="Invalid M4B"):
        db.import_audio_files(rows,{group["key"]:work})
    assert not db.list_audio_files()
    assert not db.list_local_files(work)


def test_audio_metadata_refresh_keeps_known_title_when_untagged(db,tmp_path):
    book=make_work(db)
    path=tmp_path/"A New Player in the Game - Part(1).m4b"
    path.write_bytes(b"audio")
    rows=[candidate(path)]
    db.import_audio_files(rows,{group_candidates(rows)[0]["key"]:book})
    entry=db.list_audio_files()[0]
    db.update_audio_probe(entry["id"],{
        "duration_ms":800000,"chapter_count":9,"codec":"aac",
        "title":"","author":"","narrator":"",
        "probe_status":"ok"
    })
    changed=db.list_audio_files()[0]
    assert changed["audio_title"]=="A New Player in the Game"
    assert changed["audio_author"]=="Some Writer"
    assert changed["narrator"]=="Michael Kramer"
    assert changed["duration_ms"]==800000


def test_missing_audio_receipt_can_be_relinked_without_loss(db,tmp_path):
    work=make_work(db)
    missing=tmp_path/"elsewhere"/"A New Player in the Game - Part(1).m4b"
    rows=[candidate(missing,missing=True)]
    db.import_audio_files(rows,{group_candidates(rows)[0]["key"]:work})
    file_id=db.list_audio_files()[0]["id"]
    assert not db.list_audio_files()[0]["available"]
    real=tmp_path/"new_drive"/"A New Player in the Game - Part(1).m4b"
    real.parent.mkdir()
    real.write_bytes(b"audio bytes")
    outcome=db.relink_from_directory(real.parent,work_id=work)
    assert outcome["relinked"]==1
    entry=db.list_audio_files()[0]
    assert entry["available"]
    assert entry["id"]==file_id
    assert entry["duration_ms"]==320000
    assert real.read_bytes()==b"audio bytes"


def test_archive_and_parts_are_independent_narrators(db,tmp_path):
    work=make_work(db)
    original=tmp_path/"Audio - Part(1).m4b"
    original.write_bytes(b"part")
    another=tmp_path/"Audio - Part(2).m4b"
    another.write_bytes(b"part")
    first=candidate(original)
    second=candidate(another,part=2)
    second["narrator"]="British Narrator"
    data=[first,second]
    grp=group_candidates(data)[0]
    db.import_audio_files(data,{grp["key"]:work})
    assert len(db.audio_summary())==2
    assert sorted(g["narrator"] for g in db.audio_summary())==[
        "British Narrator","Michael Kramer"
    ]



def test_reimport_when_ffprobe_unavailable_keeps_previous_audio_facts(db,tmp_path):
    work=make_work(db)
    file=tmp_path/"A New Player in the Game - Part(1).m4b"
    file.write_bytes(b"part bytes")
    original=candidate(file)
    group=group_candidates([original])[0]
    db.import_audio_files([original],{group["key"]:work})
    weaker=dict(original)
    weaker.update({
        "duration_ms":0,"chapter_count":0,"codec":"",
        "title":"","author":"","narrator":"",
        "origin":"folder","part_number":0,
        "section_first":0,"section_last":0,"studio_book_id":0,
        "probe_status":"ffprobe missing"
    })
    # Force the same source group to be matched by an explicit user decision.
    newgroup=group_candidates([weaker])[0]
    db.import_audio_files([weaker],{newgroup["key"]:work})
    record=db.list_audio_files()[0]
    assert record["duration_ms"]==320000
    assert record["chapter_count"]==3
    assert record["audio_title"]=="A New Player in the Game"
    assert record["audio_author"]=="Some Writer"
    assert record["narrator"]=="Michael Kramer"
    assert record["origin"]=="audiobookai"
    assert record["part_number"]==1
    assert record["section_last"]==3


def test_failed_metadata_refresh_preserves_recorded_duration(db,tmp_path):
    work=make_work(db)
    file=tmp_path/"A New Player in the Game - Part(1).m4b"
    file.write_bytes(b"part bytes")
    original=candidate(file)
    group=group_candidates([original])[0]
    db.import_audio_files([original],{group["key"]:work})
    file_id=db.list_audio_files()[0]["id"]
    db.update_audio_probe(file_id,{
        "duration_ms":0,"chapter_count":0,"codec":"",
        "probe_status":"ffprobe missing"
    })
    record=db.list_audio_files()[0]
    assert record["duration_ms"]==320000
    assert record["chapter_count"]==3
    assert record["codec"]=="aac"
    assert record["probe_status"]=="ffprobe missing"


def test_audio_page_cancel_action_does_not_change_library(app,db):
    from booktracker.gui import MainWindow

    work=make_work(db)
    screen=MainWindow(db)
    try:
        screen.navigate(12)
        assert screen.audio_page.cancel_scan_button.isEnabled() is False
        screen.audio_page.stop_scan()
        assert screen.audio_page.worker is None
        assert db.get_work(work)["status"]=="reading"
        assert db.list_audio_files()==[]
    finally:
        screen.close()
