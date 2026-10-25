"""BookTracker v0.12 subscription, source pacing and observed release tests."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from booktracker.database import Library
from booktracker.integrations import export_file, import_file, source_from_url
from booktracker.story_metadata import parse_story_html


@pytest.fixture
def db(tmp_path):
    library=Library(tmp_path/"library.sqlite3")
    yield library
    library.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    qapp=QApplication.instance() or QApplication([])
    assert qapp is not None
    return qapp


def serial(db,title="Long-running Story",total=30):
    key=db.add_work({
        "title":title,"author":"Writer","kind":"web_novel",
        "progress_unit":"chapters","status":"reading","total_units":total,
    })
    story_id=int(hashlib.sha256(title.encode("utf-8")).hexdigest()[:9],16)
    source=db.add_source(
        key,f"https://www.royalroad.com/fiction/{story_id}/"
        +title.lower().replace(" ","-")
    )
    return key,source


def test_follow_ack_unfollow_and_cascade(db):
    work,source=serial(db,total=20)
    record=db.follow_work(work)
    assert record["seen_units"]==20
    assert not record["notify"]
    db.set_follow_notification(work,True)
    assert db.get_follow(work)["notify"]==1
    db.record_source_check(source,observed_units=25)
    assert db.source_update_summary()[0]["new_since_seen"]==5
    assert db.source_update_summary()[0]["unread"]==25
    db.acknowledge_releases(work)
    assert db.source_update_summary()[0]["new_since_seen"]==0
    assert db.get_work(work)["total_units"]==25
    assert db.unfollow_work(work)
    assert db.get_follow(work) is None
    assert len(db.list_serial_releases(work_id=work))==1
    db.follow_work(work,notify=True)
    db.delete_work(work)
    assert db.list_serial_releases()==[]
    assert db.get_follow(work) is None


def test_follows_maintain_personal_reading_history(db):
    work,source=serial(db,total=50)
    db.record_progress_on(work,40,date="2026-08-12")
    snapshot=db.history(work)
    db.follow_work(work)
    db.record_source_check(source,observed_units=56)
    assert db.get_work(work)["status"]=="reading"
    assert db.active_run(work)["current_progress"]==40
    assert db.history(work)==snapshot
    assert db.source_update_summary()[0]["new_since_seen"]==6
    db.record_source_check(source,observed_units=55)
    assert len(db.list_serial_releases())==1
    db.record_source_check(source,error="Cloudflare 403")
    assert len(db.list_serial_releases())==1
    assert db.get_follow(work)["seen_units"]==50
    assert db.source_reliability()[0]["failures"]==1


def test_first_observation_is_baseline_not_new_release(db):
    work,source=serial(db,total=0)
    db.follow_work(work)
    assert db.get_follow(work)["seen_units"]==0
    assert db.record_source_check(source,observed_units=100)=="updated"
    assert db.get_follow(work)["seen_units"]==100
    assert db.source_update_summary()[0]["new_since_seen"]==0
    assert db.list_serial_releases()==[]
    db.record_source_check(source,observed_units=102)
    assert len(db.list_serial_releases())==1
    event=db.list_serial_releases()[0]
    assert (event["old_units"],event["new_units"])==(100,102)
    assert db.source_update_summary()[0]["new_since_seen"]==2


def test_mirror_does_not_duplicate_observed_release(db):
    work,one=serial(db,total=30)
    two=db.add_source(work,"https://archiveofourown.org/works/778899")
    db.follow_work(work)
    db.record_source_check(one,observed_units=34)
    db.record_source_check(two,observed_units=34)
    db.record_source_check(two,observed_units=40)
    events=db.list_serial_releases()
    assert [(r["old_units"],r["new_units"]) for r in events]==[
        (34,40),(30,34)
    ]
    db.remove_source(two)
    assert db.list_serial_releases()[0]["source_id"] is None


def test_source_error_unknown_and_lower_counts_never_create_releases(db):
    work,source=serial(db,total=20)
    db.record_source_check(source,observed_units=0)
    db.record_source_check(source,error="blocked")
    db.record_source_check(source,observed_units=18)
    assert not db.list_serial_releases()
    assert db.get_work(work)["total_units"]==20


def test_follow_only_serial_types(db):
    work=db.add_work({"title":"Printed Book","kind":"book"})
    with pytest.raises(ValueError,match="fanfiction"):
        db.follow_work(work)
    with pytest.raises(KeyError):
        db.follow_work("missing-work")


def test_batch_candidates_one_supported_per_work_and_cooldown(db):
    work,_ao3=serial(db,total=50)
    alternate=db.add_source(work,"https://archiveofourown.org/works/999988")
    db.set_primary_source(alternate)
    untracked,_other=serial(db,title="Not Followed",total=70)
    db.follow_work(work)
    current=datetime.now(timezone.utc)
    eligible=db.batch_source_candidates(
        followed_only=True,current_time=current,minimum_hours=12,limit=20
    )
    assert len(eligible["candidates"])==1
    assert eligible["candidates"][0]["source_id"]==alternate
    db.record_source_check(alternate,observed_units=50)
    due=db.batch_source_candidates(
        followed_only=True,current_time=current,minimum_hours=12
    )
    assert due["candidates"]==[]
    assert due["cooldown"]==1
    bypass=db.batch_source_candidates(
        followed_only=True,current_time=current,minimum_hours=0
    )
    assert len(bypass["candidates"])==1
    all_entries=db.batch_source_candidates(
        followed_only=False,current_time=current,minimum_hours=0
    )
    assert len(all_entries["candidates"])==2
    assert any(e["work_id"]==untracked for e in all_entries["candidates"])
    assert len([e for e in all_entries["candidates"] if e["work_id"]==work])==1


def test_batch_skips_unsupported_and_completed(db):
    work,source=serial(db,total=20)
    db.follow_work(work)
    other=db.add_work({"title":"Other provider","kind":"fanfiction"})
    db.add_source(other,"https://unknown.example.org/fiction/abc")
    db.follow_work(other)
    done,source_done=serial(db,title="Completed Serial",total=20)
    db.follow_work(done)
    db.update_work(done,{"status":"completed","publication_status":"completed"})
    result=db.batch_source_candidates(followed_only=True,minimum_hours=0)
    assert [entry["work_id"] for entry in result["candidates"]]==[work]
    assert result["unsupported"]==1
    assert source != source_done


@pytest.mark.parametrize("bad",[-1,721,100000])
def test_batch_cooldown_validation(db,bad):
    with pytest.raises(ValueError):
        db.batch_source_candidates(minimum_hours=bad)


def test_batch_limit_enforced(db):
    for number in range(5):
        key,_source=serial(db,title=f"Series {number}",total=10)
        db.follow_work(key)
    result=db.batch_source_candidates(limit=2,minimum_hours=0)
    assert len(result["candidates"])==2
    assert result["remaining"]==3


@pytest.mark.parametrize("ext",[".json",".xml",".btbackup"])
def test_v8_roundtrip_and_idempotent_merge(db,tmp_path,ext):
    work,source=serial(db,total=40)
    db.follow_work(work,notify=True)
    db.record_source_check(source,observed_units=44)
    db.record_source_check(source,observed_units=45)
    db.acknowledge_releases(work)
    db.record_source_check(source,observed_units=47)
    saved=db.export_data()
    assert saved["schema_version"]==9
    out=tmp_path/("portable"+ext)
    export_file(db,out)
    restore=Library(tmp_path/("restore"+ext+".sqlite3"))
    try:
        assert import_file(restore,out)==1
        assert restore.get_follow(work)["seen_units"]==45
        assert restore.get_follow(work)["notify"]==1
        assert len(restore.list_serial_releases())==3
        assert restore.source_update_summary()[0]["new_since_seen"]==2
        assert import_file(restore,out)==0
        assert len(restore.list_serial_releases())==3
        restore.set_follow_notification(work,False)
        restore.acknowledge_releases(work)
        assert import_file(restore,out)==0
        assert restore.get_follow(work)["notify"]==0
        assert restore.get_follow(work)["seen_units"]==47
        assert import_file(restore,out,strategy="replace")==1
        assert restore.get_follow(work)["notify"]==1
        assert restore.get_follow(work)["seen_units"]==45
    finally:
        restore.close()


def test_old_v7_backup_compatible(db,tmp_path):
    work,_source=serial(db,total=30)
    old=db.export_data()
    old["schema_version"]=7
    old.pop("serial_follows")
    old.pop("serial_releases")
    other=Library(tmp_path/"old.sqlite3")
    try:
        assert other.import_data(old)==1
        assert other.get_follow(work) is None
        assert other.get_work(work)["title"]=="Long-running Story"
    finally:
        other.close()


def test_invalid_v8_backup_rolls_back(db):
    work,_source=serial(db,total=20)
    old=db.export_data()
    old["serial_follows"]=[{
        "work_id":"missing","notify":True,"seen_units":20
    }]
    with pytest.raises(ValueError,match="Orphaned serial follow"):
        db.import_data(old,strategy="replace")
    assert db.get_work(work)
    assert db.list_sources(work)


def test_invalid_release_backup_rolls_back(db):
    work,source=serial(db,total=20)
    old=db.export_data()
    old["serial_releases"]=[{
        "id":"fake","work_id":work,"source_id":source,
        "old_units":30,"new_units":20
    }]
    with pytest.raises(ValueError,match="Invalid release chapter range"):
        db.import_data(old,strategy="replace")
    assert db.get_work(work)
    assert db.list_serial_releases()==[]


def test_v7_schema_migration_snapshot_idempotent(tmp_path):
    dbfile=tmp_path/"database.sqlite3"
    initial=Library(dbfile)
    work,_source=serial(initial,total=23)
    initial.close()
    with sqlite3.connect(dbfile) as legacy:
        legacy.execute("DROP TABLE serial_releases")
        legacy.execute("DROP TABLE serial_follows")
        legacy.execute("PRAGMA user_version=7")
    upgraded=Library(dbfile)
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0]==9
        assert upgraded.get_work(work)["total_units"]==23
        backups=list((tmp_path/"backups").glob("BookTracker-before-v013-*.sqlite3"))
        assert len(backups)==1
        with sqlite3.connect(backups[0]) as snapshot:
            assert snapshot.execute("PRAGMA user_version").fetchone()[0]==7
            assert snapshot.execute(
                "SELECT COUNT(*) FROM works"
            ).fetchone()[0]==1
    finally:
        upgraded.close()
    again=Library(dbfile)
    try:
        assert len(list((tmp_path/"backups").glob("BookTracker-before-v013-*.sqlite3")))==1
        assert again.get_work(work)["title"]=="Long-running Story"
    finally:
        again.close()


def test_provider_parsers_count_scoped_chapters_without_duplicates():
    source=source_from_url("https://www.royalroad.com/fiction/223344/a-story")
    html="""<html><head><meta property="og:title" content="The Story - Royal Road">
    </head><div id="chapters"><table>
    <tr data-url="/fiction/223344/chapter/100/one"><td><a href="/fiction/223344/chapter/100/one">One</a></td></tr>
    <tr data-url="/fiction/223344/chapter/101/two"><td>Two</td></tr>
    <tr data-url="/fiction/223344/chapter/102/three"><td>Three</td></tr>
    </table></div><a href="/fiction/223344/chapter/100/one">Navigation duplicate</a></html>"""
    assert parse_story_html(html,source)["total_units"]==3

    source=source_from_url("https://www.fanfiction.net/s/12345/1/story")
    html="""<html><head><meta property="og:title" content="The Story - FanFiction.Net"></head>
    <div id="profile_top">Words: 210,000 - Chapters: 28 - Reviews: 18</div></html>"""
    assert parse_story_html(html,source)["total_units"]==28

    source=source_from_url("https://www.scribblehub.com/series/9999/story/")
    html="""<html><head><meta property="og:title" content="The Story - Scribble Hub"></head>
    <div class="chapter_list">
    <a href="https://www.scribblehub.com/read/9999-story/chapter/1111/">Chapter one</a>
    <a href="https://www.scribblehub.com/read/9999-story/chapter/1112/">Chapter two</a>
    </div></html>"""
    assert parse_story_html(html,source)["total_units"]==2


def test_provider_count_unknown_when_only_titles_or_navigation():
    source=source_from_url("https://www.royalroad.com/fiction/12345/my-title")
    html="""<html><head><title>Story - Royal Road</title></head><body>
    <a href="/fiction/12345/chapter/33333/teaser">Next chapter</a>
    <p>This story has 999 chapters and 7,123 pages!</p>
    </body></html>"""
    assert parse_story_html(html,source)["total_units"]==0
    source=source_from_url("https://forums.spacebattles.com/threads/story.123456/")
    assert parse_story_html(html,source)["total_units"]==0


def test_source_reliability_aggregates_site_failures(db):
    _work,source=serial(db,total=10)
    db.record_source_check(source,observed_units=11)
    db.record_source_check(source,error="HTTP 403")
    db.record_source_check(source,observed_units=11)
    site=db.source_reliability()[0]
    assert site["checks"]==3
    assert site["successful_counts"]==2
    assert site["failures"]==1
    assert site["increases"]==1



def test_update_center_follow_ack_and_catch_up_ui(app,db,monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.gui import MainWindow

    work,source=serial(db,total=30)
    db.record_progress_on(work,22,date="2026-09-01")
    window=MainWindow(db)
    monkeypatch.setattr(
        QMessageBox,"question",lambda *_args,**_kwargs:QMessageBox.StandardButton.Yes
    )
    try:
        window.navigate(9)
        assert window.update_table.rowCount()==1
        assert window.update_table.columnCount()==9
        window.update_table.selectRow(0)
        assert window.update_follow_button.text()=="Follow story"
        window.toggle_follow_selected()
        assert db.get_follow(work)["seen_units"]==30
        assert window.update_follow_button.text()=="Unfollow story"
        window.toggle_selected_notifications()
        assert db.get_follow(work)["notify"]==1
        db.record_source_check(source,observed_units=35)
        window.refresh_updates_page()
        assert window.update_table.item(0,2).text()=="5"
        assert window.update_table.item(0,3).text()=="13"
        window.acknowledge_selected()
        assert db.source_update_summary()[0]["new_since_seen"]==0
        db.record_source_check(source,observed_units=38)
        window.catch_up_selected_serial()
        assert db.get_work(work)["status"]=="caught_up"
        assert db.active_run(work)["current_progress"]==38
        assert db.get_follow(work)["seen_units"]==38
        assert len(db.list_serial_releases())==2
        assert len(db.history(work))==2
        window.toggle_follow_selected()
        assert db.get_follow(work) is None
        assert db.get_work(work)["title"]=="Long-running Story"
    finally:
        window.close()


def test_batch_worker_success_failure_and_no_real_network(app,db,monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer

    from booktracker.gui import MainWindow, SerialCheckThread

    a,a_source=serial(db,title="First Serial",total=30)
    b,b_source=serial(db,title="Second Serial",total=50)
    db.follow_work(a,notify=True)
    db.follow_work(b,notify=True)
    calls=[]
    def fake_fetch(url):
        calls.append(url)
        if "/fiction/" not in url:
            raise AssertionError("Only recognized source URLs may be fetched")
        if "first-serial" in url:
            return {"total_units":33}
        raise ValueError("HTTP 403: blocked")
    class FakeWorker(SerialCheckThread):
        def __init__(self,entries):
            super().__init__(entries,fetch=fake_fetch)
    monkeypatch.setattr("booktracker.gui.SerialCheckThread",FakeWorker)
    win=MainWindow(db)
    try:
        win.navigate(9)
        loop=QEventLoop()
        timeout=QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        win.update_followed_only.setChecked(True)
        win.check_serial_sources()
        assert win.serial_worker is not None
        win.serial_worker.finished.connect(loop.quit)
        timeout.start(8000)
        loop.exec()
        win.serial_worker.wait()
        app.processEvents()
        assert len(calls)==2
        assert db.get_work(a)["total_units"]==33
        assert db.get_work(b)["total_units"]==50
        assert len(db.list_serial_releases())==1
        assert db.get_source(b_source)["last_error"].startswith("HTTP 403")
        assert win._serial_completed==1
        assert win._serial_errors==1
        assert not win.update_cancel_button.isEnabled()
        assert win.update_progress.value()==2
        # Both requests are now inside the usual minimum interval.
        assert db.batch_source_candidates(
            followed_only=True,minimum_hours=12
        )["candidates"]==[]
        assert db.get_source(a_source)["last_error"]==""
    finally:
        win.close()


def test_bulk_checker_skips_network_when_cooldown_empty(app,db,monkeypatch):
    from booktracker.gui import MainWindow

    work,source=serial(db,total=12)
    db.follow_work(work)
    db.record_source_check(source,observed_units=12)
    win=MainWindow(db)
    try:
        win.navigate(9)
        win.update_followed_only.setChecked(True)
        win.check_serial_sources()
        assert win.serial_worker is None
        assert win.update_progress.maximum()==1
        assert "No eligible stories" in win.statusBar().currentMessage()
    finally:
        win.close()


def test_csv_release_export_blocks_formula_spreadsheet_injection(
    app,db,tmp_path,monkeypatch
):
    import csv

    from PySide6.QtWidgets import QFileDialog

    from booktracker.gui import MainWindow

    work=db.add_work({
        "title":"=SUM(A1:A9)","kind":"fanfiction",
        "progress_unit":"chapters","total_units":4,
    })
    source=db.add_source(work,"https://archiveofourown.org/works/554433")
    db.record_source_check(source,observed_units=9,message="=DANGEROUS()")
    outfile=tmp_path/"releases.csv"
    monkeypatch.setattr(
        QFileDialog,"getSaveFileName",
        lambda *_args,**_kwargs:(str(outfile),"CSV")
    )
    win=MainWindow(db)
    try:
        win.navigate(9)
        win.export_serial_releases_csv()
        with outfile.open(encoding="utf-8-sig",newline="") as handle:
            rows=list(csv.DictReader(handle))
        assert rows[0]["Work"]=="'=SUM(A1:A9)"
        assert rows[0]["Note"]=="'=DANGEROUS()"
        assert rows[0]["New chapters"]=="5"
    finally:
        win.close()


def test_serial_worker_respects_stop_request_without_fetching(app):
    from booktracker.gui import SerialCheckThread

    attempted=[]
    urls=[
        {"source_id":str(index),"url":f"https://archiveofourown.org/works/{index}",
         "title":f"Story {index}"}
        for index in range(3)
    ]
    worker=None
    def stop_after_first(url):
        attempted.append(url)
        worker.requestInterruption()
        return {"total_units":10}
    worker=SerialCheckThread(urls,fetch=stop_after_first)
    worker.start()
    assert worker.wait(5000)
    assert len(attempted)==1


def test_source_release_history_survives_main_source_deletion(db):
    _work,source=serial(db,total=5)
    db.record_source_check(source,observed_units=8)
    db.remove_source(source)
    releases=db.list_serial_releases()
    assert len(releases)==1
    assert releases[0]["source_id"] is None
    assert releases[0]["title"]=="Long-running Story"



def test_quiet_stories_require_recent_successful_recheck(db):
    work,source=serial(db,total=20)
    db.follow_work(work)
    db.record_source_check(source,observed_units=25)
    db.db.execute(
        "UPDATE serial_releases SET observed_at=? WHERE work_id=?",
        ((datetime.now(timezone.utc)-timedelta(days=105)).isoformat(),work),
    )
    db.db.execute(
        "UPDATE source_checks SET checked_at=? WHERE source_id=?",
        ((datetime.now(timezone.utc)-timedelta(days=105)).isoformat(),source),
    )
    db.db.commit()
    assert db.source_update_summary()[0]["quiet_days"]==0
    # A recent failed request is not proof of no growth.
    db.record_source_check(source,error="HTTP 403")
    assert db.source_update_summary()[0]["quiet_days"]==0
    # A successful identical count after 105 days supports 'no growth seen'.
    db.record_source_check(source,observed_units=25)
    assert db.source_update_summary()[0]["quiet_days"]>=100
    db.record_source_check(source,observed_units=26)
    assert db.source_update_summary()[0]["quiet_days"]==0
