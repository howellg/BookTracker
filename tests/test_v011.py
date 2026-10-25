"""BookTracker v0.11: local Kindle highlights, stopwatch and reading habits."""
from __future__ import annotations

import os

import pytest

from booktracker.clippings import group_clippings, read_clippings
from booktracker.database import Library
from booktracker.integrations import export_file, import_file

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")


@pytest.fixture
def library(tmp_path):
    db=Library(tmp_path/"habits.sqlite3")
    yield db
    db.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    current=QApplication.instance() or QApplication([])
    assert current is not None
    return current


def book(db,title="The Way of Kings",author="Brandon Sanderson"):
    return db.add_work({"title":title,"author":author,"progress_unit":"pages"})


def clip(title,author,kind,text,detail="on page 40 | Location 1050-1051"):
    return (
        f"{title} ({author})\n"
        f"- Your {kind} {detail} | Added on Monday, October 20, 2025 5:20:30 PM\n"
        f"\n{text}\n==========\n"
    )


def test_clippings_parser_multiline_page_and_kindle_locations(tmp_path):
    path=tmp_path/"My Clippings.txt"
    path.write_text(
        clip("The Way of Kings","Brandon Sanderson","Highlight",
             "Life before death.\nStrength before weakness.")
        +clip("The Way of Kings","Brandon Sanderson","Note",
              "A comment", "at location 8022")
        +clip("Another Book","Somebody Else","Highlight",
              "Important quote", "on page 17 | Location 900")
        +("Fake Book (Writer)\n- Your Bookmark on page 17\n\n==========\n"),
        encoding="utf-8-sig"
    )
    parsed=read_clippings(path)
    assert parsed["skipped"]==1
    assert len(parsed["records"])==3
    assert parsed["records"][0]["kind"]=="quote"
    assert parsed["records"][0]["location"]==40
    assert parsed["records"][0]["location_unit"]=="pages"
    assert parsed["records"][0]["body"].count("\n")==1
    assert parsed["records"][1]["kind"]=="note"
    assert parsed["records"][1]["location"]==8022
    assert parsed["records"][1]["location_unit"]=="kindle_location"
    assert len(group_clippings(parsed["records"]))==2


def test_clippings_support_utf16_and_cp1252(tmp_path):
    sample=clip("L’étranger","Albert Camus","Highlight","C’est la vie.")
    for encoding in ("utf-16","cp1252"):
        path=tmp_path/("book-"+encoding+".txt")
        path.write_bytes(sample.encode(encoding))
        assert read_clippings(path)["records"][0]["title"]=="L’étranger"


def test_clippings_preserve_title_parentheses(tmp_path):
    raw=clip("The Way of Kings (Stormlight Book 1)","Brandon Sanderson",
             "Highlight","Words of Radiance", "at location 200")
    path=tmp_path/"clippings.txt"
    path.write_text(raw)
    record=read_clippings(path)["records"][0]
    assert record["title"]=="The Way of Kings (Stormlight Book 1)"
    assert record["author"]=="Brandon Sanderson"
    assert record["location"]==200


@pytest.mark.parametrize("invalid",[
    "not a clipping", "A Book (A)\n- Your Bookmark on page 23\n\n==========",
    "A Book (A)\n- Your Highlight on page 23\n\n==========",
])
def test_invalid_clippings_raise_helpful_error(tmp_path,invalid):
    path=tmp_path/"My Clippings.txt"
    path.write_text(invalid)
    with pytest.raises(ValueError,match="No readable"):
        read_clippings(path)


def test_clippings_rejects_non_txt_and_large_input(tmp_path,monkeypatch):
    path=tmp_path/"bad.html"
    path.write_text("<h1>no clippings</h1>")
    with pytest.raises(ValueError,match="My Clippings"):
        read_clippings(path)
    path=tmp_path/"too-large.txt"
    path.write_text("a" * 50)
    monkeypatch.setattr("booktracker.clippings.MAX_CLIPPINGS_BYTES",20)
    with pytest.raises(ValueError,match="20 MB"):
        read_clippings(path)


