"""Integrated, local-only EPUB reader for linked BookTracker books.

QTextBrowser intentionally renders only sanitized, resource-bounded HTML.
Opening and scrolling never creates reading-history or timed-session records.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QSaveFile,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QFont, QImageReader, QTextBlockFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
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
        for name in ("Dark","Sepia","Light","High contrast"):
            self.theme.addItem(name)
        saved=self.settings.value("reader_theme","Dark")
        self.theme.setCurrentText(saved if saved in ("Dark","Sepia","Light","High contrast") else "Dark")
        self.theme.currentIndexChanged.connect(self.apply_theme)
        row.addWidget(QLabel("Theme"))
        row.addWidget(self.theme)
        row.addWidget(_action_button("A−",lambda:self.zoom(-1)))
        row.addWidget(_action_button("A+",lambda:self.zoom(1)))
        root.addLayout(row)
        preferences=QHBoxLayout()
        self.font_family=QComboBox()
        self.font_family.addItems(["Serif","Sans","Mono"])
        self.font_family.setCurrentText(self._reader_preference("reader_family","Serif",
                                                            ("Serif","Sans","Mono")))
        self.font_family.currentTextChanged.connect(self.apply_theme)
        preferences.addWidget(QLabel("Font"))
        preferences.addWidget(self.font_family)
        self.line_spacing=QComboBox()
        self.line_spacing.addItems(["Compact","Normal","Relaxed"])
        self.line_spacing.setCurrentText(self._reader_preference(
            "reader_line_spacing","Normal",("Compact","Normal","Relaxed")
        ))
        self.line_spacing.currentTextChanged.connect(self.apply_theme)
        preferences.addWidget(QLabel("Spacing"))
        preferences.addWidget(self.line_spacing)
        self.page_margin=QComboBox()
        self.page_margin.addItems(["Narrow","Comfortable","Wide"])
        self.page_margin.setCurrentText(self._reader_preference(
            "reader_margin","Comfortable",("Narrow","Comfortable","Wide")
        ))
        self.page_margin.currentTextChanged.connect(self.apply_theme)
        preferences.addWidget(QLabel("Margins"))
        preferences.addWidget(self.page_margin)
        self.focus_button=QPushButton("Focus reading")
        self.focus_button.setCheckable(True)
        self.focus_button.toggled.connect(self.set_focus_mode)
        preferences.addWidget(self.focus_button)
        preferences.addStretch()
        root.addLayout(preferences)

        self.splitter=QSplitter(Qt.Orientation.Horizontal)
        sidebar=QWidget()
        side=QVBoxLayout(sidebar)
        side.setContentsMargins(0,0,0,0)
        self.sidebar_tabs=QTabWidget()
        side.addWidget(self.sidebar_tabs)

        toc_page=QWidget()
        toc_layout=QVBoxLayout(toc_page)
        toc_layout.setContentsMargins(5,8,5,5)
        self.toc_filter=QLineEdit()
        self.toc_filter.setPlaceholderText("Filter contents by title…")
        self.toc_filter.textChanged.connect(self.filter_contents)
        toc_layout.addWidget(self.toc_filter)
        self.sections=QListWidget()
        self.sections.setMinimumWidth(155)
        self.sections.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for i,item in enumerate(self.book.spine):
            chapter=QListWidgetItem(f"{i+1:>3}  {item.title}")
            chapter.setData(Qt.ItemDataRole.UserRole,i)
            self.sections.addItem(chapter)
        self.sections.currentRowChanged.connect(self.go_section)
        toc_layout.addWidget(self.sections,stretch=1)
        self.sidebar_tabs.addTab(toc_page,"Contents")

        matches_page=QWidget()
        matches_layout=QVBoxLayout(matches_page)
        matches_layout.setContentsMargins(5,8,5,5)
        matches_layout.addWidget(QLabel("Double-click a result to jump to its passage."))
        self.book_matches=QListWidget()
        self.book_matches.setWordWrap(True)
        self.book_matches.itemDoubleClicked.connect(self.jump_to_search_hit)
        matches_layout.addWidget(self.book_matches,stretch=1)
        self.sidebar_tabs.addTab(matches_page,"Search")

        saved_page=QWidget()
        saved_layout=QVBoxLayout(saved_page)
        saved_layout.setContentsMargins(5,8,5,5)
        saved_layout.addWidget(QLabel("EPUB location bookmarks"))
        self.marks=QListWidget()
        self.marks.itemDoubleClicked.connect(self.goto_bookmark)
        saved_layout.addWidget(self.marks,stretch=1)
        mark_actions=QHBoxLayout()
        mark_actions.addWidget(_action_button("Rename",self.rename_bookmark))
        mark_actions.addWidget(_action_button("Remove",self.remove_bookmark))
        self.listen_button=_action_button("♫ Listen",self.listen_to_work)
        self.listen_button.setToolTip("Play the linked audiobook for this work")
        self.listen_button.setEnabled(
            parent is not None and hasattr(parent,"play_audio_for_work")
            and any(item["available"] for item in library.list_audio_files(work_id=self.work["id"]))
        )
        mark_actions.addWidget(self.listen_button)
        saved_layout.addLayout(mark_actions)
        saved_layout.addWidget(QLabel("Notes and quotes for this work"))
        self.note_filter=QLineEdit()
        self.note_filter.setPlaceholderText("Search saved notes…")
        self.note_filter.textChanged.connect(self.refresh_notes)
        saved_layout.addWidget(self.note_filter)
        self.notes_list=QListWidget()
        self.notes_list.setWordWrap(True)
        self.notes_list.itemDoubleClicked.connect(self.edit_saved_note)
        saved_layout.addWidget(self.notes_list,stretch=2)
        note_actions=QGridLayout()
        for index,(title,callback) in enumerate((
            ("Edit",self.edit_saved_note),("Copy",self.copy_saved_note),
            ("Delete",self.delete_saved_note),("Export",self.export_work_notes)
        )):
            note_actions.addWidget(_action_button(title,callback),index//2,index%2)
        saved_layout.addLayout(note_actions)
        self.sidebar_tabs.addTab(saved_page,"Saved")
        links_page=QWidget()
        links_layout=QVBoxLayout(links_page)
        link_explanation=QLabel(
            "Link the current EPUB section with a specific audiobook time. "
            "No automatic chapter matching."
        )
        link_explanation.setWordWrap(True)
        links_layout.addWidget(link_explanation)
        self.audio_links_list=QListWidget()
        self.audio_links_list.itemDoubleClicked.connect(self.open_audio_text_link)
        links_layout.addWidget(self.audio_links_list,stretch=1)
        links_layout.addWidget(_action_button(
            "Save section ↔ time",self.add_audio_text_link
        ))
        link_actions=QHBoxLayout()
        link_actions.addWidget(_action_button(
            "Open saved link",self.open_audio_text_link
        ))
        link_actions.addWidget(_action_button(
            "Remove link",self.remove_audio_text_link
        ))
        links_layout.addLayout(link_actions)
        self.sidebar_tabs.addTab(links_page,"Audio links")
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
        reading_layout.addLayout(find_row)
        search_actions=QHBoxLayout()
        self.book_search_button=_action_button("Find in book",self.find_in_book)
        search_actions.addWidget(self.book_search_button)
        self.cancel_search_button=_action_button("Cancel",self.cancel_book_search)
        self.cancel_search_button.setEnabled(False)
        search_actions.addWidget(self.cancel_search_button)
        search_actions.addStretch()
        reading_layout.addLayout(search_actions)
        self.viewer=QTextBrowser()
        self.viewer.setOpenLinks(False)
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setReadOnly(True)
        self.viewer.setAcceptDrops(False)
        self.viewer.setUndoRedoEnabled(False)
        self.viewer.anchorClicked.connect(lambda _url:None)
        self.viewer.verticalScrollBar().valueChanged.connect(self.schedule_save)
        self.viewer.verticalScrollBar().valueChanged.connect(self.update_section_meter)
        reading_layout.addWidget(self.viewer,stretch=1)
        controls=QHBoxLayout()
        controls.addWidget(_action_button("◀ Previous",lambda:self.step(-1)))
        self.counter=QLabel()
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.counter,stretch=1)
        controls.addWidget(_action_button("Next ▶",lambda:self.step(1)))
        reading_layout.addLayout(controls)
        self.section_meter=QProgressBar()
        self.section_meter.setRange(0,100)
        self.section_meter.setValue(0)
        self.section_meter.setFormat("Scroll position in this section: %p% (approx.)")
        self.section_meter.setToolTip(
            "Approximate scroll position, NOT a reading milestone or completion percentage."
        )
        reading_layout.addWidget(self.section_meter)
        buttons=QGridLayout()
        buttons.addWidget(_action_button("Bookmark",self.add_bookmark),0,0)
        buttons.addWidget(_action_button("Save note / selected quote",self.save_note),0,1)
        buttons.addWidget(_action_button("Copy selection",self.copy_selection),1,0)
        buttons.addWidget(_action_button("Log reading progress…",self.log_progress),1,1)
        reading_layout.addLayout(buttons)
        self.splitter.addWidget(reading)
        self.splitter.setSizes([265,865])
        self.focus_button.setChecked(self.settings.value("reader_focus",False,type=bool))
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
        self.refresh_notes()
        self.refresh_audio_text_links()
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

    def _reader_preference(self,key,default,choices):
        value=str(self.settings.value(key,default))
        return value if value in choices else default

    def apply_theme(self,*_args):
        """Apply Qt rich-text formatting, not only widget stylesheet fonts."""
        name=self.theme.currentText()
        palettes={
            "Dark":("#141a24","#e5ebf6","#a6b5c8"),
            "Sepia":("#f2e5cc","#493a2d","#72553a"),
            "Light":("#ffffff","#212936","#546375"),
            "High contrast":("#000000","#ffffff","#ededed"),
        }
        background,foreground,subtle=palettes[name]
        ratio=self.scroll_ratio()
        self.viewer.setStyleSheet(
            f"QTextBrowser {{ background:{background}; color:{foreground};"
            f" border:1px solid {subtle}; }}"
        )
        doc=self.viewer.document()
        font=QFont({
            "Serif":"DejaVu Serif","Sans":"DejaVu Sans",
            "Mono":"DejaVu Sans Mono"
        }[self.font_family.currentText()])
        font.setPixelSize(self._font_size)
        doc.setDefaultFont(font)
        margin={"Narrow":10,"Comfortable":28,"Wide":55}[self.page_margin.currentText()]
        doc.setDocumentMargin(margin)
        spacing={"Compact":120,"Normal":150,"Relaxed":185}[self.line_spacing.currentText()]
        if not doc.isEmpty():
            cursor=QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            fmt=QTextBlockFormat()
            fmt.setLineHeight(spacing,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.mergeBlockFormat(fmt)
        self.settings.setValue("reader_theme",name)
        self.settings.setValue("reader_family",self.font_family.currentText())
        self.settings.setValue("reader_line_spacing",self.line_spacing.currentText())
        self.settings.setValue("reader_margin",self.page_margin.currentText())
        if not self._restoring:
            QTimer.singleShot(0,lambda:self.restore_scroll(ratio))

    def set_focus_mode(self,enabled):
        self.splitter.widget(0).setVisible(not enabled)
        self.focus_button.setText("Show sidebar" if enabled else "Focus reading")
        self.settings.setValue("reader_focus",bool(enabled))

    def update_section_meter(self,*_args):
        self.section_meter.setValue(round(self.scroll_ratio()/100))

    def copy_selection(self):
        value=self.viewer.textCursor().selectedText().replace("\u2029","\n").strip()
        if not value:
            self.message.setText("Select a passage in the reader to copy it.")
            return
        QApplication.clipboard().setText(value)
        self.message.setText("Copied selected passage to clipboard.")

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
        self.apply_theme()
        self.counter.setText(
            f"Spine section {index+1} / {len(self.book)}"
        )
        self.counter.setToolTip(content["title"])
        self.message.setText(
            "Reading position saves automatically. EPUB spine sections "
            "may not equal actual chapters or printed pages."
        )
        self._restoring=False
        QTimer.singleShot(0,lambda:self.restore_scroll(ratio))
        QTimer.singleShot(40,self.schedule_save)
        QTimer.singleShot(50,self.update_section_meter)

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
        self.sidebar_tabs.setCurrentIndex(1)

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
        old=self.marks.currentItem()
        selected=old.data(Qt.ItemDataRole.UserRole)["id"] if old else None
        self.marks.clear()
        for row in self.library.list_epub_bookmarks(self.file_id):
            item=QListWidgetItem(
                f"{row['section_index']+1}: {row['label']}"
            )
            item.setData(Qt.ItemDataRole.UserRole,row)
            self.marks.addItem(item)
            if row["id"]==selected:
                self.marks.setCurrentItem(item)

    def rename_bookmark(self):
        item=self.marks.currentItem()
        if not item:
            self.message.setText("Select a saved location bookmark to rename.")
            return
        row=item.data(Qt.ItemDataRole.UserRole)
        label,okay=QInputDialog.getText(
            self,"Rename EPUB bookmark","Label:",text=row["label"]
        )
        if not okay:
            return
        try:
            self.library.rename_epub_bookmark(row["id"],label)
            self.refresh_bookmarks()
            self.message.setText("Bookmark renamed without changing its position.")
        except ValueError as exc:
            QMessageBox.warning(self,"Rename bookmark",str(exc))

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
            self.sidebar_tabs.setCurrentIndex(2)
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

    def refresh_audio_text_links(self):
        from .audio_gui import fmt_duration
        from .audio_text_links import AudioTextAnchors

        self.audio_links_list.clear()
        self.audio_anchors=AudioTextAnchors(self.settings)
        files=self.library.list_audio_files(work_id=self.work["id"])
        valid=self.audio_anchors.list_links(
            self.work["id"],self.file_id,self.link["path"],files
        )
        by_id={row["id"]:row for row in files}
        for entry in valid:
            audio=by_id[entry["audio_id"]]
            title=entry["section_title"]
            item=QListWidgetItem(
                f"{title} ↔ {audio['filename']} at {fmt_duration(entry['position_ms'])}"
            )
            item.setData(Qt.ItemDataRole.UserRole,entry)
            self.audio_links_list.addItem(item)

    def add_audio_text_link(self):
        owner=self.parent()
        player=(getattr(getattr(owner,"audio_page",None),"_player_dialog",None)
                if owner is not None else None)
        if player is None or player.current["work_id"] != self.work["id"]:
            self.message.setText("Start an audiobook for this book before linking positions.")
            return
        if not player.current.get("available"):
            self.message.setText("Audio file is not available here.")
            return
        section=self.book.spine[self.index]
        try:
            self.audio_anchors.add(
                self.work["id"],self.file_id,self.link["path"],player.current,
                section_href=section.href,section_title=section.title,
                scroll_ratio=self.scroll_ratio(),position_ms=player.player.position(),
                audio_files=self.library.list_audio_files(work_id=self.work["id"])
            )
        except (ValueError,OSError) as exc:
            self.message.setText(str(exc))
            return
        self.refresh_audio_text_links()
        self.sidebar_tabs.setCurrentIndex(3)
        self.message.setText(
            "Saved an explicit local audio ↔ EPUB link. No reading progress was logged."
        )

    def _selected_audio_text_link(self):
        item=self.audio_links_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def open_audio_text_link(self,*_args):
        link=self._selected_audio_text_link()
        if not link:
            return
        # Revalidate file identities at click time, not only on list refresh.
        files=self.library.list_audio_files(work_id=self.work["id"])
        if not any(item["id"]==link["id"] for item in self.audio_anchors.list_links(
            self.work["id"],self.file_id,self.link["path"],files
        )):
            self.refresh_audio_text_links()
            self.message.setText("Linked file has changed or is unavailable.")
            return
        index=next(
            (i for i,section in enumerate(self.book.spine)
             if section.href==link["section_href"]),None
        )
        if index is None:
            self.message.setText("EPUB section no longer exists in this edition.")
            return
        self.load_section(index,int(link["scroll_ratio"]))
        owner=self.parent()
        if owner is not None and hasattr(owner,"seek_audio_text_link"):
            if owner.seek_audio_text_link(self.work["id"],link):
                self.message.setText("Opened explicitly linked EPUB and audio positions.")
            else:
                self.message.setText("EPUB position opened. Audio link unavailable.")

    def remove_audio_text_link(self):
        link=self._selected_audio_text_link()
        if not link:
            return
        if QMessageBox.question(
            self,"Remove position link?","Remove this EPUB/audio timestamp pairing?"
        )!=QMessageBox.StandardButton.Yes:
            return
        self.audio_anchors.remove(self.work["id"],self.file_id,link["id"])
        self.refresh_audio_text_links()

    def listen_to_work(self):
        owner=self.parent()
        if owner is not None and hasattr(owner,"play_audio_for_work"):
            owner.play_audio_for_work(self.work["id"])

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
            self.refresh_notes()
            self.sidebar_tabs.setCurrentIndex(2)
            self.message.setText("Saved in BookTracker Notes & quotes.")
        except ValueError as exc:
            QMessageBox.warning(self,"Reading note",str(exc))

    def refresh_notes(self,*_args):
        previous=self.notes_list.currentItem()
        selected=previous.data(Qt.ItemDataRole.UserRole) if previous else None
        self.notes_list.clear()
        entries=self.library.list_annotations(
            work_id=self.work["id"],search=self.note_filter.text(),limit=2500
        )
        for row in entries:
            preview=" ".join(row["body"].split())[:115]
            label=row["kind"].capitalize()
            if row["body"].startswith("EPUB · "):
                label+=" · From reader"
            item=QListWidgetItem(f"{label} — {preview}")
            item.setToolTip(row["body"][:1500])
            item.setData(Qt.ItemDataRole.UserRole,row["id"])
            self.notes_list.addItem(item)
            if row["id"]==selected:
                self.notes_list.setCurrentItem(item)

    def selected_note(self):
        item=self.notes_list.currentItem()
        return self.library.get_annotation(
            item.data(Qt.ItemDataRole.UserRole)
        ) if item else None

    def edit_saved_note(self,*_args):
        row=self.selected_note()
        if not row:
            self.message.setText("Select a saved quote or note to edit.")
            return
        body,okay=QInputDialog.getMultiLineText(
            self,"Edit saved annotation","Annotation text:",row["body"]
        )
        if not okay:
            return
        try:
            self.library.update_annotation(
                row["id"],kind=row["kind"],body=body,
                location=row["location"],location_unit=row["location_unit"]
            )
            self.refresh_notes()
            self.message.setText("Saved annotation changes.")
        except ValueError as exc:
            QMessageBox.warning(self,"Edit annotation",str(exc))

    def copy_saved_note(self):
        row=self.selected_note()
        if not row:
            self.message.setText("Select a saved quote or note to copy.")
            return
        QApplication.clipboard().setText(row["body"])
        self.message.setText("Copied saved annotation.")

    def delete_saved_note(self):
        row=self.selected_note()
        if not row:
            self.message.setText("Select a saved quote or note to delete.")
            return
        if QMessageBox.question(
            self,"Delete saved annotation?",
            "Delete this note or quote? This does not delete the book."
        )!=QMessageBox.StandardButton.Yes:
            return
        self.library.delete_annotation(row["id"])
        self.refresh_notes()
        self.message.setText("Deleted the selected annotation.")

    def export_work_notes(self):
        """Export only this work's notes to an atomically-written Markdown file."""
        rows=self.library.list_annotations(work_id=self.work["id"],limit=5000)
        if not rows:
            self.message.setText("No saved notes or quotes to export.")
            return
        file_path,_=QFileDialog.getSaveFileName(
            self,"Export this work's notes","BookTracker-work-notes.md",
            "Markdown files (*.md)"
        )
        if not file_path:
            return
        title=self.work["title"].replace("\n"," ")
        author=self.work["author"].replace("\n"," ")
        parts=[f"# Notes and quotes — {title}\n\n*{author}*\n\n"]
        total=len(parts[0])
        for row in reversed(rows):
            heading=row["kind"].capitalize()
            if row["location"] is not None:
                desc=("EPUB section" if row["body"].startswith("EPUB · ") else
                      row["location_unit"].replace("_"," ") or "Position")
                heading+=f" · {desc} {row['location']}"
            piece=f"## {heading}\n\n{row['body']}\n\n"
            total+=len(piece)
            if total>12_000_000:
                QMessageBox.warning(self,"Export too large",
                                    "Export exceeds the 12 million character limit.")
                return
            parts.append(piece)
        try:
            data="".join(parts).encode("utf-8")
            output=QSaveFile(file_path)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(data)!=len(data) or not output.commit():
                raise OSError(output.errorString())
            self.message.setText(
                f"Exported {len(rows)} work annotations as Markdown."
            )
        except OSError as exc:
            QMessageBox.warning(self,"Export notes",str(exc))

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
