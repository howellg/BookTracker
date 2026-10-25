"""BookTracker v0.15 EPUB reader: offline security, navigation, positions and backups."""
from __future__ import annotations

import os
import sqlite3
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from booktracker.database import Library
from booktracker.epub_reader import EpubBook, member_path
from booktracker.integrations import export_file, import_file


def make_epub(path, sections=None, *, images=None, extra=None):
    sections = sections or [
        "<html><body><h1>First Chapter</h1><p>Once upon a time <b>hello</b>.</p></body></html>",
        "<html><body><h2>The Second Chapter</h2><p>Next section.</p></body></html>",
        "<html><body><h1>Conclusion</h1><p>End of the story.</p></body></html>",
    ]
    item_definitions="\n".join(
        f'<item id="part{i}" href="chapters/{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(sections))
    )
    refs="\n".join(f'<itemref idref="part{i}"/>' for i in range(len(sections)))
    if images:
        item_definitions += "\n"+"\n".join(
            f'<item id="pic{i}" href="images/pic{i}.png" media-type="image/png"/>'
            for i in range(len(images))
        )
    opf=f'''<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Local EPUB Test</dc:title></metadata>
    <manifest>{item_definitions}</manifest><spine>{refs}</spine></package>'''
    container='''<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
    version="1.0"><rootfiles><rootfile full-path="OPS/book.opf"
    media-type="application/oebps-package+xml"/></rootfiles></container>'''
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype","application/epub+zip")
        z.writestr("META-INF/container.xml",container)
        z.writestr("OPS/book.opf",opf)
        for index,html in enumerate(sections):
            z.writestr(f"OPS/chapters/{index}.xhtml",html)
        for index,data in enumerate(images or []):
            z.writestr(f"OPS/images/pic{index}.png",data)
        for filename,data in (extra or {}).items():
            z.writestr(filename,data)
    return path


@pytest.fixture
def db(tmp_path):
    library=Library(tmp_path/"booktracker.sqlite3")
    yield library
    library.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    instance=QApplication.instance() or QApplication([])
    return instance


def with_book(db,path):
    work_id=db.add_work({
        "title":"Local EPUB Test","author":"Writer",
        "progress_unit":"pages","status":"plan_to_read"
    })
    file_id=db.add_local_file(work_id,path)
    return work_id,file_id


def test_epub_spine_chapters_and_html_sanitizing(tmp_path):
    book=EpubBook(make_epub(tmp_path/"test.epub"))
    assert len(book)==3
    first=book.section(0)
    assert first["title"]=="First Chapter"
    assert "<b>hello</b>" in first["html"]
    assert "Once upon" in first["text"]
    assert book.spine[1].href=="OPS/chapters/1.xhtml"
    assert book.section(2)["title"]=="Conclusion"
    with pytest.raises(IndexError):
        book.section(3)


@pytest.mark.parametrize("uri",[
    "https://example.com/track.gif","//example.com/image.png",
    "../../../secrets.txt","/etc/passwd","..%2f..%2f..%2fsecret",
    "file:///etc/hosts","javascript:alert(1)","\\evil.png",
])
def test_reader_rejects_external_or_unsafe_paths(uri):
    with pytest.raises(ValueError):
        member_path("OPS/chapters/0.xhtml",uri)


def test_bad_html_omits_scripts_remote_images_css_and_links(tmp_path):
    html="""<html><head><script>window.location='https://evil.test/'</script>
    <style>body{background:url(https://tracker.test)}</style></head><body>
    <h1>Chapter 1</h1>
    <p onclick="alert(1)" style="background:url(file:///tmp/key)">Harmless text</p>
    <iframe src="https://evil.test"></iframe>
    <img src="https://tracker.test/tracker.png"/>
    <a href="javascript:alert(1)" onmouseover="danger()">Ignore link action</a>
    <div><object data="file:///etc/passwd">discard</object></div>
    </body></html>"""
    book=EpubBook(make_epub(tmp_path/"untrusted.epub",[html]))
    output=book.section(0)
    assert "Harmless text" in output["html"]
    for malicious in (
        "window.location","javascript:","tracker.test","file:///","onclick",
        "onmouseover","<iframe","<object","<style","<script"
    ):
        assert malicious not in output["html"]
    assert "Ignore link action" in output["html"]
    assert not output["images"]


