"""Qt audiobook catalogue, AudiobookAI import preview and read-only media discovery."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .audiobooks import (
    _candidate,
    group_candidates,
    probe_m4b,
    scan_audiobookai_database,
    scan_m4b_folder,
)
from .database import _book_identity


def fmt_duration(milliseconds):
    seconds=int(max(0,milliseconds or 0))//1000
    hours,rem=divmod(seconds,3600)
    minutes,seconds=divmod(rem,60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _label(text,style=None):
    obj=QLabel(text)
    obj.setWordWrap(True)
    if style:
        obj.setObjectName(style)
    return obj


def _button(text,callback,*,primary=False):
    item=QPushButton(text)
    item.setMinimumHeight(34)
    if primary:
        item.setObjectName("primary")
    item.clicked.connect(callback)
    return item


def scan_m4b_files(paths, *, cancel=None):
    if not isinstance(paths,(list,tuple)) or len(paths)>150:
        raise ValueError("Choose 150 or fewer M4B files at once.")
    entries=[]
    known=set()
    for path in paths:
        if cancel and cancel():
            return {"records":[],"cancelled":True,"skipped":0}
        if str(path).casefold().endswith(".m4b"):
            item=_candidate(path=path,source_name="Selected M4B files")
            if not item["available"]:
                continue
            if item["path"] in known:
                continue
            known.add(item["path"])
            info=probe_m4b(item["path"])
            item.update({
                key:info[key] for key in (
                    "duration_ms","chapter_count","codec","probe_status","chapters"
                )
            })
            # Untagged files retain their filename as the title.
            if not item["part_number"] and not item["is_complete"] and info["title"]:
                item["title"]=info["title"]
            if info["author"]:
                item["author"]=info["author"]
            if info["narrator"]:
                item["narrator"]=info["narrator"]
            entries.append(item)
    return {"records":entries,"cancelled":False,"skipped":len(paths)-len(entries)}


class AudioScanThread(QThread):
    found=Signal(object)
    failed=Signal(str)

    def __init__(self,kind,path_or_paths,parent=None):
        super().__init__(parent)
        self.kind=kind
        self.source=path_or_paths

    def run(self):
        try:
            cancelled=self.isInterruptionRequested
            if self.kind=="studio":
                data=scan_audiobookai_database(self.source,cancel=cancelled)
            elif self.kind=="folder":
                data=scan_m4b_folder(self.source,cancel=cancelled)
            elif self.kind=="files":
                data=scan_m4b_files(self.source,cancel=cancelled)
            else:
                raise ValueError("Unknown audiobook import source.")
            if not data["cancelled"] and not self.isInterruptionRequested():
                self.found.emit(data)
        except Exception as exc:
            self.failed.emit(str(exc))


class AudioImportPreview(QDialog):
    """User explicitly matches every source title to one BookTracker work."""

    def __init__(self,parent,library,records,*,source_name="Audiobooks"):
        super().__init__(parent)
        self.library=library
        self.records=records
        self.groups=group_candidates(records)
        self.setWindowTitle("Match audiobook files — "+source_name)
        self.resize(1020,680)
        self.setMinimumSize(730,520)
        layout=QVBoxLayout(self)
        layout.addWidget(_label("Review and match audiobooks","headline"))
        total_missing=sum(not item["available"] for item in records)
        layout.addWidget(_label(
            f"{len(records)} audiobook files across {len(self.groups)} titles; "
            f"{total_missing} currently missing. "
            "Nothing is changed until you select Import.", "muted"
        ))
        layout.addWidget(_label(
            "Only a unique exact title + author match is preselected. "
            "Confirm the book for each row, create a new custom library entry, "
            "or leave Skip. The original M4Bs and AudiobookAI database are never changed.",
            "muted"
        ))
        if len(self.groups)>300:
            raise ValueError(
                "This scan found over 300 audiobook titles. Scan smaller folders."
            )
        self.group_table=QTableWidget(len(self.groups),4)
        self.group_table.setHorizontalHeaderLabels([
            "Book title / author","M4B files","Missing","Link to BookTracker"
        ])
        self.group_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.group_table.verticalHeader().setVisible(False)
        self.group_table.horizontalHeader().setStyleSheet(
            "QHeaderView { background-color: #233146; }"
        )
        self.group_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.group_table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        self.group_table.horizontalHeader().setSectionResizeMode(
            3,QHeaderView.ResizeMode.Stretch
        )
        for col in (1,2):
            self.group_table.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        books=library.list_works(sort="title")
        self.choices=[]
        for index,group in enumerate(self.groups):
            title=group["title"]+(
                f" — {group['author']}" if group["author"] else ""
            )
            self.group_table.setItem(index,0,QTableWidgetItem(title))
            self.group_table.setItem(
                index,1,QTableWidgetItem(str(group["count"]))
            )
            self.group_table.setItem(
                index,2,QTableWidgetItem(str(group["missing"]))
            )
            choice=QComboBox()
            choice.addItem("Skip this audiobook",None)
            choice.addItem("+ Create custom book (unread)","create")
            matches=[]
            for work in books:
                choice.addItem(
                    f"{work['title']} — {work['author'] or 'Unknown author'}",
                    work["id"]
                )
                if _book_identity(work["title"])!=_book_identity(group["title"]):
                    continue
                if not group["author"]:
                    continue  # Title-only guesses always require user review.
                if _book_identity(work["author"])!=_book_identity(group["author"]):
                    continue
                matches.append(work["id"])
            if len(matches)==1:
                choice.setCurrentIndex(choice.findData(matches[0]))
            self.group_table.setCellWidget(index,3,choice)
            self.group_table.setRowHeight(index,42)
            self.choices.append(choice)
        self.group_table.itemSelectionChanged.connect(self.preview_files)
        layout.addWidget(self.group_table,stretch=3)
        self.include_missing=QCheckBox(
            "Include missing AudiobookAI receipts as relinkable file entries"
        )
        self.include_missing.setChecked(True)
        layout.addWidget(self.include_missing)
        layout.addWidget(_label(
            "Selected title's part details (duration, sections and narrator)"
            ,"subheading"
        ))
        self.file_list=QTableWidget(0,5)
        self.file_list.setHorizontalHeaderLabels([
            "M4B filename","Part / edition","Narrator","Duration","EPUB sections / status"
        ])
        self.file_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.file_list.verticalHeader().setVisible(False)
        self.file_list.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        self.file_list.horizontalHeader().setSectionResizeMode(
            4,QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.file_list,stretch=2)
        layout.addWidget(_label(
            "Importing never marks a book read, records listening time, or changes "
            "chapter progress. Durations are media lengths, not listening history. "
            "Portable backups retain filenames and media metadata, not large M4B files.",
            "muted"
        ))
        buttons=QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Import approved matches"
        )
        buttons.accepted.connect(self.validate_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if self.groups:
            self.group_table.selectRow(0)

    def preview_files(self):
        row=self.group_table.currentRow()
        if not 0<=row<len(self.groups):
            self.file_list.setRowCount(0)
            return
        group=self.groups[row]
        self.file_list.setRowCount(len(group["indices"]))
        for i,index in enumerate(group["indices"]):
            item=self.records[index]
            kind=("Complete" if item["is_complete"] else
                  f"Part {item['part_number']}" if item["part_number"] else "M4B")
            sections=(
                f"{item['section_first']}–{item['section_last']}"
                if item["section_first"] else "Sections unknown"
            )
            status="Ready" if item["available"] else "Missing"
            values=(
                item["filename"],kind,item["narrator"] or "—",
                fmt_duration(item["duration_ms"]) if item["duration_ms"] else "Unknown",
                sections+" · "+status
            )
            for col,value in enumerate(values):
                self.file_list.setItem(i,col,QTableWidgetItem(value))
            self.file_list.setRowHeight(i,33)

    def decisions(self):
        return {
            group["key"]: self.choices[i].currentData()
            for i,group in enumerate(self.groups)
        }

    def selected_records(self):
        if self.include_missing.isChecked():
            return self.records
        return [item for item in self.records if item["available"]]

    def validate_accept(self):
        if not any(choice.currentData() is not None for choice in self.choices):
            QMessageBox.warning(
                self,"No books selected","Choose an existing book or Create custom."
            )
            return
        if not self.selected_records():
            QMessageBox.warning(
                self,"No media selected","All files are missing. "
                "Enable missing-file hints or select another source."
            )
            return
        self.accept()


class AudioLibraryPage(QWidget):
    """Audiobook metadata overview; no audio playback or synchronization claims."""

    def __init__(self,parent,library):
        super().__init__(parent)
        self.library=library
        self.main=parent
        self.worker=None
        self._player_dialog=None
        self.setAcceptDrops(True)
        layout=QVBoxLayout(self)
        head=QHBoxLayout()
        head.addWidget(_label("Audiobooks","headline"),stretch=1)
        head.addWidget(_button(
            "Import AudiobookAI",self.import_studio,primary=True
        ))
        head.addWidget(_button("Scan M4B folder",self.import_folder))
        head.addWidget(_button("Add M4B files",self.import_files))
        self.cancel_scan_button=_button("Cancel scan",self.stop_scan)
        self.cancel_scan_button.setEnabled(False)
        head.addWidget(self.cancel_scan_button)
        layout.addLayout(head)
        layout.addWidget(_label(
            "Discover verified AudiobookAI completed parts, archival M4Bs and other "
            "local audiobooks. Your source files stay in place. Select an audiobook "
            "to inspect its durations and chapter markers.",
            "muted"
        ))
        filters=QHBoxLayout()
        self.search=QLineEdit()
        self.search.setPlaceholderText("Search audiobook titles, authors or narrators…")
        self.search.textChanged.connect(self.refresh)
        filters.addWidget(self.search,stretch=1)
        self.show_missing=QCheckBox("Missing files only")
        self.show_missing.toggled.connect(self.refresh)
        filters.addWidget(self.show_missing)
        layout.addLayout(filters)
        self.summary=_label("","subheading")
        layout.addWidget(self.summary)
        self.scan_message=_label(
            "Ready. Choose Import AudiobookAI for its completed-parts database, "
            "or select ordinary M4B files/folders.","muted"
        )
        layout.addWidget(self.scan_message)
        splitter=QSplitter(Qt.Orientation.Vertical)
        self.groups=QTableWidget(0,8)
        self.groups.setHorizontalHeaderLabels([
            "Book","Narrator","M4Bs","Parts","Full editions",
            "Media duration","Missing","Possible part gaps"
        ])
        self.groups.verticalHeader().setVisible(False)
        self.groups.horizontalHeader().setStyleSheet(
            "QHeaderView { background-color: #233146; }"
        )
        self.groups.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.groups.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.groups.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.groups.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Interactive
        )
        self.groups.horizontalHeader().resizeSection(0,270)
        for col in range(1,8):
            self.groups.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        self.groups.itemSelectionChanged.connect(self.refresh_files)
        self.groups.itemDoubleClicked.connect(lambda _item:self.open_book())
        self.groups.setMinimumHeight(180)
        splitter.addWidget(self.groups)
        self.files=QTableWidget(0,9)
        self.files.setHorizontalHeaderLabels([
            "File","Part / full","Narrator","Length","Markers","EPUB sections",
            "Status","Origin","Resume (this device)"
        ])
        self.files.verticalHeader().setVisible(False)
        self.files.horizontalHeader().setStyleSheet(
            "QHeaderView { background-color: #233146; }"
        )
        self.files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.files.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Interactive
        )
        self.files.horizontalHeader().resizeSection(0,270)
        for col in range(1,9):
            self.files.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        self.files.setMinimumHeight(125)
        self.files.itemDoubleClicked.connect(lambda _item:self.open_file())
        splitter.addWidget(self.files)
        splitter.setSizes([350,190])
        layout.addWidget(splitter,stretch=1)
        actions=QGridLayout()
        for index,(text,func) in enumerate((
            ("Play in BookTracker",self.play_in_app),
            ("Open audio file",self.open_file),
            ("Start listening timer",self.start_listening_timer),
            ("Log listening time",self.log_listening_session),
            ("Chapter markers",self.show_chapters),
            ("Refresh metadata",self.refresh_metadata),
            ("Local file links",self.manage_files),
            ("Open library book",self.open_book),
        )):
            actions.addWidget(_button(text,func),index//4,index%4)
        layout.addLayout(actions)
        layout.addWidget(_label(
            "Possible part gaps are missing numbers between 1 and the largest "
            "known part; they do not prove generation was attempted. "
            "When a complete M4B is present, its length is shown instead of "
            "adding it to overlapping part durations. Built-in playback "
            "resumes only on this device. Playing, seeking or opening an M4B "
            "never logs listening minutes or reading milestones.", "muted"
        ))
        self.refresh()

    def current_group(self):
        row=self.groups.currentRow()
        cell=self.groups.item(row,0) if row>=0 else None
        if cell:
            key=cell.data(Qt.ItemDataRole.UserRole)
            return next(
                (item for item in getattr(self,"_summaries",[])
                 if (item["work_id"],item["narrator"])==key),None
            )
        return None

    def current_file(self):
        row=self.files.currentRow()
        cell=self.files.item(row,0) if row>=0 else None
        if cell:
            file_id=cell.data(Qt.ItemDataRole.UserRole)
            return next(
                (item for item in getattr(self,"_files",[])
                 if item["id"]==file_id),None
            )
        return None

    def refresh(self,*_args):
        selected=self.current_group()
        former=(selected["work_id"],selected["narrator"]) if selected else None
        all_groups=self.library.audio_summary()
        query=self.search.text().strip().casefold()
        shown=[
            row for row in all_groups
            if (not query or query in " ".join((
                row["title"],row["author"],row["narrator"]
            )).casefold())
            and (not self.show_missing.isChecked() or
                 row["missing_files"] or row["missing_parts"])
        ]
        self._summaries=shown
        self.groups.blockSignals(True)
        self.groups.setRowCount(len(shown))
        chosen=0
        for row,item in enumerate(shown):
            if (item["work_id"],item["narrator"])==former:
                chosen=row
            columns=(
                item["title"],item["narrator"] or "Unspecified",
                str(item["files"]),str(item["parts"]),str(item["complete"]),
                fmt_duration(item["duration_ms"]) if item["duration_ms"] else "Unknown",
                str(item["missing_files"]),
                ",".join(map(str,item["missing_parts"][:8])) or "—",
            )
            for col,value in enumerate(columns):
                cell=QTableWidgetItem(value)
                if col==0:
                    cell.setData(Qt.ItemDataRole.UserRole,
                                 (item["work_id"],item["narrator"]))
                self.groups.setItem(row,col,cell)
            self.groups.setRowHeight(row,36)
        self.groups.blockSignals(False)
        if shown:
            self.groups.selectRow(chosen)
        else:
            self.files.setRowCount(0)
        all_files=self.library.list_audio_files()
        missing=sum(not r["available"] for r in all_files)
        total=sum(group["duration_ms"] for group in all_groups)
        self.summary.setText(
            f"{len(shown)} shown audiobook editions · {len(all_files)} M4B links · "
            f"{missing} missing · {fmt_duration(total)} estimated media duration "
            "(complete editions and parts not double-counted)"
        )
        self.refresh_files()

    def refresh_files(self):
        from .audio_player import AudioResumeStore

        previous=self.current_file()
        previous_id=previous["id"] if previous else None
        group=self.current_group()
        if not group:
            self._files=[]
            self.files.setRowCount(0)
            return
        resume=AudioResumeStore(QSettings("BookTracker","BookTracker"))
        self._files=[
            item for item in self.library.list_audio_files(
                work_id=group["work_id"]
            ) if item["narrator"]==group["narrator"]
        ]
        self.files.setRowCount(len(self._files))
        for row,item in enumerate(self._files):
            parts=(
                f"{item['section_first']}–{item['section_last']}"
                if item["section_first"] else "—"
            )
            values=(
                item["filename"],
                "Complete" if item["is_complete"] else
                f"Part {item['part_number']}" if item["part_number"] else "M4B",
                item["narrator"] or "—",
                fmt_duration(item["duration_ms"]) if item["duration_ms"] else "Unknown",
                str(item["chapter_count"]) if item["chapter_count"] else "Unknown",
                parts,
                "Available" if item["available"] else "Missing / relink",
                item["origin"],
                fmt_duration(resume.position(item["id"],item["path"]))
                if item["available"] and resume.position(item["id"],item["path"]) else "—",
            )
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value)
                if col==0:
                    cell.setData(Qt.ItemDataRole.UserRole,item["id"])
                self.files.setItem(row,col,cell)
            self.files.setRowHeight(row,34)
        if self._files:
            chosen=next(
                (index for index,item in enumerate(self._files) if item["id"]==previous_id),
                0
            )
            self.files.selectRow(chosen)

    def import_studio(self):
        suggested=str(Path.home()/"AudiobookAI/output/audiobookai_studio.sqlite3")
        chosen,_=QFileDialog.getOpenFileName(
            self,"Choose AudiobookAI Studio database",suggested,
            "SQLite database (*.sqlite3 *.db *.sqlite)"
        )
        if chosen:
            self.begin_scan("studio",chosen)

    def import_folder(self):
        path=QFileDialog.getExistingDirectory(
            self,"Scan folder containing M4B audiobooks"
        )
        if path:
            self.begin_scan("folder",path)

    def import_files(self):
        paths,_=QFileDialog.getOpenFileNames(
            self,"Choose M4B audiobooks","","M4B audiobooks (*.m4b)"
        )
        if paths:
            self.begin_scan("files",paths)

    def begin_scan(self,kind,source):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(
                self,"Scan already running",
                "An audiobook scan is already in progress."
            )
            return
        self.scan_message.setText(
            "Reading audiobook metadata in the background. "
            "No audio is being converted or played…"
        )
        worker=AudioScanThread(kind,source,self)
        self.worker=worker
        self.cancel_scan_button.setEnabled(True)
        worker.found.connect(self.scan_complete)
        worker.failed.connect(self.scan_failed)
        worker.finished.connect(self.scan_stopped)
        worker.start()

    def scan_complete(self,result):
        records=result.get("records",[])
        if not records:
            self.scan_message.setText(
                "No completed M4Bs were found. "
                "Try a different directory or AudiobookAI database."
            )
            return
        self.scan_message.setText(
            f"Found {len(records)} M4B records. Review book matching to import."
        )
        try:
            dialog=AudioImportPreview(
                self,self.library,records,source_name="AudiobookAI / M4B"
            )
            if dialog.exec()!=QDialog.DialogCode.Accepted:
                self.scan_message.setText("Audiobook import cancelled; no changes made.")
                return
            stats=self.library.import_audio_files(
                dialog.selected_records(),dialog.decisions()
            )
            self.refresh()
            self.scan_message.setText(
                f"Linked {stats['linked']}, already linked {stats['already_linked']}, "
                f"missing {stats['missing']}, new custom books {stats['created']}."
            )
            if self.main:
                self.main.refresh_all()
            QMessageBox.information(
                self,"Audiobook import complete",
                f"New file links: {stats['linked']}\n"
                f"Existing file links: {stats['already_linked']}\n"
                f"Missing-file hints: {stats['missing']}\n"
                f"New custom books: {stats['created']}\n"
                f"Skipped unmatched files: {stats['unmatched']}\n\n"
                "No actual M4B audio or listening history was changed."
            )
        except Exception as exc:
            QMessageBox.critical(self,"Audiobook import failed",str(exc))

    def scan_failed(self,message):
        self.scan_message.setText("Audiobook scan could not finish.")
        QMessageBox.warning(self,"Audiobook scan",message)

    def scan_stopped(self):
        self.cancel_scan_button.setEnabled(False)
        self.scan_message.setText(
            self.scan_message.text().replace("in the background","finished")
        )

    def stop_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel_scan_button.setEnabled(False)
            self.scan_message.setText(
                "Stopping scan after the current metadata probe completes…"
            )

    def play_in_app(self):
        """Open a modeless player so the listening timer stays usable."""
        if self._player_dialog is not None:
            self._player_dialog.show()
            self._player_dialog.raise_()
            self._player_dialog.activateWindow()
            return
        item=self.current_file()
        if not item:
            self.scan_message.setText("Select an M4B file to play.")
            return
        if not item["available"]:
            QMessageBox.information(
                self,"M4B file missing",
                "Relink this M4B on this device before playing it."
            )
            return
        try:
            from .audio_player import AudioPlayerDialog

            dialog=AudioPlayerDialog(self,self._files,item)
            self._player_dialog=dialog
            dialog.finished.connect(self.player_closed)
            dialog.show()
        except (RuntimeError,ValueError,OSError) as exc:
            QMessageBox.warning(
                self,"Built-in player unavailable",
                f"{exc}\n\nYou can still open the audio file in your external player."
            )

    def player_closed(self,*_args):
        self._player_dialog=None
        self.refresh_files()

    def start_listening_timer(self):
        group=self.current_group()
        if not group or not self.main:
            self.scan_message.setText("Select an audiobook before starting a timer.")
            return
        self.main.open_reading_timer(work_id=group["work_id"],mode="listening")

    def log_listening_session(self):
        """Save explicit, user-entered minutes; never derive from file duration."""
        group=self.current_group()
        if not group:
            self.scan_message.setText("Select an audiobook before logging time.")
            return
        from .gui import ReadingSessionDialog

        dialog=ReadingSessionDialog(self,self.library,work_id=group["work_id"])
        dialog.mode.setCurrentIndex(dialog.mode.findData("listening"))
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        try:
            self.library.add_session(
                group["work_id"],**dialog.values()
            )
            if self.main:
                self.main.refresh_all(group["work_id"])
            self.scan_message.setText(
                f"Saved {dialog.values()['minutes']} manually reported listening minutes."
            )
        except ValueError as exc:
            QMessageBox.warning(self,"Log listening time",str(exc))

    def open_file(self):
        item=self.current_file()
        if not item:
            return
        if not item["available"]:
            QMessageBox.information(
                self,"M4B file missing",
                "This audiobook isn't available on this computer. "
                "Select Local file links to relink the original file."
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(item["path"])):
            QMessageBox.warning(
                self,"Cannot open audio",
                "No default application could open this audiobook."
            )

    def show_chapters(self):
        item=self.current_file()
        if not item:
            return
        if not item["available"]:
            QMessageBox.information(
                self,"Missing M4B","Relink the file before inspecting chapter markers."
            )
            return
        probe=probe_m4b(item["path"])
        if probe["probe_status"]!="ok":
            QMessageBox.warning(
                self,"No chapter information",probe["probe_status"]
            )
            return
        chapters=probe["chapters"]
        if not chapters:
            QMessageBox.information(
                self,"No chapter markers",
                "This audio file contains no readable embedded chapter markers."
            )
            return
        dialog=QDialog(self)
        dialog.setWindowTitle("M4B chapters — "+item["filename"])
        dialog.resize(790,570)
        layout=QVBoxLayout(dialog)
        layout.addWidget(_label(
            f"{len(chapters):,} embedded audio markers. These need not "
            "correspond 1:1 with EPUB chapter numbers.","muted"
        ))
        table=QTableWidget(min(len(chapters),5000),4)
        table.setHorizontalHeaderLabels(["#","Start","End","Chapter title"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            3,QHeaderView.ResizeMode.Stretch
        )
        for index,chapter in enumerate(chapters[:5000]):
            for col,value in enumerate((
                str(index+1),fmt_duration(chapter["start_ms"]),
                fmt_duration(chapter["end_ms"]),chapter["title"] or "—"
            )):
                table.setItem(index,col,QTableWidgetItem(value))
        layout.addWidget(table)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def refresh_metadata(self):
        item=self.current_file()
        if not item:
            return
        if not item["available"]:
            QMessageBox.information(
                self,"Missing file","Relink the M4B before refreshing metadata."
            )
            return
        probe=probe_m4b(item["path"])
        try:
            self.library.update_audio_probe(item["id"],probe)
            self.refresh()
            self.scan_message.setText(
                f"Metadata refreshed for {item['filename']}: "
                f"{probe['probe_status']}."
            )
        except ValueError as exc:
            QMessageBox.warning(self,"Metadata refresh",str(exc))

    def manage_files(self):
        group=self.current_group()
        if group:
            # Local import avoids a circular module dependency.
            from .gui import LocalFilesDialog

            manager=LocalFilesDialog(self,self.library,group["work_id"])
            manager.exec()
            self.refresh()

    def open_book(self):
        group=self.current_group()
        if not group or not self.main:
            return
        self.main.clear_library_filters()
        self.main.navigate(0)
        self.main.refresh_library(select_id=group["work_id"])

    def closeEvent(self,event):
        if self._player_dialog is not None:
            self._player_dialog.close()
        super().closeEvent(event)

    def dragEnterEvent(self,event):
        urls=event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and all(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".m4b")
            for url in urls
        ) and len(urls)<=150:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self,event):
        paths=[url.toLocalFile() for url in event.mimeData().urls()]
        if paths and len(paths)<=150:
            event.acceptProposedAction()
            self.begin_scan("files",paths)
        else:
            event.ignore()
