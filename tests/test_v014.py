"""v0.14 explicit shared-folder sync, cross-device conflict safety and recovery."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from booktracker import sync
from booktracker.database import Library


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    instance=QApplication.instance() or QApplication([])
    assert instance is not None
    return instance


@pytest.fixture
def pair(tmp_path):
    a=Library(tmp_path/"A"/"library.sqlite3")
    b=Library(tmp_path/"B"/"library.sqlite3")
    folder=tmp_path/"Syncthing"/"BookTracker"
    yield a,b,folder
    a.close()
    b.close()


def book(lib,title="The Way of Kings"):
    return lib.add_work({"title":title,"author":"Brandon Sanderson",
                         "progress_unit":"pages","total_units":1000})


def exchange(source,target,folder,choose=()):
    file=sync.publish(source,folder)
    peer=sync.read_snapshot(file)
    review=sync.plan(target,peer)
    return sync.apply(target,review,use_peer=choose)


def test_separate_device_ids_and_not_in_portable_export(pair,tmp_path):
    a,b,folder=pair
    aa=sync.device_info(a)
    bb=sync.device_info(b)
    assert aa["device_id"]!=bb["device_id"]
    assert aa["published_revision"]==0
    assert "sync_device" not in a.export_data()
    assert "sync_peers" not in a.export_data()
    assert a.export_data()["schema_version"]==11
    sync.set_device_name(a,"Gaming laptop")
    assert sync.device_info(a)["display_name"]=="Gaming laptop"
    with pytest.raises(ValueError):
        sync.set_device_name(a,"")
    with pytest.raises(ValueError):
        sync.set_device_name(a,"Bad\nName")
    assert not folder.exists()


def test_publish_and_receive_new_books_and_history(pair):
    a,b,folder=pair
    key=book(a)
    a.record_progress_on(key,245,date="2026-10-23")
    a.add_session(key,read_date="2026-10-23",minutes=55,mode="reading")
    a.add_annotation(key,kind="note",body="Loved this",location=12,location_unit="pages")
    published=sync.publish(a,folder)
    assert published.exists()
    assert published.suffix==".btsync"
    assert ".sqlite3" not in published.name
    peer=sync.read_snapshot(published)
    assert peer.device_id==sync.device_info(a)["device_id"]
    assert peer.revision==1
    peers,errors=sync.available_peers(b,folder)
    assert peers[0].device_id==peer.device_id and not errors
    review=sync.plan(b,peer)
    assert review.new_rows>=4
    assert not review.conflicts
    result=sync.apply(b,review)
    assert result["new_works"]==1
    assert b.get_work(key)["title"]=="The Way of Kings"
    assert len(b.list_sessions(work_id=key))==1
    assert len(b.history(key))==1
    assert b.active_run(key)["current_progress"]==245
    assert len(b.list_annotations(work_id=key))==1
    assert Path(result["backup"]).exists()
    with pytest.raises(ValueError,match="No newer"):
        sync.plan(b,peer)


def test_two_way_additive_and_duplicate_revision_skip(pair):
    a,b,folder=pair
    first=book(a)
    exchange(a,b,folder)
    second=book(b,"Mistborn")
    b.add_session(second,read_date="2026-10-21",minutes=34,mode="listening")
    exchange(b,a,folder)
    assert a.get_work(first)
    assert a.get_work(second)
    assert len(a.list_sessions(work_id=second))==1
    assert b.get_work(first)
    # Peer publishing identical data again must not duplicate anything.
    exchange(b,a,folder)
    assert len(a.list_sessions(work_id=second))==1
    assert len(a.list_works())==2


def test_safe_single_device_edit_updates_existing_row(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)  # Establish peer baseline on both computers.
    # B edits status, A unchanged since its own previous state.
    b.update_work(key,{"status":"reading","notes":"Read chapter 1"})
    exchange(b,a,folder)
    assert a.get_work(key)["status"]=="reading"
    assert a.get_work(key)["notes"]=="Read chapter 1"
    # A updates while B stays put since last shared state.
    a.update_work(key,{"rating":4.5})
    exchange(a,b,folder)
    assert b.get_work(key)["rating"]==4.5


def test_conflict_manual_keep_local_or_peer(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)
    a.update_work(key,{"notes":"My own local notes"})
    b.update_work(key,{"notes":"Other computer notes"})
    path=sync.publish(b,folder)
    review=sync.plan(a,sync.read_snapshot(path))
    notes=[(i,c) for i,c in enumerate(review.conflicts)
           if c.table=="works" and c.field=="notes"]
    assert len(notes)==1
    assert notes[0][1].local=="My own local notes"
    assert notes[0][1].remote=="Other computer notes"
    # A normal merge keeps local and still tracks consumed peer revision.
    sync.apply(a,review)
    assert a.get_work(key)["notes"]=="My own local notes"
    b.update_work(key,{"notes":"A third editorial revision"})
    path=sync.publish(b,folder)
    review=sync.plan(a,sync.read_snapshot(path))
    # B makes a later, nonconcurrent edit. After the previous review,
    # A was unchanged locally, so this change is now unambiguous.
    assert any(c.field=="notes" for c in review.safe)
    sync.apply(a,review)
    assert a.get_work(key)["notes"]=="A third editorial revision"


def test_disjoint_concurrent_fields_auto_merge(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)
    a.update_work(key,{"notes":"Local research"})
    b.update_work(key,{"rating":5.0})
    snapshot=sync.publish(b,folder)
    changes=sync.plan(a,sync.read_snapshot(snapshot))
    assert not [c for c in changes.conflicts
                if c.table=="works" and c.field in ("notes","rating")]
    assert any(c.field=="rating" for c in changes.safe)
    sync.apply(a,changes)
    assert a.get_work(key)["notes"]=="Local research"
    assert a.get_work(key)["rating"]==5.0


def test_apply_cannot_use_peer_without_review_or_after_mutation(pair):
    a,b,folder=pair
    key=book(a)
    path=sync.publish(a,folder)
    peer=sync.read_snapshot(path)
    review=sync.plan(b,peer)
    book(b,"Another book")
    with pytest.raises(ValueError,match="Library changed"):
        sync.apply(b,review)
    assert not b.get_work(key)
    assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==0


def test_peer_delete_does_not_delete_local_book(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)
    a.delete_work(key)
    path=sync.publish(a,folder)
    preview=sync.plan(b,sync.read_snapshot(path))
    assert any(change.kind=="deletion" for change in preview.conflicts)
    sync.apply(b,preview)
    assert b.get_work(key)  # No silent destructive sync.
    assert a.get_work(key) is None


def test_locally_deleted_work_not_resurrected_from_peer_snapshot(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)
    b.delete_work(key)
    a.update_work(key,{"notes":"A still has this"})
    peer=sync.read_snapshot(sync.publish(a,folder))
    preview=sync.plan(b,peer)
    assert preview.skipped_deletions>=1
    assert any(c.field=="(deleted locally)" for c in preview.conflicts)
    sync.apply(b,preview)
    assert b.get_work(key) is None


def test_concurrent_progress_is_additive_and_not_duplicated(pair):
    a,b,folder=pair
    key=book(a)
    a.record_progress_on(key,10,date="2026-10-01")
    exchange(a,b,folder)
    exchange(b,a,folder)
    # On each computer events are distinct IDs, with same reading-run ID.
    a.record_progress_on(key,30,date="2026-10-05")
    b.record_progress_on(key,40,date="2026-10-08")
    peer=sync.read_snapshot(sync.publish(b,folder))
    preview=sync.plan(a,peer)
    assert preview.new_rows>=1
    sync.apply(a,preview)
    assert len(a.history(key))==3
    assert a.active_run(key)["current_progress"]==40
    exchange(a,b,folder)
    assert len(b.history(key))==3
    assert b.active_run(key)["current_progress"]==40


def test_corrupt_archive_and_changed_snapshot_refused(pair):
    a,b,folder=pair
    book(a)
    exported=sync.publish(a,folder)
    prior=sync.read_snapshot(exported)
    review=sync.plan(b,prior)
    # A snapshot modified between preview and apply must be rejected.
    exported.write_bytes(exported.read_bytes()+b"tampered")
    with pytest.raises(ValueError,match="changed after preview"):
        sync.apply(b,review)
    exported.write_bytes(b"not a zip")
    peers,errors=sync.available_peers(b,folder)
    assert not peers
    assert len(errors)==1
    assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==0


def test_publication_revisions_are_monotonic_and_retained(pair):
    a,b,folder=pair
    for number in range(6):
        book(a,f"Novel {number}")
        out=sync.publish(a,folder)
        assert sync.read_snapshot(out).revision==number+1
    assert len(list(folder.glob("*.btsync")))==3
    assert sync.device_info(a)["published_revision"]==6
    peers,_=sync.available_peers(b,folder)
    assert peers[0].revision==6
    review=sync.plan(b,peers[0])
    assert sync.apply(b,review)["new_works"]==6


def test_old_export_does_not_clone_device_id(pair,tmp_path):
    from booktracker.integrations import export_file, import_file
    a,b,_folder=pair
    bookmark=book(a)
    saved=tmp_path/"library.json"
    export_file(a,saved)
    assert import_file(b,saved)==1
    assert b.get_work(bookmark)
    assert sync.device_info(a)["device_id"]!=sync.device_info(b)["device_id"]


def test_reject_same_device_or_duplicate_revision(pair):
    a,b,folder=pair
    path=sync.publish(a,folder)
    with pytest.raises(ValueError,match="itself"):
        sync.plan(a,sync.read_snapshot(path))
    review=sync.plan(b,sync.read_snapshot(path))
    sync.apply(b,review)
    with pytest.raises(ValueError,match="No newer"):
        sync.plan(b,sync.read_snapshot(path))


def test_sync_review_dialog_defaults_to_local(app,pair):

    from booktracker.sync_gui import SyncReview
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)
    a.update_work(key,{"notes":"A"})
    b.update_work(key,{"notes":"B"})
    review=sync.plan(a,sync.read_snapshot(sync.publish(b,folder)))
    dlg=SyncReview(None,review)
    try:
        assert dlg.table.rowCount()>=1
        assert dlg.selections()==[]
        for index,box in dlg.boxes:
            if review.conflicts[index].field=="notes":
                box.setChecked(True)
        assert dlg.selections()
    finally:
        dlg.close()


def test_sync_page_can_publish_and_display_peers(app,pair,tmp_path):

    from booktracker.gui import MainWindow
    a,b,folder=pair
    window=MainWindow(a)
    other=MainWindow(b)
    try:
        window.navigate(13)
        assert window.pages.currentIndex()==13
        assert len(window.nav_buttons)==14
        window.sync_page.folder.setText(str(folder))
        assert window.sync_page.publish()
        other.sync_page.folder.setText(str(folder))
        other.sync_page.scan()
        assert other.sync_page.table.rowCount()==1
        assert other.sync_page.selected_peer().device_id==sync.device_info(a)["device_id"]
    finally:
        other.close()
        window.close()



def test_edited_sessions_notes_and_history_synced_with_single_writer(pair):
    a,b,folder=pair
    key=book(a)
    a.record_progress_on(key,14,date="2026-10-01")
    session_id=a.add_session(key,read_date="2026-10-01",minutes=35,mode="reading")
    quote_id=a.add_annotation(key,kind="note",body="Initial thought")
    exchange(a,b,folder)
    exchange(b,a,folder)
    b.update_session(session_id,read_date="2026-10-02",minutes=60,
                     mode="listening",note="After work")
    b.update_annotation(quote_id,kind="note",body="Revised literary note")
    event=b.history(key)[0]
    b.update_progress_event(event["id"],progress=22)
    review=sync.plan(a,sync.read_snapshot(sync.publish(b,folder)))
    assert not [c for c in review.conflicts
                if c.table in ("reading_sessions","annotations","progress_events")]
    assert any(c.field=="minutes" for c in review.safe)
    assert any(c.field=="body" for c in review.safe)
    assert any(c.field=="new_progress" for c in review.safe)
    sync.apply(a,review)
    assert a.get_session(session_id)["minutes"]==60
    assert a.get_session(session_id)["mode"]=="listening"
    assert a.get_annotation(quote_id)["body"]=="Revised literary note"
    assert a.active_run(key)["current_progress"]==22


def test_concurrent_session_edits_are_conflicts_not_last_writer_wins(pair):
    a,b,folder=pair
    key=book(a)
    session_id=a.add_session(key,read_date="2026-10-01",minutes=35,mode="reading")
    exchange(a,b,folder)
    exchange(b,a,folder)
    a.update_session(session_id,read_date="2026-10-01",minutes=40)
    b.update_session(session_id,read_date="2026-10-01",minutes=55)
    review=sync.plan(a,sync.read_snapshot(sync.publish(b,folder)))
    selections=[i for i,c in enumerate(review.conflicts)
                if c.table=="reading_sessions" and c.field=="minutes"]
    assert len(selections)==1
    sync.apply(a,review)
    assert a.get_session(session_id)["minutes"]==40
    b.update_session(session_id,read_date="2026-10-01",minutes=65)
    next_review=sync.plan(a,sync.read_snapshot(sync.publish(b,folder)))
    assert any(c.field=="minutes" for c in next_review.safe)
    sync.apply(a,next_review)
    assert a.get_session(session_id)["minutes"]==65


def test_deleted_local_annotation_does_not_return(pair):
    a,b,folder=pair
    key=book(a)
    aid=a.add_annotation(key,kind="note",body="Remove later")
    exchange(a,b,folder)
    exchange(b,a,folder)
    b.delete_annotation(aid)
    review=sync.plan(b,sync.read_snapshot(sync.publish(a,folder)))
    assert any(c.table=="annotations" and c.kind=="deletion" for c in review.conflicts)
    sync.apply(b,review)
    assert b.get_annotation(aid) is None


def test_embedded_imported_cover_is_transferred(pair,tmp_path):
    import base64
    import hashlib

    a,b,folder=pair
    key=book(a)
    png=base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        "DUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
    )
    sha=hashlib.sha256(png).hexdigest()
    token=f"booktracker-cover:{sha}.png"
    covers=a.path.parent/"imported_covers"
    covers.mkdir(parents=True)
    (covers/(sha+".png")).write_bytes(png)
    a.update_work(key,{"cover_url":token})
    exchange(a,b,folder)
    assert b.get_work(key)["cover_url"]==token
    assert (b.path.parent/"imported_covers"/(sha+".png")).read_bytes()==png


def test_local_cover_deletion_exclusion_does_not_corrupt_import(pair,tmp_path):
    import base64
    import hashlib

    a,b,folder=pair
    key=book(a)
    png=base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        "DUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
    )
    sha=hashlib.sha256(png).hexdigest()
    token=f"booktracker-cover:{sha}.png"
    covers=a.path.parent/"imported_covers"
    covers.mkdir(parents=True)
    (covers/(sha+".png")).write_bytes(png)
    a.update_work(key,{"cover_url":token})
    exchange(a,b,folder)
    exchange(b,a,folder)
    b.delete_work(key)
    a.update_work(key,{"notes":"Still on A"})
    peer=sync.read_snapshot(sync.publish(a,folder))
    review=sync.plan(b,peer)
    assert review.skipped_deletions
    sync.apply(b,review)
    assert b.get_work(key) is None


def test_reject_apply_with_unreviewed_conflict_selection(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    exchange(b,a,folder)
    a.delete_work(key)
    peer=sync.read_snapshot(sync.publish(a,folder))
    review=sync.plan(b,peer)
    non_automatic=[i for i,c in enumerate(review.conflicts)
                   if c.kind=="deletion"]
    assert non_automatic
    with pytest.raises(ValueError,match="cannot be auto-applied"):
        sync.apply(b,review,use_peer=non_automatic)
    assert b.get_work(key)


def test_read_snapshot_rejects_forged_envelope(pair):
    a,_b,folder=pair
    target=sync.publish(a,folder)
    with pytest.warns(UserWarning,match="Duplicate name"), zipfile.ZipFile(target,"a") as z:
        z.writestr("sync.json",b'{"protocol":"booktracker-sync"}')
    with pytest.raises(ValueError,match="duplicate manifests"):
        sync.read_snapshot(target)


def test_incomplete_transfer_is_invisible_to_scanner(pair):
    _a,b,folder=pair
    folder.mkdir(parents=True)
    (folder/"booktracker-aaa-000000001.btsync.tmp").write_bytes(b"partial")
    peers,errors=sync.available_peers(b,folder)
    assert peers==[] and errors==[]


def test_reverted_import_prevents_duplicate_snapshot_processing(pair):
    a,b,folder=pair
    key=book(a)
    peer=sync.read_snapshot(sync.publish(a,folder))
    review=sync.plan(b,peer)
    # Library callback failure must not mark a peer revision applied.
    with pytest.raises(ValueError):
        sync.apply(b,review,use_peer=[100000])
    assert b.get_work(key) is None
    assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==0



def test_publishing_in_a_folder_containing_live_database_is_refused(pair):
    a,_b,_folder=pair
    with pytest.raises(ValueError,match="contains your live SQLite"):
        sync.publish(a,a.path.parent)
    assert sync.device_info(a)["published_revision"]==0
    assert a.path.is_file()


def test_clone_recovery_new_device_identity_preserves_books(pair,tmp_path):
    a,b,folder=pair
    key=book(a)
    existing=sync.device_info(a)["device_id"]
    # Mimic a cloned SQLite sync state; the recovery action should not touch
    # reading data or modify the original computer's ID.
    fresh=sync.new_device_identity(a)
    assert fresh!=existing
    assert sync.device_info(a)["published_revision"]==0
    assert a.get_work(key)["title"]=="The Way of Kings"
    assert b.get_work(key) is None
    assert sync.publish(a,folder).exists()


def test_identity_reset_clears_sync_cursor_but_not_progress(pair):
    a,b,folder=pair
    key=book(a)
    a.record_progress_on(key,55,date="2026-10-10")
    exchange(a,b,folder)
    assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==1
    sync.new_device_identity(b)
    assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==0
    assert b.active_run(key)["current_progress"]==55
    assert len(b.history(key))==1


def test_representative_sync_data_does_not_export_media_paths(pair,tmp_path):
    a,b,folder=pair
    key=book(a)
    file=tmp_path/"locally-owned.m4b"
    file.write_bytes(b"large audiobook is separate")
    a.add_local_file(key,file)
    peer=sync.read_snapshot(sync.publish(a,folder))
    assert all(row["path"]=="" for row in peer.data["local_files"])
    with zipfile.ZipFile(peer.path) as z:
        assert b"large audiobook is separate" not in z.read("library.json")
        assert str(file).encode() not in z.read("library.json")
    sync.apply(b,sync.plan(b,peer))
    assert b.list_local_files(key)[0]["path"]==""
    assert not b.list_local_files(key)[0]["available"]



def test_replacing_library_clears_old_peer_baselines(pair):
    a,b,folder=pair
    key=book(a)
    exchange(a,b,folder)
    before=sync.device_info(b)["device_id"]
    assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==1
    fresh=Library(b.path.parent/"replacement.sqlite3")
    try:
        book(fresh,"New catalogue")
        b.import_data(fresh.export_data(),strategy="replace")
        assert b.get_work(key) is None
        assert len(b.list_works())==1
        assert b.db.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0]==0
        assert sync.device_info(b)["device_id"]==before
    finally:
        fresh.close()



def test_different_book_ids_same_title_backfill_missing_public_metadata(pair):
    a,b,folder=pair
    first=book(a)
    second=book(b)
    assert first!=second
    a.update_work(first,{"description":"A detailed fantasy novel"})
    assert b.get_work(second)["description"]==""
    peer=sync.read_snapshot(sync.publish(a,folder))
    review=sync.plan(b,peer)
    assert any(change.kind=="duplicate" for change in review.conflicts)
    sync.apply(b,review)
    # Never merge different UUIDs on just a common title and author.
    assert len(b.list_works())==2
    assert b.get_work(second)["description"]==""
    assert b.get_work(first)["description"]=="A detailed fantasy novel"