def test_embedded_image_allowlist_and_size_cap(tmp_path):
    import base64
    png=base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        "DUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
    )
    book=EpubBook(make_epub(
        tmp_path/"pictures.epub",[
            ('<html><body><h1>Illustration</h1>'
            '<img src="../images/pic0.png" alt="Castle"/>'
            '</body></html>')
        ],images=[png]
    ))
    section=book.section(0)
    assert len(section["images"])==1
    internal=next(iter(section["images"].values()))
    assert internal=="OPS/images/pic0.png"
    assert book.image(internal)==png
    with pytest.raises(ValueError):
        book.image("META-INF/container.xml")


def test_position_persists_without_creating_progress_events(db,tmp_path):
    path=make_epub(tmp_path/"positions.epub")
    work,file_id=with_book(db,path)
    assert db.epub_position(file_id) is None
    db.save_epub_position(file_id,2,"OPS/chapters/2.xhtml",6742)
    position=db.epub_position(file_id)
    assert position["section_index"]==2
    assert position["scroll_ratio"]==6742
    assert db.get_work(work)["status"]=="plan_to_read"
    assert db.history(work)==[]
    assert db.active_run(work) is None
    db.save_epub_position(file_id,1,"OPS/chapters/1.xhtml",0)
    assert db.epub_position(file_id)["section_index"]==1


def test_bookmarks_validate_delete_and_cleanup(db,tmp_path):
    work,file_id=with_book(db,make_epub(tmp_path/"marks.epub"))
    first=db.add_epub_bookmark(file_id,0,"OPS/chapters/0.xhtml",1000,"Opening")
    second=db.add_epub_bookmark(file_id,2,"OPS/chapters/2.xhtml",8000,"Ending")
    assert [r["id"] for r in db.list_epub_bookmarks(file_id)]==[first,second]
    for kwargs in (
        (-1,"OPS/chapters/0.xhtml",200,"x"),
        (5,"OPS/chapters/0.xhtml",10001,"x"),
        (0,"../../secret",1,"x"),
        (0,"a",1,""),
    ):
        with pytest.raises(ValueError):
            db.add_epub_bookmark(file_id,*kwargs)
    assert db.remove_epub_bookmark(first)
    assert not db.remove_epub_bookmark(first)
    db.save_epub_position(file_id,1,"OPS/chapters/1.xhtml",20)
    db.remove_local_file(file_id)
    assert db.db.execute("SELECT COUNT(*) FROM epub_positions").fetchone()[0]==0
    assert db.db.execute("SELECT COUNT(*) FROM epub_bookmarks").fetchone()[0]==0
    assert db.get_work(work)


@pytest.mark.parametrize("extension",[".json",".xml",".btbackup"])
def test_epub_locations_portable_without_epub_bytes(db,tmp_path,extension):
    path=make_epub(tmp_path/"portable.epub")
    _work,linked=with_book(db,path)
    db.save_epub_position(linked,2,"OPS/chapters/2.xhtml",9866)
    bookmark=db.add_epub_bookmark(linked,1,"OPS/chapters/1.xhtml",5555,"Middle")
    export=tmp_path/("library"+extension)
    export_file(db,export)
    assert str(path).encode() not in export.read_bytes()
    assert b"Once upon a time" not in export.read_bytes()
    destination=Library(tmp_path/("new"+extension+".db"))
    try:
        assert import_file(destination,export)==1
        position=destination.epub_position(linked)
        assert position["section_index"]==2 and position["scroll_ratio"]==9866
        bookmarks=destination.list_epub_bookmarks(linked)
        assert len(bookmarks)==1 and bookmarks[0]["id"]==bookmark
        assert destination.get_local_file(linked)["path"]==""
        assert import_file(destination,export)==0
        assert len(destination.list_epub_bookmarks(linked))==1
        assert import_file(destination,export,strategy="replace")==1
        assert destination.epub_position(linked)["scroll_ratio"]==9866
    finally:
        destination.close()