def test_import_clippings_create_match_skip_and_dedup(library,tmp_path):
    first=book(library)
    library.record_progress_on(first,123,date="2025-07-12")
    original=library.get_work(first)
    data=(
        clip("The Way of Kings","Brandon Sanderson","Highlight","Honor is dead.")
        +clip("Not in Library","Unknown Author","Highlight","A new book.", "at location 31")
        +clip("Skipped Fiction","Other Author","Note","Ignore this book.")
    )
    path=tmp_path/"My Clippings.txt"
    path.write_text(data)
    records=read_clippings(path)["records"]
    choices={
        ("the way of kings","brandon sanderson"):first,
        ("not in library","unknown author"):"create",
    }
    result=library.import_clippings(records,choices)
    assert result=={"imported":2,"duplicates":0,"skipped":1,"created":1}
    assert len(library.list_works())==2
    assert library.get_work(first)["status"]==original["status"]
    assert library.active_run(first)["current_progress"]==123
    assert len(library.history(first))==1
    assert library.list_annotations(work_id=first)[0]["body"]=="Honor is dead."
    again=library.import_clippings(records,choices)
    # Using "create" should not invent a second distinct work on reimport.
    assert again["imported"]==0
    assert again["duplicates"]==2
    assert again["skipped"]==1
    assert again["created"]==0
    assert len(library.list_works())==2


def test_import_clippings_does_not_guess_missing_book(library):
    record={
        "title":"A Flower for the Soul", "author":"TheBlack'sResurgence",
        "kind":"quote","body":"Memorable passage",
        "location":14,"location_unit":"kindle_location"
    }
    response=library.import_clippings([record],{})
    assert response["skipped"]==1
    assert library.list_works()==[]


def test_import_clippings_rolls_back_all_on_bad_annotation(library):
    target=book(library)
    records=[
        {"title":"The Way of Kings","author":"Brandon Sanderson",
         "kind":"quote","body":"Good","location":3,"location_unit":"pages"},
        {"title":"The Way of Kings","author":"Brandon Sanderson",
         "kind":"quote","body":"","location":4,"location_unit":"pages"},
    ]
    with pytest.raises(ValueError):
        library.import_clippings(
            records,{("the way of kings","brandon sanderson"):target}
        )
    assert library.list_annotations()==[]


@pytest.mark.parametrize("ext",[".json",".xml",".btbackup"])
def test_clippings_annotations_portable_roundtrip(library,tmp_path,ext):
    target=book(library)
    record={
        "title":"The Way of Kings","author":"Brandon Sanderson",
        "kind":"quote","body":"A Kindle highlight",
        "location":7702,"location_unit":"kindle_location"
    }
    library.import_clippings(
        [record],{("the way of kings","brandon sanderson"):target}
    )
    output=tmp_path/("transfer"+ext)
    export_file(library,output)
    other=Library(tmp_path/("restore"+ext+".sqlite3"))
    try:
        assert import_file(other,output)==1
        notes=other.list_annotations(work_id=target)
        assert len(notes)==1
        assert notes[0]["location"]==7702
        assert notes[0]["location_unit"]=="kindle_location"
        assert import_file(other,output)==0
        assert len(other.list_annotations())==1
    finally:
        other.close()


def test_habits_includes_only_actual_dated_activity(library):
    target=book(library)
    library.record_progress_on(target,10,date="2026-10-05")
    library.record_progress_on(target,30,date="2026-10-06")
    library.add_session(target,read_date="2026-10-06",minutes=45,mode="reading")
    library.add_session(target,read_date="2026-10-08",minutes=120,mode="listening")
    library.add_session(target,read_date="2026-09-01",minutes=80,mode="reading")
    result=library.reading_habits(days=7,today="2026-10-10")
    assert result["start"]=="2026-10-04"
    assert result["end"]=="2026-10-10"
    assert result["active_days"]==3
    assert result["reading_minutes"]==45
    assert result["listening_minutes"]==120
    assert result["minutes"]==165
    assert result["chapters"]==0
    assert result["pages"]==30
    assert result["sessions"]==2
    assert result["milestones"]==2
    assert result["longest_streak"]==2
    assert result["top_books"][0]["work_id"]==target
    assert result["top_books"][0]["listening_minutes"]==120
    assert sum(w["active_days"] for w in result["weeks"])==3
    assert sum(d["minutes"] for d in result["weekdays"])==165


