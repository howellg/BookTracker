"""Integrated, local-only EPUB reader for linked BookTracker books.

QTextBrowser intentionally renders only sanitized, resource-bounded HTML.
Opening and scrolling never creates reading-history or timed-session records.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QImageReader, QTextDocument
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .epub_reader import EpubBook
from .epub_search import search_book


def _action_button(text, callback):
    button=QPushButton(text)
    button.clicked.connect(callback)
    return button


class _BookSearchThread(QThread):
    """Read a separate EPUB handle so book-wide searches never block Qt."""

    found = Signal(object)
    failed = Signal(str)

    def __init__(self, path, term, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.term = term
        self.cancel = Event()

    def run(self):
        try:
            report = search_book(EpubBook(self.path), self.term, cancel=self.cancel)
            self.found.emit(report)
        except Exception as exc:
            # A damaged or replaced file must report an error, not take down Qt.
            self.failed.emit(str(exc))


class EpubReader(QDialog):
    """Full-size modeless-safe dialog with explicit save-on-close."""
    def __init__(self, parent, library, file_id, *, settings=None):
        super().__init__(parent)
        self.library=library
        self.file_id=file_id
        self.link=library.get_local_file(file_id)
        if not self.link or self.link["format"]!="epub":
            raise ValueError("Select a linked EPUB to read.")
        if not self.link["path"] or not Path(self.link["path"]).is_file():
            raise ValueError("This EPUB is unavailable here; relink it first.")
        self.book=EpubBook(self.link["path"])
        self.work=library.get_work(self.link["work_id"])
        self.index=0
        self._restoring=False
        self._closed=False
        self._search_thread=None
        self._search_term=""
        self.settings=settings or QSettings("BookTracker","BookTracker")
        self.setWindowTitle(f"Reading: {self.work['title']} — BookTracker")
        self.resize(1130,800)
        self.setMinimumSize(660,480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        root=QVBoxLayout(self)
        row=QHBoxLayout()
        self.heading=QLabel(self.work["title"])
        self.heading.setStyleSheet("font-size:19px;font-weight:bold;")
        row.addWidget(self.heading,stretch=1)
        self._font_size=max(12,min(36,int(self.settings.value("reader_font_size",18))))
        self.theme=QComboBox()
        for name in ("Dark","Sepia","Light"):
            self.theme.addItem(name)
        saved=self.settings.value("reader_theme","Dark")
        self.theme.setCurrentText(saved if saved in ("Dark","Sepia","Light") else "Dark")
        self.theme.currentIndexChanged.connect(self.apply_theme)
        row.addWidget(QLabel("Theme"))
        row.addWidget(self.theme)
        row.addWidget(_action_button("A−",lambda:self.zoom(-1)))
        row.addWidget(_action_button("A+",lambda:self.zoom(1)))
        root.addLayout(row)
        self.splitter=QSplitter(Qt.Orientation.Horizontal)
        sidebar=QWidget()
        side=QVBoxLayout(sidebar)
        side.setContentsMargins(0,0,0,0)
        side.addWidget(QLabel("Contents"))
        self.toc_filter=QLineEdit()
        self.toc_filter.setPlaceholderText("Filter contents by title…")
        self.toc_filter.textChanged.connect(self.filter_contents)
        side.addWidget(self.toc_filter)
        self.sections=QListWidget()
        self.sections.setMinimumWidth(155)
        self.sections.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for i,item in enumerate(self.book.spine):
            chapter=QListWidgetItem(f"{i+1:>3}  {item.title}")
            chapter.setData(Qt.ItemDataRole.UserRole,i)
            self.sections.addItem(chapter)
        self.sections.currentRowChanged.connect(self.go_section)
        side.addWidget(self.sections,stretch=2)
        side.addWidget(QLabel("Book-wide search results"))
        self.book_matches=QListWidget()
        self.book_matches.setWordWrap(True)
        self.book_matches.setMinimumHeight(95)
        self.book_matches.itemDoubleClicked.connect(self.jump_to_search_hit)
        side.addWidget(self.book_matches,stretch=1)
        side.addWidget(QLabel("Bookmarks"))
        self.marks=QListWidget()
        self.marks.itemDoubleClicked.connect(self.goto_bookmark)
        side.addWidget(self.marks,stretch=1)
        side.addWidget(_action_button("Remove bookmark",self.remove_bookmark))
        self.splitter.addWidget(sidebar)
        reading=QWidget()
        reading_layout=QVBoxLayout(reading)
        reading_layout.setContentsMargins(0,0,0,0)
        find_row=QHBoxLayout()
        self.search=QLineEdit()
        self.search.setPlaceholderText("Find within this section…")
        self.search.returnPressed.connect(self.find_next)
        find_row.addWidget(self.search,stretch=1)
        find_row.addWidget(_action_button("Find next",self.find_next))
        self.book_search_button=_action_button("Find in book",self.find_in_book)
        find_row.addWidget(self.book_search_button)
        self.cancel_search_button=_action_button("Cancel",self.cancel_book_search)
        self.cancel_search_button.setEnabled(False)
        find_row.addWidget(self.cancel_search_button)
        reading_layout.addLayout(find_row)
        self.viewer=QTextBrowser()
        self.viewer.setOpenLinks(False)
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setReadOnly(True)
        self.viewer.setAcceptDrops(False)
        self.viewer.setUndoRedoEnabled(False)
        self.viewer.anchorClicked.connect(lambda _url:None)
        self.viewer.verticalScrollBar().valueChanged.connect(self.schedule_save)
        reading_layout.addWidget(self.viewer,stretch=1)
        controls=QHBoxLayout()
        controls.addWidget(_action_button("◀ Previous",lambda:self.step(-1)))
        self.counter=QLabel()
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.counter,stretch=1)
        controls.addWidget(_action_button("Next ▶",lambda:self.step(1)))
        reading_layout.addLayout(controls)
        buttons=QHBoxLayout()
        buttons.addWidget(_action_button("Bookmark",self.add_bookmark))
        buttons.addWidget(_action_button("Save note / selected quote",self.save_note))
        buttons.addWidget(_action_button("Log reading progress…",self.log_progress))
        reading_layout.addLayout(buttons)
        self.splitter.addWidget(reading)
        self.splitter.setSizes([220,910])
        root.addWidget(self.splitter,stretch=1)
        self.message=QLabel(
            "Reading position saves automatically. Opening and scrolling do not log "
            "completed chapters, pages, or minutes."
        )
        self.message.setWordWrap(True)
        root.addWidget(self.message)
        self._save_timer=QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(650)
        self._save_timer.timeout.connect(self.save_position)
        self.apply_theme()
        last=self.library.epub_position(self.file_id)
        start=0
        ratio=0
        if last:
            match=next(
                (i for i,item in enumerate(self.book.spine)
                 if item.href==last["section_href"]),None
            )
            start=match if match is not None else min(
                last["section_index"],len(self.book.spine)-1
            )
            ratio=last["scroll_ratio"]
        self.load_section(start,ratio)
        self.refresh_bookmarks()
        # Avoid QDialog keyboard defaults reinterpreting arrow keys as accept.
        for key,call in (("Alt+Left",lambda:self.step(-1)),
                         ("Alt+Right",lambda:self.step(1)),
                         ("Alt+Home",lambda:self.load_section(0)),
                         ("Alt+End",lambda:self.load_section(len(self.book)-1)),
                         ("Ctrl+F",self.focus_section_search),
                         ("Ctrl+Shift+F",self.focus_book_search)):
            action=QAction(self)
            action.setShortcut(key)
            action.triggered.connect(call)
            self.addAction(action)

    def apply_theme(self,*_args):
        name=self.theme.currentText()
        palettes={
            "Dark":("#141a24","#e5ebf6","#a6b5c8"),
            "Sepia":("#f2e5cc","#493a2d","#72553a"),
            "Light":("#ffffff","#212936","#546375"),
        }
        background,foreground,subtle=palettes[name]
        self.viewer.setStyleSheet(
            f"QTextBrowser {{ background:{background}; color:{foreground};"
            f" border:1px solid {subtle};padding:22px;"
            f"font-size:{self._font_size}px;line-height:1.5; }}"
        )
        self.settings.setValue("reader_theme",name)

    def zoom(self,delta):
        self._font_size=max(12,min(36,self._font_size+delta))
        self.settings.setValue("reader_font_size",self._font_size)
        ratio=self.scroll_ratio()
        self.apply_theme()
        QTimer.singleShot(0,lambda:self.restore_scroll(ratio))

    def scroll_ratio(self):
        bar=self.viewer.verticalScrollBar()
        return max(0,min(10000,round(
            (bar.value()/bar.maximum())*10000
        ))) if bar.maximum()>0 else 0

    def restore_scroll(self,ratio):
        bar=self.viewer.verticalScrollBar()
        bar.setValue(round(bar.maximum()*max(0,min(10000,ratio))/10000))

    def schedule_save(self,*_args):
        if not self._restoring and not self._closed:
            self._save_timer.start()

    def save_position(self):
        if self._closed:
            return
        item=self.book.spine[self.index]
        self.library.save_epub_position(
            self.file_id,self.index,item.href,self.scroll_ratio()
        )

    def load_section(self,index,ratio=0):
        if not 0<=index<len(self.book):
            return
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save_position()
        self._restoring=True
        content=self.book.section(index)
        self.index=index
        self.sections.blockSignals(True)
        self.sections.setCurrentRow(index)
        self.sections.blockSignals(False)
        self.sections.item(index).setText(
            f"{index+1:>3}  {content['title']}"
        )
        if self.toc_filter.text().strip():
            self.filter_contents(self.toc_filter.text())
        self.viewer.clear()
        # Add each *internal* image via a data-bound Qt document resource.
        image_budget=12_000_000
        for token,internal in content["images"].items():
            try:
                if image_budget<=0:
                    break
                data=self.book.image(internal)
                image_budget-=len(data)
                if image_budget<0:
                    break
                buffer=QBuffer()
                buffer.setData(QByteArray(data))
                buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                image_reader=QImageReader(buffer)
                size=image_reader.size()
                if not size.isValid() or size.width()>3500 or size.height()>3500:
                    continue
                image_reader.setScaledSize(size.scaled(
                    900,1100,Qt.AspectRatioMode.KeepAspectRatio
                ))
                image=image_reader.read()
                if not image.isNull():
                    self.viewer.document().addResource(
                        QTextDocument.ResourceType.ImageResource,
                        QUrl(token),image
                    )
            except (ValueError,OSError,RuntimeError):
                continue
        self.viewer.setHtml(content["html"])
        self.counter.setText(
            f"Spine section {index+1} / {len(self.book)} · {content['title']}"
        )
        self.message.setText(
            "Reading position saves automatically. EPUB spine sections "
            "may not equal actual chapters or printed pages."
        )
        self._restoring=False
        QTimer.singleShot(0,lambda:self.restore_scroll(ratio))
        QTimer.singleShot(40,self.schedule_save)

    def go_section(self,index):
        if 0<=index<len(self.book) and index!=self.index:
            self.load_section(index)

    def step(self,amount):
        target=self.index+amount
        if 0<=target<len(self.book):
            self.load_section(target)
        else:
            self.message.setText("You are at the beginning or end of this EPUB.")

    def find_next(self):
        term=self.search.text().strip()
        if not term:
            return
        if not self.viewer.find(term):
            cursor=self.viewer.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.viewer.setTextCursor(cursor)
            if not self.viewer.find(term):
                self.message.setText("No match in this section.")

    def filter_contents(self, term):
        """Narrow long EPUB tables of contents without changing positions."""
        needle=term.strip().casefold()
        for index in range(self.sections.count()):
            item=self.sections.item(index)
            item.setHidden(bool(needle and needle not in item.text().casefold()))

    def focus_section_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def focus_book_search(self):
        if self.search.text().strip():
            self.find_in_book()
        else:
            self.focus_section_search()

    def find_in_book(self):
        term=self.search.text().strip()
        if not 2<=len(term)<=80:
            self.message.setText("Enter 2–80 characters to search the entire EPUB.")
            self.search.setFocus()
            return
        if self._search_thread is not None:
            self.message.setText("A book-wide search is already running; cancel it first.")
            return
        self._search_term=term
        self.book_matches.clear()
        self.book_search_button.setEnabled(False)
        self.cancel_search_button.setEnabled(True)
        self.message.setText(f"Searching all EPUB sections for ‘{term}’…")
        worker=_BookSearchThread(self.book.path,term,self)
        self._search_thread=worker
        worker.found.connect(self.show_book_results)
        worker.failed.connect(self.book_search_failed)
        worker.finished.connect(self.book_search_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def cancel_book_search(self):
        if self._search_thread is not None:
            self._search_thread.cancel.set()
            self.cancel_search_button.setEnabled(False)
            self.message.setText("Stopping book-wide search…")

    def show_book_results(self, report):
        if self._closed:
            return
        for hit in report.hits:
            item=QListWidgetItem(
                f"§ {hit.section_index+1} · {hit.title}\n…{hit.excerpt}…"
            )
            item.setData(Qt.ItemDataRole.UserRole,hit)
            self.book_matches.addItem(item)
        state=("Cancelled" if report.cancelled else "Search complete")
        detail=(f"{len(report.hits)} matches across {report.sections_scanned} "
                f"EPUB sections")
        if report.truncated:
            detail+=" · results limited (narrow your search)"
        if report.skipped_sections:
            detail+=f" · {report.skipped_sections} unreadable sections skipped"
        self.message.setText(f"{state}: {detail}. Double-click a result to jump.")

    def book_search_failed(self, error):
        if not self._closed:
            self.message.setText(f"Book search could not finish: {error}")

    def book_search_finished(self):
        if self._closed:
            return
        self.book_search_button.setEnabled(True)
        self.cancel_search_button.setEnabled(False)
        self._search_thread=None

    def jump_to_search_hit(self, item):
        hit=item.data(Qt.ItemDataRole.UserRole)
        if hit is None:
            return
        self.load_section(hit.section_index)
        self.search.setText(self._search_term)

        # Deferred until the reader's scroll restoration has completed.
        def show_match():
            if self._closed:
                return
            cursor=self.viewer.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.viewer.setTextCursor(cursor)
            for _ in range(hit.occurrence):
                if not self.viewer.find(self._search_term):
                    self.message.setText("Passage not found in rendered section.")
                    return
            self.message.setText(
                f"Found in section {hit.section_index+1}: {hit.title}."
            )
        QTimer.singleShot(55,show_match)

    def refresh_bookmarks(self):
        self.marks.clear()
        for row in self.library.list_epub_bookmarks(self.file_id):
            item=QListWidgetItem(
                f"{row['section_index']+1}: {row['label']}"
            )
            item.setData(Qt.ItemDataRole.UserRole,row)
            self.marks.addItem(item)

    def add_bookmark(self):
        default=self.book._titles.get(self.index) or f"Section {self.index+1}"
        title,okay=QInputDialog.getText(
            self,"Bookmark location","Label:",text=default
        )
        if not okay:
            return
        try:
            self.library.add_epub_bookmark(
                self.file_id,self.index,self.book.spine[self.index].href,
                self.scroll_ratio(),title
            )
            self.refresh_bookmarks()
        except ValueError as exc:
            QMessageBox.warning(self,"Bookmark",str(exc))

    def goto_bookmark(self,item):
        row=item.data(Qt.ItemDataRole.UserRole)
        idx=next(
            (i for i,spine in enumerate(self.book.spine)
             if spine.href==row["section_href"]),None
        )
        if idx is None:
            idx=min(row["section_index"],len(self.book)-1)
        self.load_section(idx,row["scroll_ratio"])

    def remove_bookmark(self):
        item=self.marks.currentItem()
        if not item:
            return
        row=item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(
            self,"Remove bookmark?",f"Remove '{row['label']}'?"
        )!=QMessageBox.StandardButton.Yes:
            return
        self.library.remove_epub_bookmark(row["id"])
        self.refresh_bookmarks()

    def save_note(self):
        selected=self.viewer.textCursor().selectedText().replace("\u2029","\n").strip()
        if len(selected)>7000:
            selected=selected[:7000]
        note,okay=QInputDialog.getMultiLineText(
            self,"Save reading note",
            "Note about this section (selected text is included as a quote):"
        )
        if not okay or not note.strip() and not selected:
            return
        heading=self.book._titles.get(self.index) or f"Section {self.index+1}"
        content=f"EPUB · {heading}\n"
        if selected:
            content+=f"\nQuote:\n{selected}\n"
        if note.strip():
            content+=f"\n{note.strip()}"
        try:
            self.library.add_annotation(
                self.link["work_id"],
                kind="quote" if selected else "note",
                body=content[:10000],location=self.index+1,
                location_unit="chapters"
            )
            self.message.setText("Saved in BookTracker Notes & quotes.")
        except ValueError as exc:
            QMessageBox.warning(self,"Reading note",str(exc))

    def log_progress(self):
        work=self.library.get_work(self.link["work_id"])
        run=self.library.active_run(work["id"])
        current=run["current_progress"] if run else 0
        unit=work["progress_unit"]
        prompt=(
            f"Enter a {unit} position you've actually read.\n"
            "EPUB sections are not necessarily chapters or printed pages. "
            "BookTracker will NOT estimate this for you."
        )
        result,okay=QInputDialog.getInt(
            self,"Log actual reading progress",prompt,current,
            0,100 if unit=="percent" else 10000000
        )
        if not okay:
            return
        if QMessageBox.question(
            self,"Confirm reading milestone?",
            f"Record your {unit} progress as {result}? "
            "This changes formal reading history (unlike scrolling)."
        )!=QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.set_progress(
                work["id"],result,note="Manually logged in EPUB reader."
            )
            self.message.setText(
                f"Saved actual reading progress: {result} {unit}."
            )
        except ValueError as exc:
            QMessageBox.warning(self,"Reading progress",str(exc))

    def closeEvent(self,event):
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.save_position()
        self._closed=True
        if self._search_thread is not None and self._search_thread.isRunning():
            # Never destroy a running Qt thread when a reader dialog closes.
            self._search_thread.cancel.set()
            self._search_thread.wait()
        super().closeEvent(event)