def test_legacy_v10_import_retains_reader_defaults(db,tmp_path):
    work,file_id=with_book(db,make_epub(tmp_path/"legacy.epub"))
    data=db.export_data()
    data["schema_version"]=10
    data.pop("epub_positions")
    data.pop("epub_bookmarks")
    target=Library(tmp_path/"legacy-copy.db")
    try:
        assert target.import_data(data)==1
        assert target.get_work(work)
        assert not target.epub_position(file_id)
    finally:
        target.close()


def test_schema_v10_upgrade_snapshot(db,tmp_path):
    path=tmp_path/"previous.db"
    before=Library(path)
    work=before.add_work({"title":"Existing Work","notes":"Keep these notes"})
    before.close()
    with sqlite3.connect(path) as old:
        old.execute("DROP TABLE epub_positions")
        old.execute("DROP TABLE epub_bookmarks")
        old.execute("PRAGMA user_version=10")
    upgraded=Library(path)
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0]==11
        assert upgraded.get_work(work)["notes"]=="Keep these notes"
        snapshots=list((tmp_path/"backups").glob("BookTracker-before-v015-*.sqlite3"))
        assert len(snapshots)==1
        with sqlite3.connect(snapshots[0]) as snap:
            assert snap.execute("PRAGMA user_version").fetchone()[0]==10
    finally:
        upgraded.close()


def test_reader_gui_navigation_resume_search_theme_notes(app,db,tmp_path,monkeypatch):
    from PySide6.QtCore import QSettings

    from booktracker.reader_gui import EpubReader

    work,file_id=with_book(db,make_epub(tmp_path/"gui.epub"))
    settings=QSettings(str(tmp_path/"settings.ini"),QSettings.Format.IniFormat)
    reader=EpubReader(None,db,file_id,settings=settings)
    try:
        assert reader.index==0
        assert reader.counter.text().startswith("Spine section 1 / 3")
        reader.step(1)
        app.processEvents()
        assert reader.index==1
        assert "Second Chapter" in reader.viewer.toPlainText()
        reader.save_position()
        assert db.epub_position(file_id)["section_index"]==1
        assert db.active_run(work) is None
        reader.search.setText("Next section")
        reader.find_next()
        assert reader.viewer.textCursor().selectedText()=="Next section"
        reader.zoom(3)
        reader.theme.setCurrentText("Sepia")
        assert settings.value("reader_theme")=="Sepia"
        reader.close()
        restored=EpubReader(None,db,file_id,settings=settings)
        try:
            assert restored.index==1
            assert restored.theme.currentText()=="Sepia"
        finally:
            restored.close()
    finally:
        reader.close()