def test_habits_cross_year_and_no_inferred_minutes(library):
    target=book(library)
    library.record_progress_on(target,22,date="2025-12-31")
    library.add_session(target,read_date="2026-01-01",minutes=30,mode="reading")
    library.add_session(target,read_date="2026-01-03",minutes=15,mode="listening")
    habit=library.reading_habits(days=5,today="2026-01-03")
    assert habit["active_days"]==3
    assert habit["minutes"]==45
    assert habit["pages"]==22
    assert len(habit["weeks"])==1
    assert habit["longest_streak"]==2


@pytest.mark.parametrize("bad",[0,-2,3651])
def test_habits_rejects_invalid_period(library,bad):
    with pytest.raises(ValueError,match="interval"):
        library.reading_habits(days=bad)


def test_habits_known_completion_only(library):
    a=book(library,"Known")
    library.add_work({"title":"Unknown","status":"completed"})
    library.set_status(a,"completed",date="2026-08-01")
    info=library.reading_habits(days=31,today="2026-08-31")
    assert info["completions"]==1


def test_kindle_preview_requires_exact_unambiguous_match(app,library):
    from booktracker.gui import ClippingsPreviewDialog
    first=book(library)
    book(library,"The Way of Kings","Someone Else")
    records=[
        {"title":"The Way of Kings","author":"Brandon Sanderson",
         "kind":"quote","body":"Hi","location":1,"location_unit":"pages"},
        {"title":"Unknown Book","author":"Mystery",
         "kind":"quote","body":"Hi","location":1,"location_unit":"pages"},
    ]
    dialog=ClippingsPreviewDialog(None,library,{"records":records,"skipped":0})
    try:
        assert dialog.choices[0].currentData()==first
        assert dialog.choices[1].currentData() is None
        assert len(dialog.decisions())==2
        assert dialog.choices[0].findData("create")>=0
    finally:
        dialog.close()


def test_kindle_location_unit_editable_in_note_dialog(app,library):
    from booktracker.gui import AnnotationDialog
    key=book(library)
    original=library.add_annotation(
        key,kind="quote",body="A passage",location=3000,
        location_unit="kindle_location"
    )
    dialog=AnnotationDialog(None,library,existing=library.get_annotation(original))
    try:
        assert dialog.unit.currentData()=="kindle_location"
        assert dialog.location.value()==3000
    finally:
        dialog.close()