def test_gui_bookmark_and_manual_progress_without_guessing(app,db,tmp_path,monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from booktracker.reader_gui import EpubReader

    work,file_id=with_book(db,make_epub(tmp_path/"reader-gui.epub"))
    reader=EpubReader(
        None,db,file_id,
        settings=QSettings(str(tmp_path/"settings.ini"),QSettings.Format.IniFormat)
    )
    monkeypatch.setattr(
        QInputDialog,"getText",
        lambda *_a,**_k:("A favorite moment",True)
    )
    monkeypatch.setattr(
        QMessageBox,"question",
        lambda *_a,**_k:QMessageBox.StandardButton.Yes
    )
    try:
        reader.step(1)
        reader.add_bookmark()
        assert len(db.list_epub_bookmarks(file_id))==1
        assert reader.marks.count()==1
        reader.step(1)
        reader.goto_bookmark(reader.marks.item(0))
        assert reader.index==1
        reader.marks.setCurrentRow(0)
        reader.remove_bookmark()
        assert reader.marks.count()==0
        assert db.history(work)==[]
        monkeypatch.setattr(
            QInputDialog,"getInt",lambda *_a,**_k:(120,True)
        )
        reader.log_progress()
        assert db.active_run(work)["current_progress"]==120
        assert len(db.history(work))==1
    finally:
        reader.close()


def test_gui_book_selector_uses_linked_epub(app,db,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QDialog

    from booktracker.gui import MainWindow
    from booktracker.reader_gui import EpubReader

    work,file_id=with_book(db,make_epub(tmp_path/"main.epub"))
    calls=[]
    monkeypatch.setattr(
        EpubReader,"exec",lambda self:calls.append(self.file_id) or QDialog.DialogCode.Accepted
    )
    main=MainWindow(db)
    try:
        main.refresh_library(select_id=work)
        main.read_selected_epub()
        assert calls==[file_id]
        assert db.history(work)==[]
        assert db.get_work(work)["status"]=="plan_to_read"
    finally:
        main.close()



def test_epub3_navigation_titles_are_used_in_sidebar(tmp_path):
    path=tmp_path/"epub3-nav.epub"
    make_epub(path,extra={
        "OPS/toc.xhtml":'<html><body><nav epub:type="toc">'
        '<ol><li><a href="chapters/0.xhtml">Arrival</a></li>'
        '<li><a href="chapters/1.xhtml#top">The Plot Thickens</a></li>'
        '<li><a href="https://external.test/">Do not visit</a></li>'
        '</ol></nav></body></html>'
    })
    # Amend a small synthetic OPF to advertise the EPUB3 nav.
    with zipfile.ZipFile(path) as z:
        members={entry.filename:z.read(entry) for entry in z.infolist()}
    members["OPS/book.opf"]=members["OPS/book.opf"].replace(
        b"</manifest>",
        b'<item id="toc" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest>'
    )
    with zipfile.ZipFile(path,"w") as z:
        for filename,data in members.items():
            z.writestr(filename,data)
    book=EpubBook(path)
    assert book.toc()[0]=="Arrival"
    assert book.toc()[1]=="The Plot Thickens"
    assert book.toc()[2]=="Section 3"


def test_epub2_ncx_navigation_titles_are_used(tmp_path):
    path=tmp_path/"epub2-ncx.epub"
    make_epub(path,extra={
        "OPS/toc.ncx":'''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
        <navMap><navPoint id="a"><navLabel><text>Prologue</text></navLabel>
        <content src="chapters/0.xhtml"/></navPoint>
        <navPoint id="b"><navLabel><text>Part Two</text></navLabel>
        <content src="chapters/1.xhtml#part"/></navPoint>
        </navMap></ncx>'''
    })
    with zipfile.ZipFile(path) as z:
        members={entry.filename:z.read(entry) for entry in z.infolist()}
    members["OPS/book.opf"]=members["OPS/book.opf"].replace(
        b"</manifest>",
        b'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
    ).replace(b"<spine>",b'<spine toc="ncx">')
    with zipfile.ZipFile(path,"w") as z:
        for filename,data in members.items():
            z.writestr(filename,data)
    book=EpubBook(path)
    assert book.toc()[:2]==["Prologue","Part Two"]


def test_syncthing_epub_bookmarks_and_saved_position_two_devices(tmp_path):
    from booktracker import sync

    shared=tmp_path/"shared"
    first=Library(tmp_path/"computer-A"/"library.sqlite3")
    other=Library(tmp_path/"computer-B"/"library.sqlite3")
    try:
        path=make_epub(tmp_path/"synced.epub")
        work,file_id=with_book(first,path)
        first.save_epub_position(file_id,1,"OPS/chapters/1.xhtml",4500)
        mark=first.add_epub_bookmark(
            file_id,1,"OPS/chapters/1.xhtml",4500,"Bookmark A"
        )
        peer=sync.read_snapshot(sync.publish(first,shared))
        preview=sync.plan(other,peer)
        sync.apply(other,preview)
        assert other.epub_position(file_id)["scroll_ratio"]==4500
        assert other.list_epub_bookmarks(file_id)[0]["id"]==mark
        assert not other.get_local_file(file_id)["path"]
        # Establish a baseline in both directions.
        # A newly inserted bookmark syncs, and the existing one isn't duplicated.
        another=other.add_epub_bookmark(
            file_id,2,"OPS/chapters/2.xhtml",8000,"Bookmark B"
        )
        other.save_epub_position(file_id,2,"OPS/chapters/2.xhtml",8000)
        review=sync.plan(first,sync.read_snapshot(sync.publish(other,shared)))
        sync.apply(first,review)
        assert len(first.list_epub_bookmarks(file_id))==2
        assert {r["id"] for r in first.list_epub_bookmarks(file_id)}=={mark,another}
        assert first.epub_position(file_id)["section_index"]==1  # conflict kept local
        assert first.get_work(work)["status"]=="plan_to_read"
    finally:
        first.close()
        other.close()


def test_v11_reader_import_rejects_orphan_atomically(db,tmp_path):
    linked=make_epub(tmp_path/"backup-source.epub")
    work,file_id=with_book(db,linked)
    db.save_epub_position(file_id,1,"OPS/chapters/1.xhtml",900)
    export=db.export_data()
    export["epub_bookmarks"]=[{
        "id":"bogus","file_id":"unknown","work_id":work,
        "section_index":2,"section_href":"OPS/chapters/2.xhtml",
        "scroll_ratio":500,"label":"Bad"
    }]
    with pytest.raises(ValueError,match="Orphaned EPUB bookmark"):
        db.import_data(export,strategy="replace")
    assert db.epub_position(file_id)["section_index"]==1
    assert db.get_work(work)


def test_open_reader_does_not_change_original_epub_hash(db,tmp_path):
    import hashlib

    from PySide6.QtCore import QSettings

    from booktracker.reader_gui import EpubReader

    file=make_epub(tmp_path/"unchanged.epub")
    before=hashlib.sha256(file.read_bytes()).hexdigest()
    work,file_id=with_book(db,file)
    reader=EpubReader(None,db,file_id,settings=QSettings(
        str(tmp_path/"settings.ini"),QSettings.Format.IniFormat
    ))
    try:
        reader.step(1)
        reader.zoom(2)
        reader.save_position()
    finally:
        reader.close()
    assert hashlib.sha256(file.read_bytes()).hexdigest()==before
    assert db.get_work(work)["status"]=="plan_to_read"



def test_embedded_image_is_bound_to_qtext_document(app,db,tmp_path):

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSettings, QUrl
    from PySide6.QtGui import QImage, QTextDocument

    from booktracker.reader_gui import EpubReader
    source=QImage(24,24,QImage.Format.Format_ARGB32)
    source.fill(0xff4477aa)
    payload=QByteArray()
    output=QBuffer(payload)
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert source.save(output,"PNG")
    output.close()
    png=bytes(payload)
    path=make_epub(
        tmp_path/"illustrated.epub",
        ['<html><body><h1>Picture</h1><p><img src="../images/pic0.png"/></p></body></html>'],
        images=[png]
    )
    work,file_id=with_book(db,path)
    reader=EpubReader(None,db,file_id,settings=QSettings(
        str(tmp_path/"setting.ini"),QSettings.Format.IniFormat
    ))
    try:
        resource=reader.viewer.document().resource(
            QTextDocument.ResourceType.ImageResource,
            QUrl("epub-image:///0/0")
        )
        assert isinstance(resource,QImage) and not resource.isNull()
        assert db.history(work)==[]
    finally:
        reader.close()