def test_habits_gui_and_stopwatch_controls(app,library,monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.gui import MainWindow, ReadingTimerDialog

    key=book(library)
    win=MainWindow(library)
    try:
        assert len(win.nav_buttons)==14
        win.navigate(11)
        assert win.pages.currentIndex()==11
        assert win.habits_cards["active_days"].text()=="0"
        win.habits_period.setCurrentIndex(0)
        assert win._habits_data["days"]==7
        timer=ReadingTimerDialog(win,library,work_id=key)
        try:
            timer.toggle_timer()
            assert timer._running
            timer.toggle_timer()
            assert not timer._running
            timer._accumulated_ms=125_000
            assert timer.values()["minutes"]==2
            timer.save_timer()
            assert timer.result()==timer.DialogCode.Accepted
            assert not timer._ticker.isActive()
        finally:
            timer.close()
        monkeypatch.setattr(
            QMessageBox,"question",
            lambda *_args,**_kwargs:QMessageBox.StandardButton.Yes
        )
        timer2=ReadingTimerDialog(win,library,work_id=key)
        try:
            timer2._accumulated_ms=61_000
            timer2.reject()
            assert timer2.result()==timer2.DialogCode.Rejected
            assert library.list_sessions()==[]
        finally:
            timer2.close()
    finally:
        win.close()


def test_habits_gui_export_daily_csv(app,library,tmp_path,monkeypatch):
    import csv
    from datetime import date

    from PySide6.QtWidgets import QFileDialog

    from booktracker.gui import MainWindow

    key=book(library)
    library.add_session(
        key,read_date=date.today().isoformat(),minutes=43,mode="listening"
    )
    win=MainWindow(library)
    target=tmp_path/"habits.csv"
    monkeypatch.setattr(
        QFileDialog,"getSaveFileName",
        lambda *_args,**_kwargs:(str(target),"CSV")
    )
    try:
        win.navigate(11)
        assert "0.7 h" in win.habits_cards["minutes"].text()
        win.export_habits_csv()
        with target.open(encoding="utf-8-sig",newline="") as handle:
            entries=list(csv.DictReader(handle))
        assert len(entries)==1
        assert entries[0]["Listening minutes"]=="43"
    finally:
        win.close()


def test_import_kindle_gui_with_explicit_mapping(app,library,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from booktracker.gui import ClippingsPreviewDialog, MainWindow

    key=book(library)
    path=tmp_path/"My Clippings.txt"
    path.write_text(
        clip("The Way of Kings","Brandon Sanderson","Highlight","Stormlight")
    )
    monkeypatch.setattr(
        QFileDialog,"getOpenFileName",
        lambda *_args,**_kwargs:(str(path),"TXT")
    )
    monkeypatch.setattr(
        ClippingsPreviewDialog,"exec",
        lambda self:self.DialogCode.Accepted
    )
    monkeypatch.setattr(
        QMessageBox,"information",lambda *_args,**_kwargs:None
    )
    win=MainWindow(library)
    try:
        win.navigate(8)
        win.import_kindle_clippings()
        assert len(library.list_annotations(work_id=key))==1
        assert win.journal_table.rowCount()==1
        win.import_kindle_clippings()
        assert len(library.list_annotations(work_id=key))==1
    finally:
        win.close()


def test_annual_goal_pacing_uses_existing_targets(library):
    target=book(library)
    library.save_goal(2026,books=20,chapters=500,pages=1000)
    library.record_progress_on(target,250,date="2026-02-01")
    pace=library.annual_goal_pacing(year=2026,today="2026-07-01")
    assert pace["elapsed_days"]==182
    assert pace["total_days"]==365
    assert pace["metrics"]["pages"]["actual"]==250
    assert pace["metrics"]["pages"]["target"]==1000
    assert pace["metrics"]["pages"]["percent"]==25.0
    assert pace["metrics"]["pages"]["on_pace"] is False
    assert pace["metrics"]["books"]["actual"]==0
    assert pace["metrics"]["books"]["projected_year_end"]==0


def test_annual_goal_pacing_leap_year_and_future(library):
    library.save_goal(2028,books=12)
    pace=library.annual_goal_pacing(year=2028,today="2027-12-31")
    assert pace["total_days"]==366
    assert pace["elapsed_days"]==0
    assert pace["metrics"]["books"]["on_pace"] is None
    assert pace["metrics"]["books"]["expected_now"]==0
    ended=library.annual_goal_pacing(year=2028,today="2030-01-01")
    assert ended["elapsed_days"]==366
    assert ended["metrics"]["books"]["projected_year_end"]==0


def test_timer_requires_full_minute_and_allows_pause(app,library,monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.gui import ReadingTimerDialog

    key=book(library)
    messages=[]
    monkeypatch.setattr(
        QMessageBox,"warning",lambda *_args,**_kwargs:messages.append(True)
    )
    timer=ReadingTimerDialog(None,library,work_id=key)
    try:
        timer._accumulated_ms=59000
        timer.save_timer()
        assert timer.result()!=timer.DialogCode.Accepted
        assert messages
        timer._accumulated_ms=71000
        timer.save_timer()
        assert timer.result()==timer.DialogCode.Accepted
        assert timer.values()["minutes"]==1
    finally:
        timer.close()


def test_timer_no_unintended_automatic_history(app,library,monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from booktracker.gui import ReadingTimerDialog

    key=book(library)
    monkeypatch.setattr(
        QMessageBox,"question",
        lambda *_args,**_kwargs:QMessageBox.StandardButton.Yes
    )
    timer=ReadingTimerDialog(None,library,work_id=key)
    try:
        timer._accumulated_ms=120000
        timer.reject()
        assert library.list_sessions(work_id=key)==[]
        assert library.history(key)==[]
    finally:
        timer.close()
