"""PySide6 desktop interface. All user data stays in the local SQLite database."""
from __future__ import annotations

import csv
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QElapsedTimer, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
    QTextCharFormat,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .audio_gui import AudioLibraryPage
from .backups import automatic_backup
from .book_search import _normalize, cached_book_search
from .clippings import group_clippings, read_clippings
from .covers import CoverCache, cover_url_for
from .database import KINDS, PUBLICATION, STATUSES, UNITS, Library, _book_identity
from .epub_import import read_epub_metadata
from .integrations import (
    export_file,
    fetch_saved_page_metadata,
    fetch_url_metadata,
    import_file,
    import_goodreads,
    source_from_url,
)
from .metadata import missing_book_metadata
from .reader_gui import EpubReader
from .sync_gui import SyncPage

LABELS = {
    "book": "Book", "fanfiction": "Fanfiction", "web_novel": "Web novel", "other": "Other",
    "plan_to_read": "Plan to read", "reading": "Reading", "caught_up": "Caught up",
    "completed": "Completed", "paused": "Paused", "dropped": "Dropped",
    "unknown": "Unknown", "ongoing": "Ongoing", "hiatus": "Hiatus",
    "chapters": "Chapters", "pages": "Pages", "percent": "Percent",
    "kindle_location": "Kindle location",
}
BG = "#10151f"
PANEL = "#192231"
MUTED = "#9ba9bf"
ACCENT = "#7bb6ff"
STYLE = """
QWidget { color: #e7eef9; font-family: 'Inter', 'Noto Sans', sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #10151f; }
QWidget#editorPage { background: #192231; }
QTabWidget::pane { background: #192231; border: 1px solid #34445a; border-radius: 7px; }
QFrame#side, QFrame#details, QFrame#statCard { background: #192231; border-radius: 12px; }
QFrame#onlinePreview { background: #202c3c; border: 1px solid #34445a; border-radius: 10px; }
QLabel#onlineCover { background: #2b3a4d; border-radius: 6px; color: #9ba9bf; }
QListWidget#onlineResults::item { padding: 9px; }
QLabel#headline { font-size: 25px; font-weight: 700; }
QLabel#subheading { font-size: 16px; font-weight: 650; }
QLabel#muted { color: #9ba9bf; }
QLabel#value { color: #7bb6ff; font-size: 26px; font-weight: 700; }
QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QCalendarWidget,
QTableWidget, QListWidget {
 background: #202c3c; border: 1px solid #34445a; border-radius: 7px;
 padding: 7px; selection-background-color: #305c8f;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {
 border: 1px solid #7bb6ff;
}
QPushButton { background: #2b3d54; border: 1px solid #38506a; border-radius: 8px;
 padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #3b5575; }
QPushButton:disabled { color: #8290a4; background: #222b37; }
QPushButton#primary { background: #3274c2; border: 1px solid #3274c2; color: #ffffff; }
QPushButton#primary:hover { background: #468adb; }
QPushButton#danger { background: #613540; border: 1px solid #85505a; }
QProgressBar { background: #202c3c; color: #e7eef9; text-align: center;
  border: 1px solid #34445a; border-radius: 6px; min-height: 18px; }
QProgressBar::chunk { background: #3274c2; border-radius: 5px; }
QPushButton#nav { border: none; text-align: left; background: transparent; padding: 12px; }
QPushButton#nav:checked { background: #294665; color: #baddff; }
QTableWidget { gridline-color: transparent; background: #192231; padding: 0px; border: none; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #2a394c; }
QHeaderView::section { background: #233146; border: none; border-bottom: 1px solid #3a4e67;
 padding: 12px; font-weight: 600; }
QScrollBar:vertical { background: #172030; width: 10px; }
QScrollBar::handle:vertical { background: #40516a; border-radius: 5px; }
QTabBar::tab { padding: 10px 14px; background: #253449; border-radius: 5px; margin-right: 4px; }
QTabBar::tab:selected { background: #356ca3; }
QSplitter::handle { background: #10151f; }
QMessageBox { background: #192231; }
"""


def name(value):
    return LABELS.get(value, str(value).replace("_", " ").title())


def local_timestamp(value):
    """Display UTC-offset timestamps in the computer's local timezone."""
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return (parsed.strftime("%Y-%m-%d %H:%M")
            if "T" in text or " " in text else parsed.date().isoformat())


def label(text, style=""):
    item = QLabel(text)
    if style:
        item.setObjectName(style)
    item.setWordWrap(True)
    return item


def button(text, callback, primary=False, danger=False):
    result = QPushButton(text)
    result.setMinimumHeight(34)
    if primary:
        result.setObjectName("primary")
    elif danger:
        result.setObjectName("danger")
    result.clicked.connect(callback)
    return result


def placeholder_cover(title):
    pixmap = QPixmap(116, 164)
    pixmap.fill(QColor('#263a55'))
    painter = QPainter(pixmap)
    painter.setPen(QColor('#e7eef9'))
    font = QFont('Noto Sans', 10, QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.drawText(10, 12, 96, 140, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, title)
    painter.end()
    return pixmap


def fail(parent, message):
    QMessageBox.critical(parent, "BookTracker", str(message))


class LookupThread(QThread):
    found = Signal(object)
    failed = Signal(str)

    def __init__(self, func, *args):
        super().__init__()
        self.func = func
        self.args = args

    def run(self):
        try:
            self.found.emit(self.func(*self.args))
        except Exception as exc:
            self.failed.emit(str(exc))


class SerialCheckThread(QThread):
    """Sequential, user-requested supported-source checks; never DB in worker."""

    result = Signal(str, object)
    failed = Signal(str, str)
    status = Signal(int, int, str)

    def __init__(self, entries, fetch=fetch_url_metadata):
        super().__init__()
        self.entries = tuple(entries)
        self.fetch = fetch

    def run(self):
        total = len(self.entries)
        for index, entry in enumerate(self.entries):
            if self.isInterruptionRequested():
                break
            if index:
                # Single worker + spacing avoids provider request bursts.
                for _ in range(15):
                    if self.isInterruptionRequested():
                        break
                    self.msleep(100)
            if self.isInterruptionRequested():
                break
            self.status.emit(index + 1, total, entry["title"])
            try:
                data = self.fetch(entry["url"])
                self.result.emit(entry["source_id"], data)
            except Exception as exc:
                self.failed.emit(entry["source_id"], str(exc)[:500])


class WorkEditor(QDialog):
    def __init__(self, parent=None, work=None):
        super().__init__(parent)
        self.setWindowTitle("Edit work" if work else "Add a work")
        self.resize(895, 640)
        self.setMinimumSize(725, 520)
        self.work = work or {}
        self.source_id = self.work.get("source_id", "")
        self.cover_url = self.work.get("cover_url", "")
        self.lookup_thread = None
        self.online_books = []
        self.bulk_selection = []
        self.bulk_status = "completed"
        self.bulk_update_existing = False
        self.cover_network = QNetworkAccessManager(self)
        self.cover_request_token = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        intro = label("Add a story or book" if not work else "Edit book details", "subheading")
        layout.addWidget(intro)
        tabs = QTabWidget()
        self.tabs = tabs
        layout.addWidget(tabs, stretch=1)

        online_page, bulk_page, basic, extras, notes_page = (
            QWidget(), QWidget(), QWidget(), QWidget(), QWidget()
        )
        for page in (online_page, bulk_page, basic, extras, notes_page):
            page.setObjectName("editorPage")
        tabs.addTab(online_page, "Find online")
        tabs.addTab(bulk_page, "Paste titles")
        tabs.setTabEnabled(1, not bool(work))
        tabs.addTab(basic, "Book & source")
        tabs.addTab(extras, "More details")
        tabs.addTab(notes_page, "Notes & tags")
        self.build_online_tab(online_page)
        self.build_bulk_titles_tab(bulk_page)
        if work:
            tabs.setCurrentWidget(basic)
        form = QFormLayout(basic)
        form.setSpacing(10)
        more = QFormLayout(extras)
        more.setSpacing(10)

        self.source_url = QLineEdit(self.work.get("source_url", ""))
        self.source_url.setPlaceholderText("Paste a story link, or leave blank for manual entries")
        self.title = QLineEdit(self.work.get("title", ""))
        self.title.setPlaceholderText("Title (required)")
        self.author = QLineEdit(self.work.get("author", ""))
        self.kind = self.combo(KINDS, self.work.get("kind", "book"))
        self.status = self.combo(STATUSES, self.work.get("status", "plan_to_read"))
        self.publication = self.combo(PUBLICATION, self.work.get("publication_status", "unknown"))
        self.unit = self.combo(UNITS, self.work.get(
            "progress_unit", "pages" if not work else "chapters"
        ))
        self.kind.currentIndexChanged.connect(self.work_type_changed)
        self.total = QSpinBox()
        self.total.setRange(0, 10_000_000)
        self.total.setValue(self.work.get("total_units", 0))
        self.words = QSpinBox()
        self.words.setRange(0, 2_000_000_000)
        self.words.setValue(self.work.get("word_count", 0))
        self.rating = QDoubleSpinBox()
        self.rating.setRange(0, 5)
        self.rating.setSingleStep(0.5)
        self.rating.setDecimals(1)
        self.rating.setSpecialValueText("Unrated")
        self.rating.setValue(self.work.get("rating") or 0)
        self.fandom = QLineEdit(self.work.get("fandom", ""))
        self.series = QLineEdit(self.work.get("series", ""))
        self.source_site = QLineEdit(self.work.get("source_site", ""))
        self.isbn = QLineEdit(self.work.get("isbn", ""))
        self.language = QLineEdit(self.work.get("language", ""))
        self.favorite = QCheckBox("Favorite")
        self.favorite.setChecked(bool(self.work.get("favorite")))
        self.published = QLineEdit(self.work.get("published_at", ""))
        self.published.setPlaceholderText("YYYY or YYYY-MM-DD")
        self.started = QLineEdit(self.work.get("started_at", ""))
        self.started.setPlaceholderText("Optional date: YYYY-MM-DD")
        self.finished = QLineEdit(self.work.get("finished_at", ""))
        self.finished.setPlaceholderText("Optional date: YYYY-MM-DD")

        form.addRow("Story/book URL", self.source_url)
        lookups = QHBoxLayout()
        lookups.setSpacing(7)
        self.url_button = button("Fetch from URL", self.lookup_url)
        self.saved_button = button("Import saved page…", self.import_saved_page)
        self.search_button = button("Find books online", self.lookup_books)
        lookups.addWidget(self.url_button)
        lookups.addWidget(self.saved_button)
        lookups.addWidget(self.search_button)
        form.addRow("Auto-fill", lookups)
        self.epub_button = button("Read metadata from EPUB…", self.import_epub)
        form.addRow("Offline file", self.epub_button)
        for heading, control in (
            ("Title", self.title),
            ("Author", self.author),
            ("Type", self.kind),
            ("Reading status", self.status),
            ("Progress unit", self.unit),
            ("Published chapters/pages", self.total),
            ("", self.favorite),
        ):
            form.addRow(heading, control)

        for heading, control in (
            ("Publication status", self.publication),
            ("Word count", self.words),
            ("Rating", self.rating),
            ("Fandom", self.fandom),
            ("Series", self.series),
            ("Source website", self.source_site),
            ("ISBN", self.isbn),
            ("Language", self.language),
            ("Published date", self.published),
            ("Started date", self.started),
            ("Finished date", self.finished),
        ):
            more.addRow(heading, control)

        note_form = QFormLayout(notes_page)
        note_form.setSpacing(10)
        self.tags = QTextEdit("\n".join(self.work.get("tags") or []))
        self.tags.setPlaceholderText("One tag per line (commas within tags are preserved)")
        self.tags.setMinimumHeight(105)
        self.notes = QTextEdit(self.work.get("notes", ""))
        self.notes.setMinimumHeight(140)
        self.description = QTextEdit(self.work.get("description", ""))
        self.description.setMinimumHeight(140)
        note_form.addRow("Tags", self.tags)
        note_form.addRow("Personal notes", self.notes)
        note_form.addRow("Description", self.description)

        self.feedback = label(
            "Paste a URL to identify its source. If a site blocks automatic access, "
            "save the page from your browser and choose Import saved page.", "muted"
        )
        layout.addWidget(self.feedback)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.validate_and_accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)
        self.source_url.textChanged.connect(self.source_changed)
        if not work:
            self.online_query.setFocus()

    def build_bulk_titles_tab(self, page):
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(label("Add many books without searching them individually", "subheading"))
        layout.addWidget(label(
            "Paste one title per line and enter its author once. The entries will "
            "be added as custom books; you can fill missing metadata later. "
            "This also works when catalog websites are unavailable.", "muted"
        ))
        row = QHBoxLayout()
        row.addWidget(label("Author:"))
        self.pasted_author = QLineEdit()
        self.pasted_author.setPlaceholderText("e.g. Brandon Sanderson")
        row.addWidget(self.pasted_author, stretch=1)
        layout.addLayout(row)
        layout.addWidget(label("Book titles (one per line):"))
        self.pasted_titles = QTextEdit()
        self.pasted_titles.setPlaceholderText(
            "The Final Empire\nThe Well of Ascension\nThe Hero of Ages\n"
            "The Way of Kings\nWords of Radiance\nOathbringer"
        )
        layout.addWidget(self.pasted_titles, stretch=1)
        options = QHBoxLayout()
        options.addWidget(label("Reading status:"))
        self.pasted_status = QComboBox()
        self.pasted_status.addItem("Completed (date unknown)", "completed")
        self.pasted_status.addItem("Plan to read", "plan_to_read")
        options.addWidget(self.pasted_status)
        options.addStretch()
        options.addWidget(button("Add all pasted titles", self.add_pasted_titles, primary=True))
        layout.addLayout(options)
        self.pasted_update_checkbox = QCheckBox(
            "Also mark matching existing unread books completed"
        )
        layout.addWidget(self.pasted_update_checkbox)
        self.pasted_status.currentIndexChanged.connect(
            lambda: self.pasted_update_checkbox.setEnabled(
                self.pasted_status.currentData() == "completed"
            )
        )
        self.pasted_status_note = label(
            "Duplicate titles by the same author are automatically skipped. "
            "No completion dates will be invented.", "muted"
        )
        layout.addWidget(self.pasted_status_note)

    def add_pasted_titles(self):
        author = self.pasted_author.text().strip()
        if not author:
            self.pasted_status_note.setText("Enter an author before adding multiple titles.")
            return
        import re

        raw_lines = self.pasted_titles.toPlainText().splitlines()
        titles = [
            re.sub(r"^\s*(?:[-*•]\s*|\d+[).]\s*)", "", line).strip()
            for line in raw_lines
            if line.strip()
        ]
        titles = list(dict.fromkeys(title for title in titles if title))
        if not titles:
            self.pasted_status_note.setText("Paste at least one book title (one per line).")
            return
        if len(titles) > 500:
            self.pasted_status_note.setText("Please add no more than 500 books at once.")
            return
        self.bulk_selection = [
            {"title": title, "author": author, "kind": "book",
             "progress_unit": "pages", "publication_status": "unknown"}
            for title in titles
        ]
        self.bulk_status = self.pasted_status.currentData()
        self.bulk_update_existing = (
            self.pasted_update_checkbox.isChecked() and self.bulk_status == "completed"
        )
        self.accept()

    def build_online_tab(self, page):
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(label(
            "Find traditional books by title, author, or ISBN. Compare catalog entries "
            "before adding one to your reading library.", "muted"
        ))

        search_bar = QHBoxLayout()
        self.online_mode = QComboBox()
        self.online_mode.addItem("Title / ISBN", "title")
        self.online_mode.addItem("Author", "author")
        self.online_mode.currentIndexChanged.connect(self.online_mode_changed)
        search_bar.addWidget(self.online_mode)
        self.online_query = QLineEdit()
        self.online_query.setPlaceholderText("Search by title or ISBN")
        self.online_query.returnPressed.connect(self.search_online)
        self.online_search_button = button("Search books", self.search_online, primary=True)
        search_bar.addWidget(self.online_query, stretch=1)
        search_bar.addWidget(self.online_search_button)
        layout.addLayout(search_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.online_results = QListWidget()
        self.online_results.setObjectName("onlineResults")
        self.online_results.setSpacing(3)
        self.online_results.setWordWrap(True)
        self.online_results.itemChanged.connect(self.selection_count_changed)
        self.online_results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.online_results.currentRowChanged.connect(self.preview_online_result)
        self.online_results.itemDoubleClicked.connect(lambda _: self.use_online_result())
        splitter.addWidget(self.online_results)

        preview = QFrame()
        preview.setObjectName("onlinePreview")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(14, 10, 14, 10)
        preview_layout.setSpacing(9)
        cover_and_info = QHBoxLayout()
        cover_and_info.setSpacing(12)
        self.online_cover = QLabel("No cover")
        self.online_cover.setObjectName("onlineCover")
        self.online_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.online_cover.setFixedSize(96, 140)
        cover_and_info.addWidget(self.online_cover)
        headings = QVBoxLayout()
        self.online_title = label("Search for a book to see its details", "subheading")
        self.online_meta = label("Results from Open Library and Google Books", "muted")
        headings.addWidget(self.online_title)
        headings.addWidget(self.online_meta)
        headings.addStretch()
        cover_and_info.addLayout(headings, stretch=1)
        preview_layout.addLayout(cover_and_info)
        preview_layout.addWidget(label("Description", "muted"))
        self.online_description = QTextEdit()
        self.online_description.setReadOnly(True)
        self.online_description.setPlaceholderText("Descriptions appear here when a catalog provides one.")
        preview_layout.addWidget(self.online_description, stretch=1)
        preview_actions = QHBoxLayout()
        self.online_use_button = button("Review details", self.use_online_result)
        self.online_add_button = button("Add to library", self.add_online_result, primary=True)
        self.online_use_button.setEnabled(False)
        self.online_add_button.setEnabled(False)
        preview_actions.addWidget(self.online_use_button)
        preview_actions.addWidget(self.online_add_button)
        preview_layout.addLayout(preview_actions)
        splitter.addWidget(preview)
        splitter.setSizes([330, 455])
        layout.addWidget(splitter, stretch=1)
        bulk_row = QHBoxLayout()
        bulk_row.addWidget(button("Select all results", self.select_all_books))
        bulk_row.addWidget(button("Clear selection", self.clear_selected_books))
        self.bulk_counter = label("0 selected", "muted")
        bulk_row.addWidget(self.bulk_counter)
        bulk_row.addStretch()
        bulk_row.addWidget(label("Set reading status:", "muted"))
        self.bulk_status_select = QComboBox()
        self.bulk_status_select.addItem("Completed (date unknown)", "completed")
        self.bulk_status_select.addItem("Plan to read", "plan_to_read")
        bulk_row.addWidget(self.bulk_status_select)
        self.bulk_add_button = button("Add 0 selected", self.add_selected_books, primary=True)
        self.bulk_add_button.setEnabled(False)
        bulk_row.addWidget(self.bulk_add_button)
        layout.addLayout(bulk_row)
        self.bulk_update_checkbox = QCheckBox(
            "Also mark matching existing unread books completed"
        )
        layout.addWidget(self.bulk_update_checkbox)
        self.bulk_status_select.currentIndexChanged.connect(
            lambda: self.bulk_update_checkbox.setEnabled(
                self.bulk_status_select.currentData() == "completed"
            )
        )

        self.online_status = label(
            "Search two public catalogs. You can still enter a custom work using the Book & source tab.",
            "muted",
        )
        layout.addWidget(self.online_status)

    def online_mode_changed(self, _index):
        is_author = self.online_mode.currentData() == "author"
        self.online_query.setPlaceholderText(
            "e.g. Brandon Sanderson — select multiple works"
            if is_author else "e.g. The Way of Kings or an ISBN"
        )

    def selection_count_changed(self, _item):
        count = sum(self.online_results.item(i).checkState() == Qt.CheckState.Checked
                    for i in range(self.online_results.count()))
        self.bulk_counter.setText(f"{count} selected")
        self.bulk_add_button.setText(f"Add {count} selected")
        self.bulk_add_button.setEnabled(count > 0)

    def select_all_books(self):
        for i in range(self.online_results.count()):
            self.online_results.item(i).setCheckState(Qt.CheckState.Checked)

    def clear_selected_books(self):
        for i in range(self.online_results.count()):
            self.online_results.item(i).setCheckState(Qt.CheckState.Unchecked)

    def add_selected_books(self):
        chosen = [
            dict(self.online_books[i])
            for i in range(self.online_results.count())
            if self.online_results.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not chosen:
            self.online_status.setText("Check at least one book to add.")
            return
        self.bulk_selection = chosen
        self.bulk_status = self.bulk_status_select.currentData()
        self.bulk_update_existing = (
            self.bulk_update_checkbox.isChecked() and self.bulk_status == "completed"
        )
        self.accept()

    def search_online(self):
        query = self.online_query.text().strip()
        if not query:
            self.online_status.setText("Enter a book title, author, or ISBN to search.")
            self.online_query.setFocus()
            return
        if self.lookup_thread and self.lookup_thread.isRunning():
            self.online_status.setText("A search or metadata lookup is already running.")
            return
        self.online_books = []
        self.online_results.clear()
        self.selection_count_changed(None)
        self.online_use_button.setEnabled(False)
        self.online_add_button.setEnabled(False)
        self.online_description.clear()
        self.online_title.setText("Searching…")
        self.online_meta.setText("")
        self.online_cover.setPixmap(QPixmap())
        self.online_cover.setText("No cover")
        mode = self.online_mode.currentData()
        self.run_lookup(
            lambda term: cached_book_search(term, mode=mode),
            query, self.show_online_results,
        )
        self.online_status.setText(f"Searching {mode} catalogs for “{query}”…")

    def show_online_results(self, response):
        results = response.get("results", [])
        errors = response.get("errors", [])
        if self.online_mode.currentData() == "author":
            # Work-centric list: don't show the same novel twice just because
            # Google Books and Open Library list different editions.
            deduped = {}
            for work in results:
                key = (_normalize(work.get("title", "")), _normalize(work.get("author", "")))
                if not all(key):
                    continue
                previous = deduped.get(key)
                def quality(entry):
                    return (bool(entry.get("cover_url")), bool(entry.get("total_units")),
                            bool(entry.get("description")), len(entry.get("tags") or []))
                if not previous or quality(work) > quality(previous):
                    deduped[key] = work
            results = sorted(deduped.values(), key=lambda item: _normalize(item["title"]))

        self.online_books = results
        self.online_results.clear()
        for book in results:
            published = str(book.get("published_at") or "").strip()
            year = published[:4] if published else "Year unknown"
            subtitle = "  ·  ".join(
                x for x in (book.get("author") or "Unknown author",
                            year, book.get("source_site", "")) if x
            )
            pages = book.get("total_units") or 0
            if pages:
                subtitle += f"  ·  {pages:,} pages"
            item = QListWidgetItem(f"{book.get('title', '')}\n{subtitle}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, book.get("source_id"))
            self.online_results.addItem(item)
        self.selection_count_changed(None)
        if results:
            self.online_results.setCurrentRow(0)
        else:
            self.online_title.setText("No matching books found")
            self.online_meta.setText("Try a shorter title, an author, or an ISBN.")
            self.online_cover.setText("No cover")
        warnings = " | ".join(errors)
        message = f"{len(results)} unique book results" if self.online_mode.currentData() == "author" else f"{len(results)} book results"
        if response.get("cached"):
            message += " · Loaded from local search cache"
        if warnings:
            message += f" · Some sources unavailable: {warnings}"
        self.online_status.setText(message)
        self.feedback.setText(
            "Select a catalog result and choose Add to library, or Review details "
            "to adjust reading status and other fields."
        )

    def preview_online_result(self, index):
        if not 0 <= index < len(self.online_books):
            self.online_use_button.setEnabled(False)
            self.online_add_button.setEnabled(False)
            return
        book = self.online_books[index]
        self.online_use_button.setEnabled(True)
        self.online_add_button.setEnabled(True)
        self.online_title.setText(book.get("title") or "Untitled")
        details = [
            book.get("author") or "Unknown author",
            book.get("source_site") or "Unknown catalog",
        ]
        if book.get("published_at"):
            details.append(f"Published: {book['published_at']}")
        if book.get("total_units"):
            details.append(f"{book['total_units']:,} pages")
        if book.get("isbn"):
            details.append(f"ISBN: {book['isbn']}")
        if book.get("publisher"):
            details.append(book["publisher"])
        self.online_meta.setText("\n".join(details))
        self.online_description.setPlainText(
            book.get("description") or "No description available for this catalog entry."
        )
        self.load_online_cover(book.get("cover_url") or "")

    def load_online_cover(self, url):
        self.cover_request_token += 1
        token = self.cover_request_token
        self.online_cover.setPixmap(QPixmap())
        self.online_cover.setText("No cover")
        parsed = QUrl(url)
        if (parsed.scheme() != "https" or parsed.host() not in
                ("covers.openlibrary.org", "books.google.com", "books.googleusercontent.com")):
            return
        request = QNetworkRequest(parsed)
        request.setTransferTimeout(8000)
        reply = self.cover_network.get(request)

        def handle_reply():
            if token == self.cover_request_token and reply.error() == QNetworkReply.NetworkError.NoError:
                data = bytes(reply.readAll())
                if len(data) <= 2_000_000:
                    pixmap = QPixmap()
                    if pixmap.loadFromData(data):
                        self.online_cover.setText("")
                        self.online_cover.setPixmap(pixmap.scaled(
                            96, 140, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        ))
            reply.deleteLater()

        reply.finished.connect(handle_reply)

    def use_online_result(self):
        index = self.online_results.currentRow()
        if not 0 <= index < len(self.online_books):
            return
        self.fill_metadata(self.online_books[index])
        self.tabs.setCurrentIndex(2)
        self.feedback.setText(
            "Book details filled from the selected catalog entry. "
            "Choose a reading status or edit anything before saving."
        )

    def add_online_result(self):
        index = self.online_results.currentRow()
        if not 0 <= index < len(self.online_books):
            return
        self.fill_metadata(self.online_books[index])
        self.validate_and_accept()

    @staticmethod
    def combo(choices, selected):
        widget = QComboBox()
        for item in choices:
            widget.addItem(name(item), item)
        index = widget.findData(selected)
        widget.setCurrentIndex(max(0, index))
        return widget

    def values(self):
        # Every saved recognized URL gets a stable site/work ID even if
        # the metadata request timed out or was blocked by Cloudflare.
        url = self.source_url.text().strip()
        try:
            source = source_from_url(url)
        except ValueError:
            source = {}
        return {
            "title": self.title.text().strip(),
            "author": self.author.text().strip(),
            "kind": self.kind.currentData(), "status": self.status.currentData(),
            "publication_status": self.publication.currentData(),
            "progress_unit": self.unit.currentData(),
            "total_units": self.total.value(), "word_count": self.words.value(),
            "rating": self.rating.value() or None,
            "fandom": self.fandom.text().strip(), "series": self.series.text().strip(),
            "source_site": source.get("source_site") or self.source_site.text().strip(),
            "source_url": url,
            "source_id": source.get("source_id") or self.source_id,
            "cover_url": self.cover_url,
            "language": self.language.text().strip(),
            "isbn": self.isbn.text().strip(), "favorite": self.favorite.isChecked(),
            "published_at": self.published.text().strip(),
            "started_at": self.started.text().strip(), "finished_at": self.finished.text().strip(),
            "tags": [t.strip() for t in self.tags.toPlainText().splitlines() if t.strip()],
            "notes": self.notes.toPlainText().strip(),
            "description": self.description.toPlainText().strip(),
        }

    def validate_and_accept(self):
        if not self.title.text().strip():
            QMessageBox.warning(self, "Title required", "Please enter a title.")
            self.title.setFocus()
            return
        self.accept()

    def source_changed(self, url):
        self.source_id = ""
        if url.strip() == self.work.get("source_url"):
            self.source_id = self.work.get("source_id", "")
        try:
            info = source_from_url(url)
        except ValueError:
            return
        self.source_id = info["source_id"]
        self.source_site.setText(info["source_site"])
        ix = self.kind.findData(info["kind"])
        if ix >= 0:
            self.kind.setCurrentIndex(ix)
        self.feedback.setText(
            f"Recognized {info['source_site']} work #{info['source_id']}. "
            "You can save with a manual title if the lookup is unavailable."
        )

    def fill_metadata(self, info):
        for attr, key in ((self.title, "title"), (self.author, "author"),
                          (self.fandom, "fandom"), (self.series, "series"),
                          (self.source_site, "source_site"),
                          (self.source_url, "source_url"), (self.isbn, "isbn"),
                          (self.published, "published_at"), (self.language, "language")):
            if info.get(key):
                attr.setText(str(info[key]))
        if info.get("source_id"):
            self.source_id = info["source_id"]
        if info.get("cover_url"):
            self.cover_url = info["cover_url"]
        if info.get("description"):
            self.description.setPlainText(info["description"])
        if info.get("tags"):
            current = [t.strip() for t in self.tags.toPlainText().splitlines() if t.strip()]
            self.tags.setPlainText("\n".join(dict.fromkeys(current + info["tags"])))
        for attr, key in ((self.kind, "kind"), (self.unit, "progress_unit"),
                          (self.publication, "publication_status")):
            ix = attr.findData(info.get(key))
            if ix >= 0:
                attr.setCurrentIndex(ix)
        if info.get("total_units"):
            self.total.setValue(info["total_units"])
        if info.get("word_count"):
            self.words.setValue(info["word_count"])
        self.feedback.setText("Metadata loaded. Check the details and save when ready.")

    def run_lookup(self, func, arg, callback):
        if self.lookup_thread and self.lookup_thread.isRunning():
            return
        self.url_button.setEnabled(False)
        self.saved_button.setEnabled(False)
        self.search_button.setEnabled(False)
        self.online_search_button.setEnabled(False)
        self.feedback.setText("Looking up metadata…")
        thread = LookupThread(func, arg)
        self.lookup_thread = thread
        thread.found.connect(callback)
        thread.failed.connect(self.lookup_failed)
        thread.finished.connect(lambda: self.url_button.setEnabled(True))
        thread.finished.connect(lambda: self.saved_button.setEnabled(True))
        thread.finished.connect(lambda: self.search_button.setEnabled(True))
        thread.finished.connect(lambda: self.online_search_button.setEnabled(True))
        thread.start()

    def lookup_failed(self, message):
        self.feedback.setText("Lookup failed: " + message)
        self.online_status.setText("Search failed: " + message)
        if self.tabs.currentIndex() == 0:
            self.online_title.setText("Could not search the catalogs")

    def lookup_url(self):
        url = self.source_url.text().strip()
        if not url:
            QMessageBox.information(self, "Paste a link", "Enter a story URL first.")
            return
        try:
            source_from_url(url)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.run_lookup(fetch_url_metadata, url, self.fill_metadata)

    def work_type_changed(self, _index):
        if self.work:
            return
        requested = "pages" if self.kind.currentData() == "book" else "chapters"
        selected = self.unit.findData(requested)
        if selected >= 0:
            self.unit.setCurrentIndex(selected)

    def import_epub(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an EPUB", "", "EPUB ebooks (*.epub)"
        )
        if not path:
            return
        try:
            parent = self.parent()
            folder = (parent.library.path.parent / "imported_covers"
                      if parent and hasattr(parent, "library") else None)
            info = read_epub_metadata(path, cover_dir=folder)
            self.fill_metadata(info)
            self.tabs.setCurrentIndex(2)
            self.feedback.setText(
                "EPUB metadata loaded locally. Review the fields, then save."
            )
        except (OSError, ValueError) as exc:
            self.feedback.setText(f"EPUB import failed: {exc}")

    def import_saved_page(self):
        url = self.source_url.text().strip()
        if not url:
            QMessageBox.information(
                self, "Story URL required", "Paste the original story URL first, "
                "then select the HTML file saved from your browser."
            )
            return
        try:
            source_from_url(url)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select saved story page", "", "HTML pages (*.html *.htm)"
        )
        if not path:
            return
        try:
            self.fill_metadata(fetch_saved_page_metadata(path, url))
        except (OSError, ValueError) as exc:
            self.feedback.setText(f"Saved page import failed: {exc}")

    def lookup_books(self):
        query = self.title.text().strip() or self.isbn.text().strip()
        if query:
            self.online_query.setText(query)
        self.tabs.setCurrentIndex(0)
        if self.online_query.text().strip():
            self.search_online()
        else:
            self.online_query.setFocus()

    def done(self, code):
        # Accept/reject hides a QDialog without necessarily calling closeEvent.
        # Finish the worker before destroying its Python QThread wrapper.
        if self.lookup_thread and self.lookup_thread.isRunning():
            self.lookup_thread.wait()
        super().done(code)


class ProgressDialog(QDialog):
    def __init__(self, parent, work):
        super().__init__(parent)
        self.setWindowTitle("Log reading progress")
        self.resize(380, 230)
        layout = QVBoxLayout(self)
        layout.addWidget(label(work["title"], "subheading"))
        form = QFormLayout()
        self.progress = QSpinBox()
        self.progress.setRange(0, 100 if work["progress_unit"] == "percent" else 10_000_000)
        self.progress.setValue(work.get("progress", 0))
        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional reading note")
        self.use_historical_date = QCheckBox("Set a specific reading date")
        self.when = QDateEdit()
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd")
        self.when.setDate(QDate.currentDate())
        self.when.setMinimumDate(QDate(1900, 1, 1))
        self.when.setMaximumDate(QDate(2200, 12, 31))
        self.when.setEnabled(False)
        self.use_historical_date.toggled.connect(self.when.setEnabled)
        form.addRow(name(work["progress_unit"]) + " reached", self.progress)
        form.addRow("Note", self.note)
        form.addRow(self.use_historical_date)
        form.addRow("Date", self.when)
        layout.addLayout(form)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)


class MilestoneEditor(QDialog):
    """Correct an existing progress event, including past dates and absolute progress."""

    def __init__(self, parent, event):
        super().__init__(parent)
        self.setWindowTitle("Edit reading milestone")
        self.resize(460, 270)
        layout = QVBoxLayout(self)
        layout.addWidget(label(event["title"], "subheading"))
        layout.addWidget(label(
            "Editing a previous milestone recalculates later progress differences.", "muted"
        ))
        form = QFormLayout()
        self.date = QDateEdit()
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setCalendarPopup(True)
        self.date.setMinimumDate(QDate(1900, 1, 1))
        self.date.setMaximumDate(QDate(2200, 12, 31))
        parsed = QDate.fromString(
            Library.activity_day(event["recorded_at"]) or "",
            "yyyy-MM-dd",
        )
        self.date.setDate(parsed if parsed.isValid() else QDate.currentDate())
        self.progress = QSpinBox()
        self.progress.setRange(
            0, 100 if event["progress_unit"] == "percent" else 10_000_000
        )
        self.progress.setValue(event["new_progress"])
        self.note = QLineEdit(event["note"])
        self.note.setPlaceholderText("Optional note")
        form.addRow("Date", self.date)
        form.addRow(name(event["progress_unit"]) + " reached", self.progress)
        form.addRow("Note", self.note)
        layout.addLayout(form)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)


class ReadingSessionDialog(QDialog):
    """Log explicit reading/listening time; never infer it from chapter progress."""

    def __init__(self, parent, library, *, existing=None, work_id=None, day=None):
        super().__init__(parent)
        self.setWindowTitle("Edit reading time" if existing else "Log reading time")
        self.resize(535, 345)
        layout = QVBoxLayout(self)
        layout.addWidget(label(
            "Reading and listening time is separate from chapter/page milestones.",
            "muted"
        ))
        form = QFormLayout()
        self.book = QComboBox()
        self.book.setMaxVisibleItems(20)
        for entry in library.list_works(sort="title"):
            self.book.addItem(
                f"{entry['title']}  —  {entry['author'] or 'Unknown author'}",
                entry["id"]
            )
        target = existing["work_id"] if existing else work_id
        if target:
            choice = self.book.findData(target)
            if choice >= 0:
                self.book.setCurrentIndex(choice)
        self.book.setEnabled(not existing)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setDate(QDate.fromString(
            existing["read_date"] if existing else
            (day or QDate.currentDate().toString("yyyy-MM-dd")), "yyyy-MM-dd"
        ))
        self.date.setMinimumDate(QDate(1900, 1, 1))
        self.date.setMaximumDate(QDate(2200, 12, 31))
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 1440)
        self.minutes.setValue(existing["minutes"] if existing else 30)
        self.minutes.setSuffix(" minutes")
        self.mode = QComboBox()
        self.mode.addItem("Reading (text / ebook)", "reading")
        self.mode.addItem("Listening (audiobook)", "listening")
        if existing:
            self.mode.setCurrentIndex(self.mode.findData(existing["mode"]))
        self.note = QTextEdit()
        self.note.setPlaceholderText("Optional session notes…")
        self.note.setMaximumHeight(105)
        if existing:
            self.note.setPlainText(existing["note"])
        form.addRow("Book", self.book)
        form.addRow("Date", self.date)
        form.addRow("Duration", self.minutes)
        form.addRow("Activity", self.mode)
        form.addRow("Note", self.note)
        layout.addLayout(form)
        self.feedback = label("", "muted")
        layout.addWidget(self.feedback)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.validate_accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def validate_accept(self):
        if not self.book.currentData():
            self.feedback.setText("Choose a book before saving reading time.")
            return
        self.accept()

    def values(self):
        return {
            "read_date": self.date.date().toString("yyyy-MM-dd"),
            "minutes": self.minutes.value(),
            "mode": self.mode.currentData(),
            "note": self.note.toPlainText().strip(),
        }


class AnnotationDialog(QDialog):
    """A bookmark, quotation or note attached to a work, optionally at a chapter/page."""

    def __init__(self, parent, library, *, existing=None, work_id=None):
        super().__init__(parent)
        self.setWindowTitle("Edit saved note" if existing else "Add bookmark or note")
        self.resize(610, 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.book = QComboBox()
        self.book.setMaxVisibleItems(20)
        for entry in library.list_works(sort="title"):
            self.book.addItem(
                f"{entry['title']}  —  {entry['author'] or 'Unknown author'}",
                entry["id"]
            )
        target = existing["work_id"] if existing else work_id
        if target:
            choice = self.book.findData(target)
            if choice >= 0:
                self.book.setCurrentIndex(choice)
        self.book.setEnabled(not existing)
        self.kind = QComboBox()
        for item, display in (
            ("bookmark", "Bookmark"), ("quote", "Quote / passage"),
            ("note", "Reading note")
        ):
            self.kind.addItem(display, item)
        if existing:
            self.kind.setCurrentIndex(self.kind.findData(existing["kind"]))
        # New bookmarks and quotes should not open with their location controls
        # mysteriously disabled. Location can still be omitted explicitly.
        self.has_location = QCheckBox("Include a position (uncheck if unknown)")
        self.has_location.setToolTip(
            "Uncheck to save this entry without a chapter, page, or percentage."
        )
        self.location = QSpinBox()
        self.location.setRange(0, 10000000)
        self.location.setToolTip("The chapter, page or percentage for this entry.")
        self.unit = QComboBox()
        for key in (*UNITS, "kindle_location"):
            self.unit.addItem(name(key), key)
        self.unit.setToolTip("Choose whether the position refers to chapters, pages or percent.")
        self.has_location.toggled.connect(self.location.setEnabled)
        self.has_location.toggled.connect(self.unit.setEnabled)

        # When the dialog opens on a selected book, suggest that book's actual
        # tracking unit and current position. Never write progress automatically.
        def suggest_location():
            work = library.get_work(self.book.currentData())
            if not work:
                return
            self.unit.setCurrentIndex(
                max(0, self.unit.findData(work["progress_unit"]))
            )
            run = library.active_run(work["id"])
            latest = run["current_progress"] if run else 0
            self.location.setValue(
                min(100 if work["progress_unit"] == "percent" else 10000000,
                    latest or (0 if work["progress_unit"] == "percent" else 1))
            )

        if existing:
            if existing["location"] is not None:
                self.has_location.setChecked(True)
                self.location.setValue(existing["location"])
                index = self.unit.findData(existing["location_unit"])
                if index >= 0:
                    self.unit.setCurrentIndex(index)
            else:
                self.has_location.setChecked(False)
                suggest_location()
        else:
            suggest_location()
            self.has_location.setChecked(True)
            self.book.currentIndexChanged.connect(lambda _index: suggest_location())
        # Both controls must faithfully follow the checkbox, including for an
        # existing annotation which was deliberately saved without a location.
        self.location.setEnabled(self.has_location.isChecked())
        self.unit.setEnabled(self.has_location.isChecked())
        self.body = QTextEdit()
        self.body.setPlaceholderText(
            "Quote, personal reaction, bookmark label, or something to remember…"
        )
        if existing:
            self.body.setPlainText(existing["body"])
        self.body.setMinimumHeight(130)
        form.addRow("Book", self.book)
        form.addRow("Type", self.kind)
        form.addRow("Location", self.has_location)
        form.addRow("Position", self.location)
        form.addRow("Unit", self.unit)
        form.addRow("Text", self.body)
        layout.addLayout(form)
        self.feedback = label("", "muted")
        layout.addWidget(self.feedback)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.validate_accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def validate_accept(self):
        if not self.book.currentData():
            self.feedback.setText("Choose a book before saving.")
            return
        if self.kind.currentData() in ("quote", "note") and not self.body.toPlainText().strip():
            self.feedback.setText("A quote or note needs some text.")
            return
        self.accept()

    def values(self):
        return {
            "kind": self.kind.currentData(),
            "location": self.location.value() if self.has_location.isChecked() else None,
            "location_unit": self.unit.currentData() if self.has_location.isChecked() else "",
            "body": self.body.toPlainText().strip(),
        }


class EditionEditor(QDialog):
    """Edit an edition without touching the work's personal reading records."""

    def __init__(self, parent=None, edition=None):
        super().__init__(parent)
        self.setWindowTitle("Edit edition" if edition else "Add edition")
        self.resize(560, 475)
        original = edition or {}
        layout = QVBoxLayout(self)
        layout.addWidget(label(
            "Track a print, ebook, audiobook, web or other edition of this work.",
            "muted"
        ))
        form = QFormLayout()
        self.title = QLineEdit(original.get("title", ""))
        self.title.setPlaceholderText("Optional edition title, e.g. 10th Anniversary")
        self.fmt = QComboBox()
        for key, text in (
            ("unknown", "Unspecified"),
            ("print", "Print"),
            ("ebook", "Ebook"),
            ("audiobook", "Audiobook"),
            ("web", "Web edition"),
            ("other", "Other"),
        ):
            self.fmt.addItem(text, key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(original.get("format","unknown"))))
        self.isbn = QLineEdit(original.get("isbn", ""))
        self.publisher = QLineEdit(original.get("publisher", ""))
        self.published = QLineEdit(original.get("published_at", ""))
        self.published.setPlaceholderText("YYYY or YYYY-MM-DD")
        self.pages = QSpinBox()
        self.pages.setRange(0, 10_000_000)
        self.pages.setSpecialValueText("Unknown")
        self.pages.setValue(original.get("pages") or 0)
        self.language = QLineEdit(original.get("language", ""))
        self.notes = QTextEdit(original.get("notes", ""))
        self.notes.setMaximumHeight(130)
        for caption, control in (
            ("Edition title", self.title), ("Format", self.fmt),
            ("ISBN", self.isbn), ("Publisher", self.publisher),
            ("Published", self.published), ("Pages", self.pages),
            ("Language", self.language), ("Edition notes", self.notes),
        ):
            form.addRow(caption, control)
        layout.addLayout(form)
        layout.addWidget(label(
            "Choosing a preferred edition changes catalog ISBN/page metadata, "
            "not your reading history.", "muted"
        ))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {
            "title": self.title.text(), "format": self.fmt.currentData(),
            "isbn": self.isbn.text(), "publisher": self.publisher.text(),
            "published_at": self.published.text(),
            "pages": self.pages.value(), "language": self.language.text(),
            "notes": self.notes.toPlainText(),
        }


class CatalogManagerDialog(QDialog):
    """One work may have many source mirrors and catalog/format editions."""

    def __init__(self, parent, library, work_id):
        super().__init__(parent)
        self.library = library
        self.work_id = work_id
        self.worker = None
        self.setWindowTitle("Sources & editions")
        self.resize(1000, 650)
        self.setMinimumSize(750, 520)
        work = library.get_work(work_id)
        layout = QVBoxLayout(self)
        layout.addWidget(label(work["title"], "headline"))
        layout.addWidget(label(
            "All linked sources and editions belong to this one work. "
            "Reading history stays attached to the original entry.", "muted"
        ))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)
        source_page = QWidget()
        source_layout = QVBoxLayout(source_page)
        self.sources = QTableWidget(0, 5)
        self.sources.setHorizontalHeaderLabels([
            "Site", "Label / URL", "Known chapters", "Last checked", "Health"
        ])
        self.sources.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sources.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.sources.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sources.verticalHeader().setVisible(False)
        for col in (1,):
            self.sources.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )
        for col in (0,2,3,4):
            self.sources.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        source_layout.addWidget(self.sources)
        row = QHBoxLayout()
        for caption, callback in (
            ("+ Add source", self.add_source),
            ("Make primary", self.make_primary),
            ("Open link", self.open_source_link),
            ("Check chapters", self.check_source),
            ("Import saved HTML", self.import_saved_html),
        ):
            row.addWidget(button(caption, callback))
        source_layout.addLayout(row)
        utilities = QHBoxLayout()
        utilities.addWidget(button(
            "Set chapter count manually", self.set_manual_chapters
        ))
        utilities.addWidget(button(
            "View check history", self.show_check_history
        ))
        utilities.addWidget(button(
            "Remove selected source", self.remove_source, danger=True
        ))
        source_layout.addLayout(utilities)
        source_layout.addWidget(label(
            "Automatic checks work only for supported public story sites. "
            "Cloudflare blocks are normal; use a browser-saved HTML page instead. "
            "No continuous monitoring or Cloudflare bypass is used.", "muted"
        ))
        self.tabs.addTab(source_page, "Story sources")

        edition_page = QWidget()
        edition_layout = QVBoxLayout(edition_page)
        self.editions = QTableWidget(0, 5)
        self.editions.setHorizontalHeaderLabels([
            "Format", "Edition", "ISBN", "Publisher", "Pages"
        ])
        self.editions.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.editions.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.editions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.editions.verticalHeader().setVisible(False)
        self.editions.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for col in (0,2,3,4):
            self.editions.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.editions.itemDoubleClicked.connect(lambda _: self.edit_edition())
        edition_layout.addWidget(self.editions)
        actions = QHBoxLayout()
        for caption, callback in (
            ("+ Add edition", self.add_edition),
            ("Edit edition", self.edit_edition),
            ("Make preferred", self.prefer_edition),
            ("Remove edition", self.remove_edition),
        ):
            actions.addWidget(button(caption, callback))
        edition_layout.addLayout(actions)
        edition_layout.addWidget(label(
            "Selecting a preferred edition changes the main catalog ISBN and, "
            "for page-tracked works, the known page count. It never resets progress.", "muted"
        ))
        self.tabs.addTab(edition_page, "Print / ebook / audiobook editions")

        self.feedback = label("", "muted")
        layout.addWidget(self.feedback)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        previous_source = self.selected_source_id()
        sources = self.library.list_sources(self.work_id)
        self.sources.setRowCount(len(sources))
        target = 0
        for i, source in enumerate(sources):
            if source["id"] == previous_source:
                target = i
            site = ("★ " if source["is_primary"] else "") + source["site"]
            values = (
                site,
                source["label"] + (" — " if source["label"] else "") + source["url"],
                f"{source['published_units']:,}" if source["published_units"] else "—",
                local_timestamp(source["last_checked_at"]) if source["last_checked_at"] else "Never",
                "Error" if source["last_error"] else "OK",
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, source["id"])
                self.sources.setItem(i,col,cell)
            self.sources.setRowHeight(i,41)
        if sources:
            self.sources.selectRow(target)
        self.refresh_editions()

    def refresh_editions(self):
        selected = self.selected_edition_id()
        editions = self.library.list_editions(self.work_id)
        self.editions.setRowCount(len(editions))
        target = 0
        for i, edition in enumerate(editions):
            if edition["id"] == selected:
                target=i
            values = (
                ("★ " if edition["is_preferred"] else "") + name(edition["format"]),
                edition["title"] or "Untitled edition",
                edition["isbn"] or "—", edition["publisher"] or "—",
                f"{edition['pages']:,}" if edition["pages"] else "—",
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col==0:
                    cell.setData(Qt.ItemDataRole.UserRole, edition["id"])
                self.editions.setItem(i,col,cell)
            self.editions.setRowHeight(i,41)
        if editions:
            self.editions.selectRow(target)

    def selected_source_id(self):
        row = self.sources.currentRow()
        cell = self.sources.item(row,0) if row>=0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def selected_edition_id(self):
        row = self.editions.currentRow()
        cell = self.editions.item(row,0) if row>=0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def add_source(self):
        url, okay = QInputDialog.getText(
            self, "Add source or crosspost", "Story URL (HTTPS):"
        )
        if not okay:
            return
        try:
            self.library.add_source(self.work_id,url)
            self.refresh()
            self.feedback.setText("Source linked to this work.")
        except Exception as exc:
            fail(self,exc)

    def make_primary(self):
        key = self.selected_source_id()
        if not key:
            return
        try:
            self.library.set_primary_source(key)
            self.refresh()
            self.feedback.setText("Preferred source updated; reading history unchanged.")
        except Exception as exc:
            fail(self,exc)

    def remove_source(self):
        key=self.selected_source_id()
        if not key:
            return
        if QMessageBox.question(
            self, "Remove source link?",
            "Remove this source link and its check history? "
            "The book and all reading records remain."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.remove_source(key)
            self.refresh()
        except Exception as exc:
            fail(self,exc)

    def open_source_link(self):
        key=self.selected_source_id()
        source=self.library.get_source(key) if key else None
        if source:
            webbrowser.open(source["url"])

    def set_manual_chapters(self):
        key = self.selected_source_id()
        source = self.library.get_source(key) if key else None
        if not source:
            return
        count, okay = QInputDialog.getInt(
            self, "Record published chapters",
            "Published chapters for this source (entered manually):",
            value=source["published_units"], min=0, max=10_000_000,
        )
        if not okay:
            return
        try:
            outcome = self.library.record_source_check(
                key, observed_units=count,
                message="Manually recorded by the user; not checked online."
            )
            self.refresh()
            self.feedback.setText(
                f"Manually recorded {count:,} chapters ({outcome}). "
                "Reading progress remains unchanged."
            )
        except Exception as exc:
            fail(self, exc)

    def show_check_history(self):
        key = self.selected_source_id()
        if not key:
            return
        entries = self.library.list_source_checks(key,limit=300)
        if not entries:
            QMessageBox.information(
                self, "Check history", "No checks or manual chapter updates have been recorded."
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Source check history")
        dialog.resize(850,480)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(entries), 4)
        table.setHorizontalHeaderLabels([
            "Date", "Outcome", "Old → reported", "Details"
        ])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        for i, row in enumerate(entries):
            for col, value in enumerate((
                local_timestamp(row["checked_at"]),
                row["outcome"].title(),
                f"{row['old_units']} → {row['observed_units']}",
                row["message"],
            )):
                table.setItem(i,col,QTableWidgetItem(value))
        layout.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def check_source(self):
        key = self.selected_source_id()
        source = self.library.get_source(key) if key else None
        if not source:
            return
        if self.worker and self.worker.isRunning():
            self.feedback.setText("A story check is already running.")
            return
        try:
            source_from_url(source["url"])
        except ValueError:
            self.feedback.setText(
                "Automatic checks aren't available for this provider. "
                "You can still open the link and update chapters manually."
            )
            return
        self.feedback.setText("Checking the public story page…")
        worker = LookupThread(fetch_url_metadata, source["url"])
        self.worker = worker
        worker.found.connect(lambda response, sid=key: self.check_success(sid,response))
        worker.failed.connect(lambda message, sid=key: self.check_failed(sid,message))
        worker.start()

    def check_success(self, source_id, response):
        count = response.get("total_units") or 0
        try:
            outcome = self.library.record_source_check(
                source_id, observed_units=count, message="Story metadata retrieved."
            )
            self.feedback.setText(
                f"Check: {outcome}. Reported chapters: {count or 'unknown'}. "
                "Your reading position was not changed."
            )
            self.refresh()
        except Exception as exc:
            fail(self,exc)

    def check_failed(self, source_id, message):
        self.library.record_source_check(source_id,error=message)
        self.refresh()
        self.feedback.setText(
            "Source could not be checked. No book metadata was deleted. "
            "Try Import saved HTML after opening the page in your browser."
        )

    def import_saved_html(self):
        key = self.selected_source_id()
        source = self.library.get_source(key) if key else None
        if not source:
            return
        try:
            source_from_url(source["url"])
        except ValueError:
            self.feedback.setText(
                "Saved-page parsing is supported only for recognized story sites."
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select browser-saved story HTML", "",
            "HTML documents (*.html *.htm)"
        )
        if not path:
            return
        try:
            data = fetch_saved_page_metadata(path,source["url"])
            count = data.get("total_units") or 0
            outcome = self.library.record_source_check(
                key, observed_units=count,
                message="Parsed locally from browser-saved HTML."
            )
            self.refresh()
            self.feedback.setText(
                f"Saved HTML imported: {outcome}; "
                f"known chapter count {count or 'unknown'}."
            )
        except Exception as exc:
            fail(self,exc)

    def add_edition(self):
        dialog = EditionEditor(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.add_edition(self.work_id,dialog.values())
            self.refresh()
        except Exception as exc:
            fail(self,exc)

    def edit_edition(self):
        ident=self.selected_edition_id()
        edition=self.library.get_edition(ident) if ident else None
        if not edition:
            return
        dialog=EditionEditor(self,edition=edition)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.update_edition(ident,dialog.values())
            self.refresh()
        except Exception as exc:
            fail(self,exc)

    def prefer_edition(self):
        ident=self.selected_edition_id()
        if not ident:
            return
        try:
            self.library.set_preferred_edition(ident)
            self.refresh()
            self.feedback.setText("Preferred edition updated.")
        except Exception as exc:
            fail(self,exc)

    def remove_edition(self):
        ident=self.selected_edition_id()
        if not ident:
            return
        if QMessageBox.question(
            self, "Remove edition?",
            "Remove this edition record? The book and reading history remain."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.remove_edition(ident)
            self.refresh()
        except Exception as exc:
            fail(self,exc)

    def done(self, code):
        if self.worker and self.worker.isRunning():
            self.feedback.setText("Finish the current check before closing this dialog.")
            return
        super().done(code)


class QueueEntryDialog(QDialog):
    """A queue preference is independent of book status and reading history."""

    def __init__(self, parent, library, *, work_id=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Edit reading plan" if existing else "Add to reading queue")
        self.resize(590, 360)
        layout=QVBoxLayout(self)
        layout.addWidget(label(
            "Keep a planned reading order without changing any book's status or history.",
            "muted"
        ))
        form=QFormLayout()
        self.book=QComboBox()
        self.book.setEditable(True)
        self.book.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.book.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        books=library.list_works(sort="title")
        for book in books:
            self.book.addItem(f"{book['title']} — {book['author'] or 'Unknown author'}",book["id"])
        chosen = (existing or {}).get("work_id") or work_id
        if chosen:
            index=self.book.findData(chosen)
            if index>=0:
                self.book.setCurrentIndex(index)
        if existing or work_id:
            self.book.setEnabled(False)
        self.priority=QComboBox()
        for key, text in (
            ("next","Next up — highest priority"),
            ("high","High priority"),
            ("normal","Normal priority"),
            ("low","Someday / low priority"),
        ):
            self.priority.addItem(text,key)
        self.priority.setCurrentIndex(max(
            0,self.priority.findData((existing or {}).get("priority","normal"))
        ))
        self.target=QLineEdit((existing or {}).get("target_date",""))
        self.target.setPlaceholderText("Optional target date (YYYY-MM-DD)")
        self.note=QTextEdit((existing or {}).get("note",""))
        self.note.setPlaceholderText("Why you want to read it, plans or other reminders…")
        self.note.setMinimumHeight(85)
        form.addRow("Book",self.book)
        form.addRow("Priority",self.priority)
        form.addRow("Target date",self.target)
        form.addRow("Plan / notes",self.note)
        layout.addLayout(form)
        layout.addWidget(label(
            "Dates are optional. No reminders are sent automatically.", "muted"
        ))
        buttons=QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {
            "priority":self.priority.currentData(),
            "target_date":self.target.text().strip(),
            "note":self.note.toPlainText(),
        }

    def validate_accept(self):
        if (
            not self.book.currentData()
            or self.book.currentIndex()<0
            or self.book.currentText()!=self.book.itemText(self.book.currentIndex())
        ):
            QMessageBox.warning(
                self,"Choose book","Choose an existing book from the suggestions."
            )
            return
        try:
            Library._queue_values(**self.values())
        except ValueError as exc:
            QMessageBox.warning(self,"Reading plan",str(exc))
            return
        self.accept()


class LocalFilesDialog(QDialog):
    """Link existing files without moving or importing their content."""

    FILE_FILTER=(
        "Reading / audiobook files "
        "(*.epub *.m4b *.pdf *.mobi *.azw3 *.cbz "
        "*.mp3 *.flac *.ogg *.wav *.txt)"
    )

    def __init__(self,parent,library,work_id):
        super().__init__(parent)
        self.library=library
        self.work_id=work_id
        work=library.get_work(work_id)
        self.setWindowTitle("Local files — "+work["title"])
        self.resize(850,550)
        self.setMinimumSize(650,460)
        layout=QVBoxLayout(self)
        layout.addWidget(label(work["title"],"headline"))
        layout.addWidget(label(
            "Associate existing EPUB, M4B, PDF and other book/audio files. "
            "BookTracker stores links only, not file content or playback progress.",
            "muted"
        ))
        self.file_table=QTableWidget(0,5)
        self.file_table.setHorizontalHeaderLabels([
            "File", "Type", "Location", "Status", "Label"
        ])
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        self.file_table.horizontalHeader().setSectionResizeMode(
            2,QHeaderView.ResizeMode.Stretch
        )
        for col in (1,3,4):
            self.file_table.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        self.file_table.itemDoubleClicked.connect(lambda _:self.open_file())
        layout.addWidget(self.file_table,stretch=1)
        actions=QHBoxLayout()
        for caption,callback in (
            ("+ Attach files",self.attach),
            ("Read EPUB",self.read_selected_epub),
            ("Open selected",self.open_file),
            ("Relink selected",self.relink),
            ("Find in folder",self.relink_folder),
            ("Rename label",self.rename),
        ):
            actions.addWidget(button(caption,callback))
        layout.addLayout(actions)
        layout.addWidget(button(
            "Unlink selected file (does not delete the file)",
            self.remove, danger=True
        ))
        layout.addWidget(label(
            "Portable backups include only file names and metadata, never the "
            "full device path or large audio/ebook contents. On another computer, "
            "use Relink to point to your copy.", "muted"
        ))
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        previous=self.selected_file_id()
        records=self.library.list_local_files(self.work_id)
        self.file_table.setRowCount(len(records))
        chosen=0
        for row, item in enumerate(records):
            if item["id"]==previous:
                chosen=row
            columns=(
                item["filename"],item["format"].upper(),
                item["path"] or "(not linked on this computer)",
                "Available" if item["available"] else "Missing — relink",
                item["label"] or "—",
            )
            for col,value in enumerate(columns):
                cell=QTableWidgetItem(value)
                if col==0:
                    cell.setData(Qt.ItemDataRole.UserRole,item["id"])
                self.file_table.setItem(row,col,cell)
            self.file_table.setRowHeight(row,38)
        if records:
            self.file_table.selectRow(chosen)

    def selected_file_id(self):
        row=self.file_table.currentRow()
        cell=self.file_table.item(row,0) if row>=0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def attach(self):
        paths,_=QFileDialog.getOpenFileNames(
            self,"Attach local reading or audio files","",self.FILE_FILTER
        )
        if not paths:
            return
        errors=[]
        for path in paths:
            try:
                self.library.add_local_file(self.work_id,path)
            except (OSError,ValueError) as exc:
                errors.append(f"{Path(path).name}: {exc}")
        self.refresh()
        if errors:
            QMessageBox.warning(self,"Some files not linked","\n".join(errors[:12]))

    def read_selected_epub(self):
        key=self.selected_file_id()
        if not key:
            QMessageBox.information(self,"Choose EPUB","Select an EPUB file first.")
            return
        try:
            reader=EpubReader(self,self.library,key)
            reader.exec()
            self.refresh()
        except (ValueError,OSError,KeyError) as exc:
            QMessageBox.warning(self,"EPUB reader",str(exc))

    def open_file(self):
        key=self.selected_file_id()
        entry=self.library.get_local_file(key) if key else None
        if not entry:
            return
        if not entry["path"] or not Path(entry["path"]).is_file():
            QMessageBox.information(
                self,"File unavailable",
                "This file is missing or belongs to another computer. "
                "Choose Relink missing file to locate your copy."
            )
            self.refresh()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(entry["path"])):
            QMessageBox.warning(
                self,"Cannot open file","No system application could open this file."
            )

    def relink(self):
        key=self.selected_file_id()
        if not key:
            return
        path,_=QFileDialog.getOpenFileName(
            self,"Locate the existing book or audiobook","",self.FILE_FILTER
        )
        if not path:
            return
        try:
            self.library.relink_local_file(key,path)
            self.refresh()
        except (OSError,ValueError) as exc:
            fail(self,exc)

    def relink_folder(self):
        folder=QFileDialog.getExistingDirectory(
            self,"Find matching filenames in a folder"
        )
        if not folder:
            return
        try:
            result=self.library.relink_from_directory(
                folder,work_id=self.work_id
            )
            self.refresh()
            QMessageBox.information(
                self,"Bulk file relinking",
                f"Relinked: {result['relinked']}\n"
                f"Missing: {result['missing']}\n"
                f"Ambiguous matches skipped: {result['ambiguous']}\n"
                f"Files scanned: {result['scanned']}"
            )
        except Exception as exc:
            fail(self,exc)

    def rename(self):
        key=self.selected_file_id()
        item=self.library.get_local_file(key) if key else None
        if not item:
            return
        value,okay=QInputDialog.getText(
            self,"File label","Optional label:",text=item["label"]
        )
        if not okay:
            return
        try:
            self.library.rename_local_file(key,value)
            self.refresh()
        except ValueError as exc:
            fail(self,exc)

    def remove(self):
        key=self.selected_file_id()
        if not key:
            return
        if QMessageBox.question(
            self,"Unlink file?",
            "Remove this association from BookTracker? "
            "The real file on disk will not be deleted."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.library.remove_local_file(key)
        self.refresh()


class ReadingTimerDialog(QDialog):
    """Monotonic, foreground stopwatch. Saves only on explicit confirmation."""

    def __init__(self, parent, library, *, work_id=None, mode="reading"):
        super().__init__(parent)
        self.setWindowTitle("Reading / audiobook timer")
        self.resize(560, 465)
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        layout.addWidget(label(
            "Time only counts while this stopwatch is running. "
            "Pause it any time, then save real minutes to your calendar.",
            "muted"
        ))
        form = QFormLayout()
        self.book = QComboBox()
        self.book.setEditable(True)
        self.book.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.book.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        for work in library.list_works(sort="title"):
            self.book.addItem(
                f"{work['title']} — {work['author'] or 'Unknown author'}", work["id"]
            )
        if work_id:
            idx = self.book.findData(work_id)
            if idx >= 0:
                self.book.setCurrentIndex(idx)
        self.mode = QComboBox()
        self.mode.addItem("Reading (text / ebooks)", "reading")
        self.mode.addItem("Listening (audiobooks)", "listening")
        if mode not in ("reading","listening"):
            raise ValueError("Unknown reading timer type.")
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setMinimumDate(QDate(1900,1,1))
        self.date.setMaximumDate(QDate(2200,12,31))
        self.date.setDate(QDate.currentDate())
        self.note = QTextEdit()
        self.note.setPlaceholderText("Optional session notes…")
        self.note.setMinimumHeight(70)
        self.note.setMaximumHeight(100)
        for title,control in (
            ("Book",self.book),("Session type",self.mode),
            ("Reading date",self.date),("Note",self.note)
        ):
            form.addRow(title,control)
        layout.addLayout(form)
        self.clock=label("00:00:00","value")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.clock)
        controls=QHBoxLayout()
        self.start_button=button("Start",self.toggle_timer,primary=True)
        self.reset_button=button("Reset timer",self.reset_timer)
        controls.addWidget(self.start_button)
        controls.addWidget(self.reset_button)
        layout.addLayout(controls)
        layout.addWidget(label(
            "The clock uses elapsed (monotonic) time; no reading progress is inferred. "
            "Sessions shorter than 60 seconds cannot be saved. "
            "Closing without saving discards this stopwatch.",
            "muted"
        ))
        actions=QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.save_timer)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)
        self._accumulated_ms=0
        self._running=False
        self._elapsed=QElapsedTimer()
        self._ticker=QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self.refresh_timer)

    def elapsed_ms(self):
        return self._accumulated_ms + (
            self._elapsed.elapsed() if self._running else 0
        )

    def refresh_timer(self):
        seconds=self.elapsed_ms()//1000
        hours,remain=divmod(seconds,3600)
        minutes,seconds=divmod(remain,60)
        self.clock.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def toggle_timer(self):
        if self._running:
            self._accumulated_ms+=self._elapsed.elapsed()
            self._running=False
            self._ticker.stop()
            self.start_button.setText("Resume")
        else:
            self._elapsed.start()
            self._running=True
            self._ticker.start()
            self.start_button.setText("Pause")
        self.refresh_timer()

    def reset_timer(self):
        if self.elapsed_ms() and QMessageBox.question(
            self,"Reset timer?","Discard the elapsed time from this stopwatch?"
        )!=QMessageBox.StandardButton.Yes:
            return
        self._running=False
        self._ticker.stop()
        self._accumulated_ms=0
        self.start_button.setText("Start")
        self.refresh_timer()

    def values(self):
        return {
            "read_date":self.date.date().toString("yyyy-MM-dd"),
            "minutes":self.elapsed_ms()//60000,
            "mode":self.mode.currentData(),
            "note":self.note.toPlainText(),
        }

    def save_timer(self):
        index=self.book.currentIndex()
        if (index<0 or not self.book.currentData()
                or self.book.currentText()!=self.book.itemText(index)):
            QMessageBox.warning(
                self,"Select book","Choose a book from your library."
            )
            return
        values=self.values()
        if values["minutes"]<1:
            QMessageBox.warning(
                self,"Session too short",
                "At least one full minute is required to log reading time."
            )
            return
        if values["minutes"]>1440:
            QMessageBox.warning(
                self,"Session too long",
                "BookTracker supports sessions of up to 24 hours. "
                "Save shorter sessions separately."
            )
            return
        self.accept()

    def done(self,code):
        if (
            code!=QDialog.DialogCode.Accepted
            and self.elapsed_ms()>=1000
            and QMessageBox.question(
                self,"Discard unsaved time?",
                "This timed session has not been saved. Discard the elapsed time?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )!=QMessageBox.StandardButton.Yes
        ):
            return
        if code==QDialog.DialogCode.Accepted and self._running:
            # Freeze the exact accepted duration before the caller persists it.
            self._accumulated_ms+=self._elapsed.elapsed()
            self._running=False
        self._ticker.stop()
        super().done(code)


class ClippingsPreviewDialog(QDialog):
    """Preview and explicitly choose each Kindle book target before importing."""

    def __init__(self,parent,library,parsed):
        super().__init__(parent)
        self.library=library
        self.parsed=parsed
        self.groups=group_clippings(parsed["records"])
        self.setWindowTitle("Import Kindle clippings")
        self.resize(1020,650)
        self.setMinimumSize(740,520)
        layout=QVBoxLayout(self)
        layout.addWidget(label("Review Kindle highlights and notes","headline"))
        layout.addWidget(label(
            f"{len(parsed['records'])} readable passages across "
            f"{len(self.groups)} Kindle titles; {parsed['skipped']} unsupported "
            "or empty entries skipped. Nothing is imported until you confirm.",
            "muted"
        ))
        layout.addWidget(label(
            "For each title, choose a specific library book, create a new custom "
            "book, or skip it. BookTracker only auto-selects a unique exact "
            "title/author match. Original Kindle locations are preserved.",
            "muted"
        ))
        self.table=QTableWidget(len(self.groups),3)
        self.table.setHorizontalHeaderLabels(
            ["Kindle title / author","Passages","Target in BookTracker"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1,QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2,QHeaderView.ResizeMode.Stretch
        )
        self.choices=[]
        books=library.list_works(sort="title")
        for row, group in enumerate(self.groups):
            title=group["title"]+(
                f" — {group['author']}" if group["author"] else ""
            )
            self.table.setItem(row,0,QTableWidgetItem(title))
            self.table.setItem(row,1,QTableWidgetItem(str(len(group["records"]))))
            choice=QComboBox()
            choice.addItem("Skip this Kindle title",None)
            choice.addItem("+ Create a custom book (unread)", "create")
            matched=[]
            for work in books:
                choice.addItem(
                    f"{work['title']} — {work['author'] or 'Unknown author'}",
                    work["id"]
                )
                if _book_identity(work["title"]) != _book_identity(group["title"]):
                    continue
                if (group["author"] and
                        _book_identity(work["author"]) != _book_identity(group["author"])):
                    continue
                matched.append(work["id"])
            if len(matched)==1:
                choice.setCurrentIndex(choice.findData(matched[0]))
            self.table.setCellWidget(row,2,choice)
            self.table.setRowHeight(row,44)
            self.choices.append(choice)
        layout.addWidget(self.table,stretch=1)
        layout.addWidget(label(
            "Reimporting the same passages is safe: duplicate import IDs are "
            "skipped. Existing notes are not overwritten. Kindle's original "
            "highlight date is not used as a fabricated reading-history date.",
            "muted"
        ))
        actions=QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self.check_selection)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def decisions(self):
        return {
            group["key"]:self.choices[i].currentData()
            for i,group in enumerate(self.groups)
        }

    def check_selection(self):
        if not any(choice.currentData() is not None for choice in self.choices):
            QMessageBox.warning(
                self,"Nothing to import",
                "Select at least one existing book or Create a custom book."
            )
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, library):
        super().__init__()
        self.library = library
        self.selected_id = None
        self.covers = CoverCache(self)
        self.covers.local_covers = library.path.parent / "imported_covers"
        self.covers.cover_ready.connect(self.cover_available)
        self.current_cover_label = None
        self.current_cover_url = ""
        self.gallery_books = []
        self.metadata_worker = None
        self.epub_worker = None
        self.serial_worker = None
        self._serial_added = []
        self._serial_errors = 0
        self._serial_completed = 0
        self._serial_cancelled = False
        self._serial_tray = None
        self.settings = QSettings("BookTracker", "BookTracker")
        self.setWindowTitle("BookTracker — Your reading, remembered")
        self.resize(1360, 840)
        self.setMinimumSize(900, 620)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(14)

        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(205)
        sidebar = QVBoxLayout(side)
        sidebar.setContentsMargins(15, 22, 15, 18)
        sidebar.setSpacing(7)
        sidebar.addWidget(label("◈  BookTracker", "subheading"))
        sidebar.addWidget(label("Your reading, remembered.", "muted"))
        sidebar.addSpacing(10)
        nav_scroller=QScrollArea()
        nav_scroller.setObjectName("navScroll")
        nav_scroller.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroller.setWidgetResizable(True)
        nav_scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_panel=QWidget()
        nav_panel.setObjectName("navPanel")
        nav_scroller.setStyleSheet(
            "QScrollArea#navScroll { background-color: #192231; border: none; }"
            "QWidget#navPanel { background-color: #192231; }"
        )
        for surface in (nav_scroller, nav_scroller.viewport(), nav_panel):
            palette=surface.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#192231"))
            surface.setPalette(palette)
            surface.setAutoFillBackground(True)
        nav_layout=QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0,0,0,0)
        nav_layout.setSpacing(6)
        self.nav_buttons = []
        for title, index in (("▤   Library", 0), ("◷   Reading history", 1),
                             ("▥   Dashboard", 2), ("⇄   Device sync", 13), ("▦   Series", 3),
                             ("♧   Authors", 4), ("▤   Insights", 5),
                             ("◇   Library health", 6),
                             ("▦   Reading calendar", 7),
                             ("✎   Notes && quotes", 8),
                             ("◉   Update center", 9),
                             ("☷   Reading queue", 10),
                             ("◴   Reading habits", 11),
                             ("♫   Audiobooks", 12)):
            nav = button(title, lambda _, i=index: self.navigate(i))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            # Sidebar display order is independent of QStackedWidget page order.
            # Device sync is positioned near the top, but its page is index 13.
            nav.setProperty("page_index", index)
            nav_layout.addWidget(nav)
            self.nav_buttons.append(nav)
        nav_layout.addStretch()
        nav_scroller.setWidget(nav_panel)
        sidebar.addWidget(nav_scroller,stretch=1)
        sidebar.addWidget(button("Import EPUB files", self.import_epub_files))
        sidebar.addWidget(button("Import library", self.import_library))
        sidebar.addWidget(button("Export library", self.export_library))
        sidebar.addWidget(button("Back up database", self.manual_backup))
        sidebar.addWidget(label("Offline-first · SQLite", "muted"))
        outer.addWidget(side)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, stretch=1)
        self.create_library_page()
        self.create_history_page()
        self.create_stats_page()
        self.create_series_page()
        self.create_authors_page()
        self.create_insights_page()
        self.create_health_page()
        self.create_calendar_page()
        self.create_journal_page()
        self.create_updates_page()
        self.create_queue_page()
        self.create_habits_page()
        self.audio_page=AudioLibraryPage(self,self.library)
        self.pages.addWidget(self.audio_page)
        self.sync_page=SyncPage(self,self.library)
        self.pages.addWidget(self.sync_page)
        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        if self.settings.value("library_view", "list") == "gallery":
            self.view_selector.setCurrentIndex(1)
        self.navigate(0)
        self.refresh_all()
        self.statusBar().showMessage(f"Local database: {self.library.path}")
        self.make_menu()
        try:
            automatic_backup(self.library, destination=self.library.path.parent / 'backups')
        except Exception as exc:
            self.statusBar().showMessage(f"Automatic backup warning: {exc}", 25000)
        # Strictly opt-in. Never scrape as an unannounced default behavior.
        if self.settings.value("serial_auto_check", False, type=bool):
            QTimer.singleShot(1800, self.maybe_check_on_launch)

    def make_menu(self):
        menu = self.menuBar().addMenu("&File")
        for title, callback, shortcut in (
            ("Add work", self.add_work, "Ctrl+N"),
            ("Import EPUB files", self.import_epub_files, "Ctrl+Shift+E"),
            ("Read selected EPUB", self.read_selected_epub, "Ctrl+Alt+R"),
            ("Import Goodreads CSV", self.import_goodreads_csv, ""),
            ("Import BookTracker file", self.import_library, ""),
            ("Export BookTracker file", self.export_library, "Ctrl+Shift+S"),
            ("Back up database", self.manual_backup, "Ctrl+B"),
            ("Focus library search", self.focus_library_search, "Ctrl+F"),
            ("Reading history", lambda: self.navigate(1), "Ctrl+H"),
            ("Authors", lambda: self.navigate(4), "Ctrl+Shift+A"),
            ("Reading insights", lambda: self.navigate(5), "Ctrl+Shift+I"),
            ("Library health", lambda: self.navigate(6), "Ctrl+Shift+L"),
            ("Save current library view", self.save_current_view, "Ctrl+Shift+V"),
            ("Log progress on selected work", self.log_progress, "Ctrl+L"),
            ("Reading calendar", lambda: self.navigate(7), "Ctrl+Shift+C"),
            ("Notes and quotes", lambda: self.navigate(8), "Ctrl+Shift+N"),
            ("Log reading or listening time", self.log_reading_session, "Ctrl+Shift+T"),
            ("Sources and editions", self.manage_work_catalog, "Ctrl+Shift+M"),
            ("Update center", lambda: self.navigate(9), "Ctrl+Shift+U"),
            ("Check followed stories", lambda: self.check_serial_sources(only_followed=True),
             "Ctrl+Alt+U"),
            ("Reading queue", lambda: self.navigate(10), "Ctrl+Shift+Q"),
            ("Reading habits", lambda: self.navigate(11), "Ctrl+Alt+H"),
            ("Audiobooks", lambda: self.navigate(12), "Ctrl+Alt+B"),
            ("Device sync", lambda: self.navigate(13), "Ctrl+Alt+S"),
            ("Import AudiobookAI completed parts",
             lambda: self.audio_page.import_studio(), ""),
            ("Scan audiobook folder",
             lambda: self.audio_page.import_folder(), ""),
            ("Start reading timer", self.open_reading_timer, "Ctrl+Alt+T"),
            ("Import Kindle clippings", self.import_kindle_clippings, ""),
            ("Local book/audio files", self.manage_local_files, "Ctrl+Shift+F"),
            ("Relink library files from folder", self.relink_library_files, ""),
        ):
            action = QAction(title, self)
            action.triggered.connect(callback)
            if shortcut:
                action.setShortcut(shortcut)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for nav in self.nav_buttons:
            nav.setChecked(nav.property("page_index") == index)
        if index == 1:
            self.refresh_history()
        elif index == 2:
            self.refresh_stats()
        elif index == 3:
            self.refresh_series_page()
        elif index == 4:
            self.refresh_authors_page()
        elif index == 5:
            self.refresh_insights_page()
        elif index == 6:
            self.refresh_health_page()
        elif index == 7:
            self.refresh_calendar_page()
        elif index == 8:
            self.refresh_journal_page()
        elif index == 9:
            self.refresh_updates_page()
        elif index == 10:
            self.refresh_queue_page()
        elif index == 11:
            self.refresh_habits_page()
        elif index == 12:
            self.audio_page.refresh()
        elif index == 13:
            pass  # Discovery only when requested by the user.

    def focus_library_search(self):
        self.navigate(0)
        self.search.setFocus()
        self.search.selectAll()

    def create_library_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("My library", "headline"))
        titles.addWidget(label("Books, fanfiction and serialized stories in one place", "muted"))
        top.addLayout(titles, stretch=1)
        top.addWidget(button("+ Add work", self.add_work, primary=True))
        layout.addLayout(top)
        layout.addSpacing(12)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search title, author, fandom, tags, notes, description or ISBN…"
        )
        self.search.textChanged.connect(self.refresh_library)
        self.filter_status = QComboBox()
        self.filter_status.addItem("All statuses", "all")
        for item in STATUSES:
            self.filter_status.addItem(name(item), item)
        self.filter_status.currentIndexChanged.connect(self.refresh_library)
        self.filter_kind = QComboBox()
        self.filter_kind.addItem("All types", "all")
        for item in KINDS:
            self.filter_kind.addItem(name(item), item)
        self.filter_kind.currentIndexChanged.connect(self.refresh_library)
        self.sort = QComboBox()
        for title, key in (("Recently updated", "updated"), ("Title", "title"),
                           ("Author", "author"), ("Recently added", "added"), ("Rating", "rating"),
                           ("Recently finished", "finished"), ("Series", "series"),
                           ("Most progress", "progress")):
            self.sort.addItem(title, key)
        self.sort.currentIndexChanged.connect(self.refresh_library)
        self.shelf_filter = QComboBox()
        self.shelf_filter.addItem("All shelves", None)
        self.shelf_filter.currentIndexChanged.connect(self.refresh_library)
        self.series_filter = QComboBox()
        self.series_filter.addItem("All series", None)
        self.series_filter.currentIndexChanged.connect(self.refresh_library)
        self.author_filter = QComboBox()
        self.author_filter.addItem("All authors", None)
        self.author_filter.currentIndexChanged.connect(self.refresh_library)
        self.tag_filter = QComboBox()
        self.tag_filter.addItem("All tags", None)
        self.tag_filter.currentIndexChanged.connect(self.refresh_library)
        self.quality_filter = QComboBox()
        self.quality_filter.addItem("All records", None)
        for caption, key in (
            ("Missing cover", "missing_cover"),
            ("Missing author", "missing_author"),
            ("Missing description", "missing_description"),
            ("Missing source link", "missing_source"),
            ("No series assigned", "missing_series"),
            ("Incomplete metadata", "incomplete"),
            ("Uncaught serial chapters", "serial_backlog"),
            ("Completed, date unknown", "unknown_dates"),
        ):
            self.quality_filter.addItem(caption, key)
        self.quality_filter.currentIndexChanged.connect(self.refresh_library)
        self.only_fav = QCheckBox("★ Favorites")
        self.only_fav.toggled.connect(self.refresh_library)
        filters.addWidget(self.search, stretch=3)
        filters.addWidget(self.filter_status, stretch=1)
        filters.addWidget(self.filter_kind, stretch=1)
        filters.addWidget(self.sort, stretch=1)
        filters.addWidget(self.only_fav)
        layout.addLayout(filters)
        secondary_filters = QHBoxLayout()
        secondary_filters.addWidget(label("Collections:", "muted"))
        secondary_filters.addWidget(self.shelf_filter, stretch=1)
        secondary_filters.addWidget(self.series_filter, stretch=1)
        secondary_filters.addWidget(self.author_filter, stretch=1)
        secondary_filters.addWidget(self.tag_filter, stretch=1)
        secondary_filters.addWidget(self.quality_filter, stretch=1)
        secondary_filters.addWidget(button("Clear filters", self.clear_library_filters))
        layout.addLayout(secondary_filters)
        commands = QHBoxLayout()
        self.view_selector = QComboBox()
        self.view_selector.addItem("Detailed list", "list")
        self.view_selector.addItem("Cover gallery", "gallery")
        self.view_selector.currentIndexChanged.connect(self.toggle_library_view)
        commands.addWidget(self.view_selector)
        commands.addWidget(button("Select all", self.select_all_visible))
        commands.addWidget(button("Bulk edit", self.bulk_edit_selected))
        commands.addWidget(button("Add to shelf", self.add_selected_to_shelf))
        commands.addWidget(button("Queue selected", self.queue_selected_books))
        commands.addWidget(button("New shelf", self.create_shelf))
        commands.addWidget(button("Manage shelves", self.manage_shelves))
        commands.addStretch()
        layout.addLayout(commands)
        saved_row = QHBoxLayout()
        saved_row.addWidget(label("Saved view:", "muted"))
        self.saved_view_combo = QComboBox()
        self.saved_view_combo.addItem("Choose a saved filter…", None)
        self.saved_view_combo.currentIndexChanged.connect(self.apply_saved_view)
        saved_row.addWidget(self.saved_view_combo, stretch=1)
        saved_row.addWidget(button("Save current filters", self.save_current_view))
        saved_row.addWidget(button("Delete saved view", self.delete_current_view))
        layout.addLayout(saved_row)
        layout.addSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Title", "Author", "Type", "Reading", "Progress", "Rating"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setIconSize(QSize(37, 53))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.table.itemDoubleClicked.connect(lambda _: self.edit_work())
        self.library_views = QStackedWidget()
        self.library_views.addWidget(self.table)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setMovement(QListWidget.Movement.Static)
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setWrapping(True)
        self.gallery.setWordWrap(True)
        self.gallery.setSpacing(14)
        self.gallery.setGridSize(QSize(164, 226))
        self.gallery.setIconSize(QSize(116, 164))
        self.gallery.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.gallery.itemSelectionChanged.connect(self.show_selected)
        self.gallery.itemDoubleClicked.connect(lambda _: self.edit_work())
        self.library_views.addWidget(self.gallery)
        splitter.addWidget(self.library_views)

        details = QFrame()
        details.setObjectName("details")
        details.setMinimumWidth(285)
        details.setMaximumWidth(480)
        d_outer = QVBoxLayout(details)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #192231; border: none; }")
        detail_content = QWidget()
        detail_content.setObjectName("detailContent")
        detail_content.setStyleSheet("QWidget#detailContent { background-color: #192231; }")
        for surface in (scroll.viewport(), detail_content):
            palette = surface.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#192231"))
            surface.setPalette(palette)
            surface.setAutoFillBackground(True)
        self.details = QVBoxLayout(detail_content)
        self.details.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.details.setSpacing(10)
        scroll.setWidget(detail_content)
        d_outer.addWidget(scroll)
        splitter.addWidget(details)
        splitter.setSizes([720, 330])
        layout.addWidget(splitter, stretch=1)
        self.library_count = label("", "muted")
        layout.addWidget(self.library_count)
        self.pages.addWidget(page)

    def create_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Reading history", "headline"))
        layout.addWidget(label(
            "Find and correct chapter/page milestones across all reading runs.", "muted"
        ))
        filter_row = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search book, author or note…")
        self.history_search.textChanged.connect(self.refresh_history)
        filter_row.addWidget(self.history_search, stretch=1)
        self.history_year = QComboBox()
        self.history_year.addItem("All years", None)
        self.history_year.currentIndexChanged.connect(self.refresh_history)
        filter_row.addWidget(self.history_year)
        filter_row.addWidget(button("Edit selected", self.edit_history_event))
        filter_row.addWidget(button("Open book", self.open_history_book))
        filter_row.addWidget(button("Export CSV", self.export_history_csv))
        layout.addLayout(filter_row)
        actions = QHBoxLayout()
        actions.addWidget(button("Delete selected mistaken milestone", self.delete_history_event))
        self.history_count = label("", "muted")
        actions.addWidget(self.history_count)
        actions.addStretch()
        layout.addLayout(actions)
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Date", "Work", "Progress", "Note"])
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.history_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.history_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.history_table.itemDoubleClicked.connect(
            lambda _: self.edit_history_event()
        )
        layout.addWidget(self.history_table)
        self.pages.addWidget(page)

    def create_stats_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #10151f; border: none; }")
        content = QWidget()
        content.setObjectName("dashboardContent")
        content.setStyleSheet("QWidget#dashboardContent { background: #10151f; }")
        for surface in (scroll.viewport(), content):
            palette = surface.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#10151f"))
            surface.setPalette(palette)
            surface.setAutoFillBackground(True)
        layout = QVBoxLayout(content)
        layout.setSpacing(15)
        layout.addWidget(label("Reading dashboard", "headline"))
        layout.addWidget(label(
            "Recorded milestones, your current reads and goals — without guessing historical dates.",
            "muted"
        ))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.stat_labels = {}
        for i, (key, title) in enumerate((
            ("works", "Works in library"), ("completed_works", "Completed works"),
            ("completed_reads", "Finished reading runs"), ("chapters_logged", "Chapters logged"),
            ("favorites", "Favorites"), ("reading", "Currently reading"))):
            frame = QFrame()
            frame.setObjectName("statCard")
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(18, 12, 18, 12)
            inner.addWidget(label(title, "muted"))
            value = label("0", "value")
            inner.addWidget(value)
            self.stat_labels[key] = value
            grid.addWidget(frame, i // 3, i % 3)
        layout.addLayout(grid)
        self.stat_breakdown = label("", "muted")
        layout.addWidget(self.stat_breakdown)

        head = QHBoxLayout()
        head.addWidget(label("Reading goals", "subheading"))
        head.addWidget(label("Year:", "muted"))
        self.goal_year = QSpinBox()
        self.goal_year.setRange(1900, 2200)
        self.goal_year.setValue(datetime.now().year)
        head.addWidget(self.goal_year)
        head.addStretch()
        layout.addLayout(head)

        goals_form = QGridLayout()
        self.goal_inputs = {}
        self.goal_progress_bars = {}
        for row, (key, title, upper) in enumerate((
            ("books", "Completed books (known finish dates)", 100000),
            ("chapters", "Chapters recorded", 10000000),
            ("pages", "Pages recorded", 100000000),
        )):
            goals_form.addWidget(label(title), row, 0)
            spin = QSpinBox()
            spin.setRange(0, upper)
            spin.setSpecialValueText("No goal")
            self.goal_inputs[key] = spin
            goals_form.addWidget(spin, row, 1)
            bar = QProgressBar()
            bar.setFormat("%v / %m")
            bar.setMaximum(1)
            bar.setValue(0)
            self.goal_progress_bars[key] = bar
            goals_form.addWidget(bar, row, 2)
        goals_form.setColumnStretch(2, 2)
        layout.addLayout(goals_form)
        actions = QHBoxLayout()
        actions.addWidget(button("Save goals", self.save_reading_goals, primary=True))
        actions.addWidget(label(
            "No goal disables that target. Unknown historical finish dates are excluded.",
            "muted"
        ), stretch=1)
        layout.addLayout(actions)

        layout.addWidget(label("Continue reading", "subheading"))
        self.current_reads = QListWidget()
        self.current_reads.setMaximumHeight(155)
        self.current_reads.itemDoubleClicked.connect(self.open_current_read)
        layout.addWidget(self.current_reads)
        layout.addWidget(label("Serialized stories with unread published chapters", "subheading"))
        self.serial_backlog = QListWidget()
        self.serial_backlog.setMaximumHeight(155)
        self.serial_backlog.itemDoubleClicked.connect(self.open_current_read)
        layout.addWidget(self.serial_backlog)
        layout.addWidget(label("Recent recorded activity", "subheading"))
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(190)
        self.recent_list.itemDoubleClicked.connect(self.open_current_read)
        layout.addWidget(self.recent_list)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.pages.addWidget(page)
        self.goal_year.valueChanged.connect(self.load_reading_goals)
        self.load_reading_goals()

    def create_insights_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        top.addWidget(label("Reading insights", "headline"), stretch=1)
        top.addWidget(label("Year:", "muted"))
        self.insights_year = QSpinBox()
        self.insights_year.setRange(1900, 2200)
        self.insights_year.setValue(datetime.now().year)
        self.insights_year.valueChanged.connect(self.refresh_insights_page)
        top.addWidget(self.insights_year)
        top.addWidget(button("Export monthly CSV", self.export_insights_csv))
        layout.addLayout(top)
        layout.addWidget(label(
            "Measured reading milestones and known finish dates only. "
            "Unknown historical dates are excluded.", "muted"
        ))
        self.insights_summary = label("", "subheading")
        layout.addWidget(self.insights_summary)
        self.monthly_table = QTableWidget(12, 6)
        self.monthly_table.setHorizontalHeaderLabels(
            ["Month", "Chapters", "Pages", "Activity days", "Events", "Completions"]
        )
        self.monthly_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.monthly_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.monthly_table.verticalHeader().setVisible(False)
        self.monthly_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in range(1, 6):
            self.monthly_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        layout.addWidget(self.monthly_table, stretch=2)
        layout.addWidget(label("Most represented authors", "subheading"))
        self.insight_authors = QListWidget()
        self.insight_authors.setMaximumHeight(170)
        layout.addWidget(self.insight_authors)
        self.pages.addWidget(page)

    def export_insights_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export monthly reading activity",
            f"BookTracker-activity-{self.insights_year.value()}.csv",
            "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["Year", "Month", "Chapters", "Pages", "Active days", "Events",
                     "Completions", "Reading minutes", "Listening minutes", "Sessions"]
                )
                for item in self.library.monthly_activity(self.insights_year.value()):
                    writer.writerow([
                        self.insights_year.value(), item["month"], item["chapters"],
                        item["pages"], item["reading_days"], item["events"], item["completed"],
                        item["reading_minutes"], item["listening_minutes"],
                        item["sessions"],
                    ])
            self.statusBar().showMessage("Reading insights exported.", 7000)
        except OSError as exc:
            fail(self, exc)

    def refresh_insights_page(self, *_args):
        data = self.library.monthly_activity(self.insights_year.value())
        names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        for row, item in enumerate(data):
            values = [
                names[row], f"{item['chapters']:,}", f"{item['pages']:,}",
                str(item["reading_days"]), str(item["events"]), str(item["completed"]),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                self.monthly_table.setItem(row, col, cell)
            self.monthly_table.setRowHeight(row, 35)
        self.insights_summary.setText(
            f"{sum(m['chapters'] for m in data):,} chapters   ·   "
            f"{sum(m['pages'] for m in data):,} pages   ·   "
            f"{sum(m['reading_days'] for m in data):,} active days   ·   "
            f"{sum(m['completed'] for m in data):,} dated completions\n"
            f"{sum(m['reading_minutes'] for m in data) / 60:.1f} h reading   ·   "
            f"{sum(m['listening_minutes'] for m in data) / 60:.1f} h listening"
        )
        self.insight_authors.clear()
        for author in self.library.author_summary()[:12]:
            self.insight_authors.addItem(
                f"{author['author']}    ·    {author['total']} works "
                f"({author['completed']} completed)"
            )
        if not self.insight_authors.count():
            self.insight_authors.addItem("Add works with authors to see them here.")

    def create_health_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Library health", "headline"))
        layout.addWidget(label(
            "Review potentially incomplete metadata and duplicates. "
            "No books are deleted or merged automatically.", "muted"
        ))
        counts_grid = QGridLayout()
        self.health_counts = {}
        filters = [
            ("Missing covers", "missing_cover"),
            ("Missing authors", "missing_author"),
            ("Missing descriptions", "missing_description"),
            ("Missing source links", "missing_source"),
            ("Unassigned series", "missing_series"),
            ("Uncaught chapters", "serial_backlog"),
            ("Completed without date", "unknown_dates"),
        ]
        for index, (caption, key) in enumerate(filters):
            frame = QFrame()
            frame.setObjectName("statCard")
            column = QVBoxLayout(frame)
            column.addWidget(label(caption, "muted"))
            count = label("0", "value")
            column.addWidget(count)
            column.addWidget(button(
                "Show affected books", lambda _checked=False, key=key: self.open_quality_filter(key)
            ))
            self.health_counts[key] = count
            counts_grid.addWidget(frame, index // 4, index % 4)
        layout.addLayout(counts_grid)

        layout.addWidget(label("Possible duplicate works", "subheading"))
        layout.addWidget(label(
            "Matches are based on normalized title and author. "
            "Review each match before changing or deleting anything.", "muted"
        ))
        self.duplicate_table = QTableWidget(0, 3)
        self.duplicate_table.setHorizontalHeaderLabels(
            ["Work", "Author", "Matching entries"]
        )
        self.duplicate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.duplicate_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.duplicate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.duplicate_table.verticalHeader().setVisible(False)
        self.duplicate_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.duplicate_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.duplicate_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.duplicate_table.itemDoubleClicked.connect(self.open_duplicate_book)
        layout.addWidget(self.duplicate_table, stretch=1)
        duplicate_actions = QHBoxLayout()
        duplicate_actions.addWidget(button("Open selected book", self.open_duplicate_book))
        self.duplicate_count = label("", "muted")
        duplicate_actions.addWidget(self.duplicate_count, stretch=1)
        layout.addLayout(duplicate_actions)

        tags = QHBoxLayout()
        tags.addWidget(label("Tag cleanup:", "subheading"))
        self.health_tags = QComboBox()
        tags.addWidget(self.health_tags, stretch=1)
        tags.addWidget(button("Rename / merge", self.rename_selected_tag))
        tags.addWidget(button("Delete tag", self.delete_selected_tag))
        layout.addLayout(tags)
        layout.addWidget(label(
            "Renaming a tag merges duplicate spellings. Removing a tag "
            "does not delete its books.", "muted"
        ))
        layout.addWidget(button("Check SQLite database integrity", self.show_integrity_result))
        self.pages.addWidget(page)

    def refresh_health_page(self):
        counts = self.library.quality_counts()
        for key, widget in self.health_counts.items():
            widget.setText(str(counts[key]))
        groups = self.library.duplicate_candidates()
        self.duplicate_table.setRowCount(0)
        row = 0
        for entries in groups:
            for work in entries:
                self.duplicate_table.insertRow(row)
                values = (work["title"], work["author"], str(len(entries)))
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, work["id"])
                    self.duplicate_table.setItem(row, col, item)
                self.duplicate_table.setRowHeight(row, 35)
                row += 1
        self.duplicate_count.setText(
            f"{len(groups)} possible duplicate groups ({row} entries)"
        )
        current = self.health_tags.currentData()
        self.health_tags.clear()
        self.health_tags.addItem("Choose a tag…", None)
        for tag in self.library.tag_summary():
            self.health_tags.addItem(
                f"{tag['tag']} ({tag['total']})", tag["tag"]
            )
        position = self.health_tags.findData(current)
        if position >= 0:
            self.health_tags.setCurrentIndex(position)

    def open_quality_filter(self, key):
        self.clear_library_filters()
        index = self.quality_filter.findData(key)
        if index >= 0:
            self.quality_filter.setCurrentIndex(index)
        self.navigate(0)
        self.refresh_library()

    def open_duplicate_book(self, item=None):
        row = item.row() if item is not None else self.duplicate_table.currentRow()
        if row < 0:
            return
        cell = self.duplicate_table.item(row, 0)
        work_id = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        if not work_id:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=work_id)

    def rename_selected_tag(self):
        old = self.health_tags.currentData()
        if not old:
            QMessageBox.information(self, "Select tag", "Choose a tag first.")
            return
        replacement, accepted = QInputDialog.getText(
            self, "Rename or merge tag", f"Replace tag {old!r} with:", text=old
        )
        if not accepted:
            return
        try:
            affected = self.library.rename_tag(old, replacement)
            self.refresh_all()
            self.refresh_health_page()
            self.statusBar().showMessage(
                f"Renamed tag in {affected} works.", 6500
            )
        except Exception as exc:
            fail(self, exc)

    def delete_selected_tag(self):
        name = self.health_tags.currentData()
        if not name:
            QMessageBox.information(self, "Select tag", "Choose a tag first.")
            return
        if QMessageBox.question(
            self, "Remove tag?", f"Remove {name!r} from every book? The books remain."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            affected = self.library.delete_tag(name)
            self.refresh_all()
            self.refresh_health_page()
            self.statusBar().showMessage(
                f"Removed tag from {affected} works.", 6500
            )
        except Exception as exc:
            fail(self, exc)

    def show_integrity_result(self):
        try:
            status = self.library.integrity_check()
            QMessageBox.information(
                self, "SQLite integrity check",
                "Database integrity: OK" if status == "ok" else
                f"Database integrity check reported: {status}"
            )
        except Exception as exc:
            fail(self, exc)

    def create_calendar_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QHBoxLayout()
        heading.addWidget(label("Reading calendar", "headline"), stretch=1)
        heading.addWidget(button(
            "+ Log reading / listening", self.log_reading_session, primary=True
        ))
        heading.addWidget(button("Start timer", self.open_reading_timer))
        heading.addWidget(button("Export time log CSV", self.export_time_log))
        layout.addLayout(heading)
        layout.addWidget(label(
            "A day is active if you recorded a reading milestone or logged actual "
            "reading/listening time. Days with missing history stay unmarked.", "muted"
        ))
        self.calendar_streak = label("", "subheading")
        layout.addWidget(self.calendar_streak)
        columns = QHBoxLayout()
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.calendar.setMinimumWidth(335)
        self.calendar.setStyleSheet(
            "QCalendarWidget { background: #192231; }"
            "QCalendarWidget QWidget#qt_calendar_navigationbar { background: #233146; }"
            "QCalendarWidget QTableView { background-color: #202c3c;"
            " alternate-background-color: #202c3c; color: #e7eef9;"
            " selection-background-color: #3274c2; }"
            "QCalendarWidget QHeaderView::section { background: #233146;"
            " color: #e7eef9; border: none; }"
        )
        header = QTextCharFormat()
        header.setForeground(QColor("#c5d5ea"))
        header.setBackground(QColor("#233146"))
        for weekday in (
            Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday,
            Qt.DayOfWeek.Wednesday, Qt.DayOfWeek.Thursday,
            Qt.DayOfWeek.Friday, Qt.DayOfWeek.Saturday,
            Qt.DayOfWeek.Sunday,
        ):
            self.calendar.setWeekdayTextFormat(weekday, header)
        self.calendar.selectionChanged.connect(self.refresh_calendar_day)
        self.calendar.currentPageChanged.connect(self.refresh_calendar_page)
        columns.addWidget(self.calendar, stretch=1)
        right = QVBoxLayout()
        self.calendar_month_summary = label("", "subheading")
        right.addWidget(self.calendar_month_summary)
        self.calendar_day_summary = label("", "muted")
        right.addWidget(self.calendar_day_summary)
        self.calendar_table = QTableWidget(0, 4)
        self.calendar_table.setHorizontalHeaderLabels(
            ["Activity", "Book", "Amount", "Note"]
        )
        self.calendar_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.calendar_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.calendar_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.calendar_table.verticalHeader().setVisible(False)
        self.calendar_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.calendar_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.calendar_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.calendar_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.calendar_table.itemDoubleClicked.connect(self.open_calendar_book)
        right.addWidget(self.calendar_table, stretch=1)
        controls = QHBoxLayout()
        controls.addWidget(button("Edit time", self.edit_selected_session))
        controls.addWidget(button(
            "Delete time", self.delete_selected_session, danger=True
        ))
        controls.addWidget(button("Open book", self.open_calendar_book))
        right.addLayout(controls)
        columns.addLayout(right, stretch=2)
        layout.addLayout(columns, stretch=1)
        layout.addWidget(label(
            "Highlighted dates show activity. Clicking a date displays recorded "
            "progress and time sessions; editing a milestone remains available "
            "under Reading history.", "muted"
        ))
        self.pages.addWidget(page)
        self._calendar_marked = []

    def refresh_calendar_page(self, *_args):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        daily = self.library.daily_activity(year=year, month=month)
        for date in self._calendar_marked:
            self.calendar.setDateTextFormat(date, QTextCharFormat())
        self._calendar_marked.clear()
        for iso, item in daily.items():
            date = QDate.fromString(iso, "yyyy-MM-dd")
            if not date.isValid():
                continue
            minutes = item["reading_minutes"] + item["listening_minutes"]
            background = ("#356894" if minutes >= 60 else
                          "#294f72" if minutes or item["milestones"] >= 2
                          else "#2c465f")
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(background))
            fmt.setForeground(QColor("#ffffff"))
            self.calendar.setDateTextFormat(date, fmt)
            self._calendar_marked.append(date)
        total_minutes = sum(
            entry["reading_minutes"] + entry["listening_minutes"]
            for entry in daily.values()
        )
        self.calendar_month_summary.setText(
            f"{len(daily)} active days  ·  {total_minutes / 60:.1f} hours logged"
        )
        self.calendar_streak.setText(
            f"Current reading streak: {self.library.reading_streak()} days "
            "(based on logged activity)"
        )
        self.refresh_calendar_day()

    def refresh_calendar_day(self, *_args):
        day = self.calendar.selectedDate().toString("yyyy-MM-dd")
        daily = self.library.daily_activity(
            year=self.calendar.selectedDate().year(),
            month=self.calendar.selectedDate().month()
        ).get(day, {})
        sessions = self.library.list_sessions(read_date=day, limit=5000)
        milestones = self.library.milestones_on(day)
        minutes = daily.get("reading_minutes", 0)
        audio = daily.get("listening_minutes", 0)
        self.calendar_day_summary.setText(
            f"{day}: {len(sessions)} timed sessions  ·  "
            f"{minutes} min reading  ·  {audio} min listening  ·  "
            f"{len(milestones)} progress milestones"
        )
        self.calendar_table.setRowCount(0)
        for session in sessions:
            row = self.calendar_table.rowCount()
            self.calendar_table.insertRow(row)
            values = (
                "Listening" if session["mode"] == "listening" else "Reading",
                session["title"], f"{session['minutes']} min", session["note"],
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        ("session", session["id"], session["work_id"]),
                    )
                self.calendar_table.setItem(row, col, item)
            self.calendar_table.setRowHeight(row, 37)
        for event in milestones:
            row = self.calendar_table.rowCount()
            self.calendar_table.insertRow(row)
            amount = event["new_progress"] - event["old_progress"]
            values = (
                "Milestone", event["title"],
                f"{event['new_progress']} {event['progress_unit']} ({amount:+d})",
                event["note"],
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        ("milestone", event["id"], event["work_id"]),
                    )
                self.calendar_table.setItem(row, col, item)
            self.calendar_table.setRowHeight(row, 37)

    def selected_calendar_record(self):
        row = self.calendar_table.currentRow()
        if row < 0:
            return None
        item = self.calendar_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def log_reading_session(self):
        if not self.library.list_works():
            QMessageBox.information(
                self, "Empty library", "Add a book before logging reading time."
            )
            return
        day = (
            self.calendar.selectedDate().toString("yyyy-MM-dd")
            if self.pages.currentIndex() == 7 else None
        )
        dialog = ReadingSessionDialog(
            self, self.library, work_id=self.selected_id, day=day
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            ident = self.library.add_session(
                dialog.book.currentData(), **dialog.values()
            )
            self.refresh_all(dialog.book.currentData())
            self.statusBar().showMessage(
                f"Reading session saved ({ident[:8]}).", 7000
            )
        except Exception as exc:
            fail(self, exc)

    def edit_selected_session(self):
        record = self.selected_calendar_record()
        if not record or record[0] != "session":
            QMessageBox.information(
                self, "Choose session", "Select a logged Reading or Listening entry."
            )
            return
        current = self.library.get_session(record[1])
        if current is None:
            return
        dialog = ReadingSessionDialog(self, self.library, existing=current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.update_session(record[1], **dialog.values())
            self.refresh_all()
        except Exception as exc:
            fail(self, exc)

    def delete_selected_session(self):
        record = self.selected_calendar_record()
        if not record or record[0] != "session":
            QMessageBox.information(
                self, "Choose session", "Select a reading or listening session."
            )
            return
        if QMessageBox.question(
            self, "Delete time log?",
            "Remove this timed session? Book progress remains unchanged."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_session(record[1])
        self.refresh_all()

    def open_calendar_book(self, _item=None):
        record = self.selected_calendar_record()
        if not record:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=record[2])

    def export_time_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export reading/listening sessions",
            "BookTracker-time-log.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["Date", "Book", "Author", "Activity", "Minutes", "Note"]
                )
                for item in self.library.list_sessions(limit=50000):
                    # Spreadsheet applications must not interpret untrusted
                    # book titles/notes as executable formula expressions.
                    def safe(value):
                        text = str(value or "")
                        return (
                            "'" + text if text.lstrip().startswith(
                                ("=", "+", "-", "@")
                            ) else text
                        )
                    writer.writerow([
                        item["read_date"], safe(item["title"]),
                        safe(item["author"]), item["mode"],
                        item["minutes"], safe(item["note"]),
                    ])
            self.statusBar().showMessage("Exported logged reading time.", 7000)
        except OSError as exc:
            fail(self, exc)

    def create_journal_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QHBoxLayout()
        heading.addWidget(label("Notes & quotes", "headline"), stretch=1)
        heading.addWidget(button(
            "+ Add bookmark / quote / note", self.add_annotation, primary=True
        ))
        heading.addWidget(button("Import Kindle highlights", self.import_kindle_clippings))
        heading.addWidget(button("Export Markdown", self.export_annotations_md))
        layout.addLayout(heading)
        layout.addWidget(label(
            "Keep passages, reactions and reading positions attached to each book. "
            "Notes remain private in your local library and portable backups.", "muted"
        ))
        filters = QHBoxLayout()
        self.journal_search = QLineEdit()
        self.journal_search.setPlaceholderText(
            "Search passages, titles or authors…"
        )
        self.journal_search.textChanged.connect(self.refresh_journal_page)
        filters.addWidget(self.journal_search, stretch=2)
        self.journal_type = QComboBox()
        self.journal_type.addItem("All types", None)
        for kind in ("bookmark", "quote", "note"):
            self.journal_type.addItem(name(kind), kind)
        self.journal_type.currentIndexChanged.connect(self.refresh_journal_page)
        filters.addWidget(self.journal_type)
        self.journal_work = QComboBox()
        self.journal_work.addItem("All books", None)
        self.journal_work.currentIndexChanged.connect(self.refresh_journal_page)
        filters.addWidget(self.journal_work, stretch=2)
        layout.addLayout(filters)
        self.journal_table = QTableWidget(0, 5)
        self.journal_table.setHorizontalHeaderLabels(
            ["Type", "Book", "Position", "Excerpt / note", "Updated"]
        )
        self.journal_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.journal_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.journal_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.journal_table.verticalHeader().setVisible(False)
        for col in (1, 3):
            self.journal_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )
        for col in (0, 2, 4):
            self.journal_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.journal_table.itemSelectionChanged.connect(self.show_annotation_detail)
        self.journal_table.itemDoubleClicked.connect(
            lambda _item: self.edit_annotation()
        )
        layout.addWidget(self.journal_table, stretch=4)
        controls = QHBoxLayout()
        controls.addWidget(button("Edit", self.edit_annotation))
        controls.addWidget(button("Copy text", self.copy_annotation))
        controls.addWidget(button("Open book", self.open_annotation_book))
        controls.addWidget(button(
            "Delete", self.delete_selected_annotation, danger=True
        ))
        self.journal_count = label("", "muted")
        controls.addWidget(self.journal_count, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(label("Selected note / quotation", "subheading"))
        self.annotation_detail = QTextEdit()
        self.annotation_detail.setReadOnly(True)
        self.annotation_detail.setMaximumHeight(125)
        layout.addWidget(self.annotation_detail, stretch=1)
        self.pages.addWidget(page)

    def refresh_journal_work_filter(self):
        current = self.journal_work.currentData()
        self.journal_work.blockSignals(True)
        self.journal_work.clear()
        self.journal_work.addItem("All books", None)
        for work in self.library.list_works(sort="title"):
            self.journal_work.addItem(
                f"{work['title']} — {work['author']}", work["id"]
            )
        choice = self.journal_work.findData(current)
        self.journal_work.setCurrentIndex(max(0, choice))
        self.journal_work.blockSignals(False)

    def refresh_journal_page(self, *_args):
        self.refresh_journal_work_filter()
        entries = self.library.list_annotations(
            work_id=self.journal_work.currentData(),
            kind=self.journal_type.currentData(),
            search=self.journal_search.text(),
            limit=5000,
        )
        selected = self.selected_annotation_id()
        self.journal_table.blockSignals(True)
        self.journal_table.setRowCount(len(entries))
        selected_row = -1
        for row, item in enumerate(entries):
            if item["id"] == selected:
                selected_row = row
            position = (
                f"{'Page' if item['location_unit'] == 'pages' else 'Chapter'} "
                f"{item['location']}"
                if item["location"] is not None
                and item["location_unit"] != "percent"
                else f"{item['location']}%"
                if item["location"] is not None else "—"
            )
            values = (
                name(item["kind"]), item["title"], position,
                item["body"].replace("\n", " ")[:150],
                local_timestamp(item["updated_at"]),
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item["id"])
                self.journal_table.setItem(row, col, cell)
            self.journal_table.setRowHeight(row, 39)
        self.journal_table.blockSignals(False)
        if selected_row >= 0:
            self.journal_table.selectRow(selected_row)
        self.journal_count.setText(
            f"{len(entries)} notes shown"
            + (" (first 5,000)" if len(entries) == 5000 else "")
        )
        self.show_annotation_detail()

    def selected_annotation_id(self):
        if not hasattr(self, "journal_table"):
            return None
        rows = self.journal_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.journal_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def show_annotation_detail(self):
        ident = self.selected_annotation_id()
        item = self.library.get_annotation(ident) if ident else None
        self.annotation_detail.setPlainText(item["body"] if item else "")

    def add_annotation(self):
        if not self.library.list_works():
            QMessageBox.information(
                self, "Empty library", "Add a book before saving notes or bookmarks."
            )
            return
        dialog = AnnotationDialog(self, self.library, work_id=self.selected_id)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.add_annotation(dialog.book.currentData(), **dialog.values())
            self.refresh_all(dialog.book.currentData())
            self.statusBar().showMessage("Saved note to your library.", 6000)
        except Exception as exc:
            fail(self, exc)

    def edit_annotation(self):
        ident = self.selected_annotation_id()
        if not ident:
            QMessageBox.information(
                self, "Choose note", "Select a saved bookmark, quote or note first."
            )
            return
        item = self.library.get_annotation(ident)
        if not item:
            return
        dialog = AnnotationDialog(self, self.library, existing=item)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.update_annotation(ident, **dialog.values())
            self.refresh_all()
        except Exception as exc:
            fail(self, exc)

    def copy_annotation(self):
        ident = self.selected_annotation_id()
        item = self.library.get_annotation(ident) if ident else None
        if item:
            QApplication.clipboard().setText(item["body"])
            self.statusBar().showMessage("Copied note to clipboard.", 5000)

    def delete_selected_annotation(self):
        ident = self.selected_annotation_id()
        if not ident:
            QMessageBox.information(self, "Choose note", "Select a saved note first.")
            return
        if QMessageBox.question(
            self, "Delete saved note?",
            "Delete this bookmark, quote or note? The book remains untouched."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_annotation(ident)
        self.refresh_all()

    def open_annotation_book(self):
        ident = self.selected_annotation_id()
        item = self.library.get_annotation(ident) if ident else None
        if not item:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=item["work_id"])

    def show_work_annotations(self):
        work = self.selected_work()
        if not work:
            return
        self.journal_search.clear()
        self.journal_type.setCurrentIndex(0)
        self.navigate(8)
        position = self.journal_work.findData(work["id"])
        if position >= 0:
            self.journal_work.setCurrentIndex(position)
        self.refresh_journal_page()

    def export_annotations_md(self):
        entries = self.library.list_annotations(
            work_id=self.journal_work.currentData(),
            kind=self.journal_type.currentData(),
            search=self.journal_search.text(),
            limit=50000,
        )
        if not entries:
            QMessageBox.information(self, "No saved notes", "No notes match these filters.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export reading notes",
            "BookTracker-reading-notes.md", "Markdown (*.md)"
        )
        if not path:
            return
        try:
            entries.sort(
                key=lambda item: (
                    item["title"].casefold(), item["author"].casefold(),
                    item["created_at"], item["id"]
                )
            )
            with open(path, "w", encoding="utf-8") as output:
                output.write("# BookTracker Reading Notes\n\n")
                last_book = None
                for item in entries:
                    if item["work_id"] != last_book:
                        output.write(
                            f"\n## {item['title']} — {item['author']}\n\n"
                        )
                        last_book = item["work_id"]
                    where = (
                        f" · {item['location']}%"
                        if item["location_unit"] == "percent"
                        else f" · {'Page' if item['location_unit'] == 'pages' else 'Chapter'} "
                             f"{item['location']}"
                    ) if item["location"] is not None else ""
                    output.write(
                        f"### {name(item['kind'])}{where}\n\n"
                        f"{item['body'] or '(bookmark)'}\n\n"
                    )
            self.statusBar().showMessage(
                f"Exported {len(entries)} saved notes.", 7000
            )
        except OSError as exc:
            fail(self, exc)

    def create_updates_page(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        heading=QHBoxLayout()
        heading.addWidget(label("Update center","headline"),stretch=1)
        heading.addWidget(button(
            "Check eligible stories",self.check_serial_sources,primary=True
        ))
        self.update_cancel_button=button(
            "Cancel check",self.cancel_serial_checks
        )
        self.update_cancel_button.setEnabled(False)
        heading.addWidget(self.update_cancel_button)
        layout.addLayout(heading)
        layout.addWidget(label(
            "Follow ongoing fanfiction and LitRPG, distinguish newly detected "
            "chapters from unread backlog, and keep historical release observations. "
            "Checks use supported public sites only and never bypass Cloudflare.",
            "muted"
        ))
        switches=QGridLayout()
        self.update_followed_only=QCheckBox("Check followed stories only")
        self.update_followed_only.setChecked(True)
        switches.addWidget(self.update_followed_only,0,0)
        self.update_override_cooldown=QCheckBox("Override 12-hour cooldown")
        switches.addWidget(self.update_override_cooldown,0,1)
        self.update_auto_check=QCheckBox("Check followed on startup (24-hour minimum)")
        self.update_auto_check.setChecked(
            self.settings.value("serial_auto_check",False,type=bool)
        )
        self.update_auto_check.toggled.connect(
            lambda value:self.settings.setValue("serial_auto_check",value)
        )
        switches.addWidget(self.update_auto_check,1,0)
        self.update_alerts=QCheckBox("Desktop alerts")
        self.update_alerts.setChecked(
            self.settings.value("serial_alerts",False,type=bool)
        )
        self.update_alerts.toggled.connect(
            lambda value:self.settings.setValue("serial_alerts",value)
        )
        switches.addWidget(self.update_alerts,1,1)
        layout.addLayout(switches)
        self.update_progress=QProgressBar()
        self.update_progress.setRange(0,1)
        self.update_progress.setValue(0)
        self.update_progress.setFormat("No check running")
        layout.addWidget(self.update_progress)
        filter_bar=QHBoxLayout()
        self.update_search=QLineEdit()
        self.update_search.setPlaceholderText("Search titles or authors…")
        self.update_search.textChanged.connect(self.refresh_updates_page)
        filter_bar.addWidget(self.update_search,stretch=2)
        self.update_filter=QComboBox()
        for title,key in (
            ("All tracked serials","all"),("Following","following"),
            ("New since acknowledged","new"),("Unread known chapters","unread"),
            ("Failed checks","errors"),("Never checked","unchecked"),
            ("No growth observed in 90+ days","quiet")
        ):
            self.update_filter.addItem(title,key)
        self.update_filter.currentIndexChanged.connect(self.refresh_updates_page)
        filter_bar.addWidget(self.update_filter,stretch=1)
        layout.addLayout(filter_bar)
        self.update_counts=label("","subheading")
        layout.addWidget(self.update_counts)
        self.update_table=QTableWidget(0,9)
        self.update_table.setHorizontalHeaderLabels([
            "Work","Following","New","Unread","Read / known",
            "Sources","Latest growth seen","Last checked","Status"
        ])
        self.update_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.update_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.update_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.update_table.verticalHeader().setVisible(False)
        self.update_table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Interactive
        )
        self.update_table.horizontalHeader().resizeSection(0,270)
        for i in range(1,9):
            self.update_table.horizontalHeader().setSectionResizeMode(
                i,QHeaderView.ResizeMode.ResizeToContents
            )
        self.update_table.itemSelectionChanged.connect(self.refresh_update_actions)
        self.update_table.itemDoubleClicked.connect(
            lambda _:self.manage_update_source()
        )
        layout.addWidget(self.update_table,stretch=1)
        actions=QHBoxLayout()
        self.update_follow_button=button("Follow story",self.toggle_follow_selected)
        self.update_notify_button=button(
            "Enable story alerts",self.toggle_selected_notifications
        )
        self.update_seen_button=button("Acknowledge new",self.acknowledge_selected)
        for control in (
            self.update_follow_button,self.update_notify_button,self.update_seen_button
        ):
            actions.addWidget(control)
        actions.addWidget(button(
            "Mark caught up",self.catch_up_selected_serial
        ))
        actions.addWidget(button("Open source",self.open_update_source))
        actions.addWidget(button(
            "Sources & editions",self.manage_update_source
        ))
        layout.addLayout(actions)
        bottom=QHBoxLayout()
        bottom.addWidget(button("Release history",self.show_serial_release_history))
        bottom.addWidget(button("Provider reliability",self.show_provider_reliability))
        bottom.addWidget(button("Export releases CSV",self.export_serial_releases_csv))
        bottom.addWidget(button("Open book",self.open_update_work))
        bottom.addStretch()
        layout.addLayout(bottom)
        layout.addWidget(label(
            "New = increases observed after a previously known chapter count, "
            "until you acknowledge them. Unread = known published chapters "
            "minus your saved reading position. The first successful check "
            "establishes a baseline; failures do not imply a deleted story. "
            "Bulk checks are limited to 20 stories per run, with a 1.5-second "
            "gap and an optional cooldown. Automatic checks and desktop alerts "
            "are off until you enable them.",
            "muted"
        ))
        self.pages.addWidget(page)

    def refresh_updates_page(self,*_args):
        rows=self.library.source_update_summary()
        search=self.update_search.text().strip().casefold()
        mode=self.update_filter.currentData()
        selected=self.selected_update_work_id()
        visible=[]
        for entry in rows:
            if search and not (
                search in entry["title"].casefold() or
                search in entry["author"].casefold()
            ):
                continue
            if mode=="following" and not entry["following"]:
                continue
            if mode=="new" and not entry["new_since_seen"]:
                continue
            if mode=="unread" and not entry["unread"]:
                continue
            if mode=="errors" and not entry["has_error"]:
                continue
            if mode=="unchecked" and entry["last_checked_at"]:
                continue
            if mode=="quiet" and not entry["quiet_days"]:
                continue
            visible.append(entry)
        follows=sum(bool(entry["following"]) for entry in rows)
        new=sum(entry["new_since_seen"] for entry in rows)
        backlog=sum(entry["unread"] for entry in rows)
        self.update_counts.setText(
            f"{len(rows)} tracked serials · {follows} followed · "
            f"{new:,} newly observed chapters · {backlog:,} unread chapters"
        )
        self.update_table.blockSignals(True)
        self.update_table.setRowCount(len(visible))
        selected_row=-1
        for row,entry in enumerate(visible):
            if entry["work_id"]==selected:
                selected_row=row
            values=(
                entry["title"],
                "★" if entry["following"] else "—",
                str(entry["new_since_seen"]) if entry["new_since_seen"] else "—",
                str(entry["unread"]),
                f"{entry['progress']} / {entry['known_chapters']}",
                str(entry["source_count"]),
                local_timestamp(entry["last_release_at"])[:10]
                  if entry["last_release_at"] else "No increase recorded",
                local_timestamp(entry["last_checked_at"])
                  if entry["last_checked_at"] else "Never",
                "Check blocked" if entry["has_error"]
                else f"No growth observed {entry['quiet_days']}d"
                  if entry["quiet_days"] else name(entry["status"])
            )
            for col,value in enumerate(values):
                item=QTableWidgetItem(value)
                if col==0:
                    item.setData(Qt.ItemDataRole.UserRole,entry["work_id"])
                self.update_table.setItem(row,col,item)
            self.update_table.setRowHeight(row,39)
        self.update_table.blockSignals(False)
        if selected_row>=0:
            self.update_table.selectRow(selected_row)
        elif visible:
            self.update_table.selectRow(0)
        self.refresh_update_actions()

    def refresh_update_actions(self):
        work_id=self.selected_update_work_id()
        follow=self.library.get_follow(work_id) if work_id else None
        self.update_follow_button.setEnabled(bool(work_id))
        self.update_follow_button.setText(
            "Unfollow story" if follow else "Follow story"
        )
        self.update_notify_button.setEnabled(bool(follow))
        self.update_notify_button.setText(
            "Disable story alerts" if follow and follow["notify"]
            else "Enable story alerts"
        )
        self.update_seen_button.setEnabled(bool(follow))

    def toggle_library_story_follow(self):
        work=self.selected_work()
        if not work:
            return
        try:
            if self.library.get_follow(work["id"]):
                self.library.unfollow_work(work["id"])
                self.statusBar().showMessage("Stopped following story.",5000)
            else:
                self.library.follow_work(work["id"],notify=False)
                self.statusBar().showMessage(
                    "Following story. Enable alerts in Update center if desired.",6000
                )
            self.refresh_all(work["id"])
        except Exception as exc:
            fail(self,exc)

    def toggle_follow_selected(self):
        key=self.selected_update_work_id()
        if not key:
            return
        try:
            if self.library.get_follow(key):
                if QMessageBox.question(
                    self,"Unfollow story?",
                    "Unfollow this story? Book and release-check history remain."
                )!=QMessageBox.StandardButton.Yes:
                    return
                self.library.unfollow_work(key)
            else:
                self.library.follow_work(key,notify=False)
            self.refresh_updates_page()
        except Exception as exc:
            fail(self,exc)

    def toggle_selected_notifications(self):
        key=self.selected_update_work_id()
        follow=self.library.get_follow(key) if key else None
        if not follow:
            return
        self.library.set_follow_notification(key,not follow["notify"])
        self.refresh_updates_page()

    def acknowledge_selected(self):
        key=self.selected_update_work_id()
        if key and self.library.get_follow(key):
            self.library.acknowledge_releases(key)
            self.refresh_updates_page()

    def catch_up_selected_serial(self):
        key=self.selected_update_work_id()
        work=self.library.get_work(key) if key else None
        if not work:
            return
        if QMessageBox.question(
            self,"Mark caught up?",
            f"Record reading progress through {work['total_units']} chapters "
            f"of {work['title']}? This writes a real progress milestone."
        )!=QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.catch_up_to_published(key)
            if self.library.get_follow(key):
                self.library.acknowledge_releases(key)
            self.refresh_all(key)
            self.refresh_updates_page()
        except Exception as exc:
            fail(self,exc)

    def open_update_source(self):
        key=self.selected_update_work_id()
        sources=self.library.list_sources(key) if key else []
        if sources:
            webbrowser.open(sources[0]["url"])

    def maybe_check_on_launch(self):
        if not self.settings.value("serial_auto_check",False,type=bool):
            return
        self.check_serial_sources(only_followed=True,startup=True)

    def check_serial_sources(self,*_args,only_followed=None,startup=False):
        if self.serial_worker and self.serial_worker.isRunning():
            self.statusBar().showMessage("A serial-check batch is already running.",5000)
            return
        followed = (
            bool(only_followed) if only_followed is not None
            else self.update_followed_only.isChecked()
        )
        minimum = 24 if startup else (
            0 if self.update_override_cooldown.isChecked() else 12
        )
        result=self.library.batch_source_candidates(
            followed_only=followed,minimum_hours=minimum,
            limit=8 if startup else 20
        )
        pending=result["candidates"]
        if not pending:
            self.statusBar().showMessage(
                "No eligible stories due for checking. "
                f"{result['cooldown']} are within the cooldown; "
                f"{result['unsupported']} have no supported source.",10000
            )
            return
        self._serial_added=[]
        self._serial_errors=0
        self._serial_completed=0
        self._serial_cancelled=False
        self.update_progress.setRange(0,len(pending))
        self.update_progress.setValue(0)
        self.update_progress.setFormat(
            f"Checking 0/{len(pending)} supported story sources"
        )
        self.update_cancel_button.setEnabled(True)
        worker=SerialCheckThread(pending)
        self.serial_worker=worker
        worker.status.connect(self._serial_check_status)
        worker.result.connect(self._serial_check_result)
        worker.failed.connect(self._serial_check_failed)
        worker.finished.connect(self._serial_check_finished)
        worker.start()

    def _serial_check_status(self,index,total,title):
        self.update_progress.setFormat(
            f"Checking {index}/{total}: {title[:60]}"
        )

    def _serial_check_result(self,source_id,data):
        previous=self.library.get_source(source_id)
        if not previous:
            return
        key=previous["work_id"]
        known_before=self.library.get_work(key)["total_units"]
        count=data.get("total_units") or 0
        try:
            self.library.record_source_check(
                source_id,observed_units=count,message="Checked public story metadata."
            )
            self._serial_completed+=1
            follow=self.library.get_follow(key)
            after=self.library.get_work(key)["total_units"]
            if (
                known_before>0 and after>known_before and follow
                and follow["notify"] and self.update_alerts.isChecked()
            ):
                self._serial_added.append((self.library.get_work(key)["title"],
                                           after-known_before))
        except Exception as exc:
            self._serial_errors+=1
            self.statusBar().showMessage(f"Story check could not be saved: {exc}",9000)
        self.update_progress.setValue(
            min(self.update_progress.maximum(),
                self._serial_completed+self._serial_errors)
        )
        self.refresh_updates_page()

    def _serial_check_failed(self,source_id,message):
        self._serial_errors+=1
        if self.library.get_source(source_id):
            try:
                self.library.record_source_check(source_id,error=message)
            except Exception as exc:
                self.statusBar().showMessage(f"Could not store check error: {exc}",7000)
        self.update_progress.setValue(
            min(self.update_progress.maximum(),
                self._serial_completed+self._serial_errors)
        )
        self.refresh_updates_page()

    def cancel_serial_checks(self):
        worker=self.serial_worker
        if worker and worker.isRunning():
            self._serial_cancelled=True
            worker.requestInterruption()
            self.update_progress.setFormat(
                "Stopping after the active request completes…"
            )
            self.update_cancel_button.setEnabled(False)

    def _serial_check_finished(self):
        self.update_cancel_button.setEnabled(False)
        self.update_progress.setFormat(
            f"{'Stopped' if self._serial_cancelled else 'Complete'}: "
            f"{self._serial_completed} checked, {self._serial_errors} failed"
        )
        self.statusBar().showMessage(
            self.update_progress.format(),12000
        )
        if self._serial_added:
            self._show_serial_alert()
        self.refresh_updates_page()

    def _show_serial_alert(self):
        if not self.update_alerts.isChecked():
            return
        total=sum(delta for _,delta in self._serial_added)
        names=", ".join(title for title,_ in self._serial_added[:3])
        message=f"{total} newly observed chapters across {len(self._serial_added)} stories: {names}"
        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._serial_tray is None:
                self._serial_tray=QSystemTrayIcon(self.windowIcon(),self)
                self._serial_tray.setToolTip("BookTracker")
                self._serial_tray.show()
            self._serial_tray.showMessage(
                "BookTracker chapter updates",message,
                QSystemTrayIcon.MessageIcon.Information,8000
            )
        else:
            QApplication.alert(self,5000)
        self.statusBar().showMessage(message,12000)

    def show_serial_release_history(self):
        releases=self.library.list_serial_releases(limit=2000)
        if not releases:
            QMessageBox.information(
                self,"No observed releases",
                "BookTracker has not recorded any *increases* since a known "
                "chapter count. A first check establishes a baseline."
            )
            return
        dialog=QDialog(self)
        dialog.setWindowTitle("Observed chapter release history")
        dialog.resize(950,570)
        layout=QVBoxLayout(dialog)
        layout.addWidget(label(
            "An observation date is when BookTracker detected the increase, "
            "not necessarily the story's actual publication date.","muted"
        ))
        table=QTableWidget(len(releases),5)
        table.setHorizontalHeaderLabels([
            "Observed","Story","Source","Old → new","Chapters gained"
        ])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            1,QHeaderView.ResizeMode.Stretch
        )
        for row,entry in enumerate(releases):
            columns=(
                local_timestamp(entry["observed_at"]),
                entry["title"],entry["site"] or "Deleted source",
                f"{entry['old_units']} → {entry['new_units']}",
                f"+{entry['new_units']-entry['old_units']}"
            )
            for col,value in enumerate(columns):
                table.setItem(row,col,QTableWidgetItem(value))
        layout.addWidget(table)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_provider_reliability(self):
        sources=self.library.source_reliability()
        if not sources:
            QMessageBox.information(
                self,"No source checks","No site checks have been recorded."
            )
            return
        dialog=QDialog(self)
        dialog.setWindowTitle("Source provider reliability")
        dialog.resize(790,480)
        layout=QVBoxLayout(dialog)
        layout.addWidget(label(
            "Observed requests only. A blocked or unknown response doesn't "
            "mean the story is gone.","muted"
        ))
        table=QTableWidget(len(sources),5)
        table.setHorizontalHeaderLabels([
            "Provider","Checks","Known counts","Errors","Last checked"
        ])
        table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        for i,entry in enumerate(sources):
            values=(
                entry["site"],str(entry["checks"]),
                str(entry["successful_counts"]),str(entry["failures"]),
                local_timestamp(entry["last_checked_at"])
            )
            for col,value in enumerate(values):
                table.setItem(i,col,QTableWidgetItem(value))
        layout.addWidget(table)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def export_serial_releases_csv(self):
        path,_=QFileDialog.getSaveFileName(
            self,"Export observed chapter releases",
            "BookTracker-chapter-releases.csv","CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with Path(path).open("w",newline="",encoding="utf-8-sig") as target:
                writer=csv.writer(target)
                writer.writerow([
                    "Observed at","Work","Author","Source","Previous chapters",
                    "Observed chapters","New chapters","Note"
                ])
                for item in self.library.list_serial_releases(limit=10000):
                    def safe(value):
                        text=str(value or "")
                        return (
                            "'"+text if text.lstrip().startswith(("=","+","-","@"))
                            else text
                        )
                    writer.writerow([
                        item["observed_at"],safe(item["title"]),safe(item["author"]),
                        safe(item["site"]),item["old_units"],item["new_units"],
                        item["new_units"]-item["old_units"],safe(item["note"])
                    ])
            self.statusBar().showMessage("Exported observed release history.",7000)
        except OSError as exc:
            fail(self,exc)

    def selected_update_work_id(self):
        row = self.update_table.currentRow()
        cell = self.update_table.item(row,0) if row>=0 else None
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def manage_update_source(self):
        key = self.selected_update_work_id()
        if not key:
            QMessageBox.information(self,"Choose story","Select a tracked story.")
            return
        self.manage_catalog_for(key)

    def open_update_work(self):
        key=self.selected_update_work_id()
        if not key:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=key)

    def manage_catalog_for(self, work_id):
        if not self.library.get_work(work_id):
            return
        dialog = CatalogManagerDialog(self,self.library,work_id)
        dialog.exec()
        self.refresh_all(work_id)
        if self.pages.currentIndex()==9:
            self.refresh_updates_page()

    def manage_work_catalog(self):
        work=self.selected_work()
        if not work:
            QMessageBox.information(
                self,"Select book","Choose a work before editing its sources."
            )
            return
        self.manage_catalog_for(work["id"])

    def create_queue_page(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        top=QHBoxLayout()
        top.addWidget(label("Reading queue","headline"),stretch=1)
        top.addWidget(button("+ Plan a book",self.queue_new_book,primary=True))
        top.addWidget(button("Surprise me",self.pick_queue_book))
        layout.addLayout(top)
        layout.addWidget(label(
            "Plan what to read next without changing completion status or "
            "inventing historical reading sessions. Reorder works, set "
            "priorities and optional target dates.", "muted"
        ))
        filters=QHBoxLayout()
        self.queue_search=QLineEdit()
        self.queue_search.setPlaceholderText(
            "Search queued titles, authors and planning notes…"
        )
        self.queue_search.textChanged.connect(self.refresh_queue_page)
        filters.addWidget(self.queue_search,stretch=3)
        self.queue_priority=QComboBox()
        self.queue_priority.addItem("All priorities",None)
        for key, text in (
            ("next","Next up"),("high","High"),("normal","Normal"),("low","Someday")
        ):
            self.queue_priority.addItem(text,key)
        self.queue_priority.currentIndexChanged.connect(self.refresh_queue_page)
        filters.addWidget(self.queue_priority,stretch=1)
        layout.addLayout(filters)
        self.queue_summary_label=label("","subheading")
        layout.addWidget(self.queue_summary_label)
        self.queue_table=QTableWidget(0,7)
        self.queue_table.setHorizontalHeaderLabels([
            "#","Title","Priority","Target","Reading","Progress","Plan / note"
        ])
        self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.queue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(
            1,QHeaderView.ResizeMode.Stretch
        )
        self.queue_table.horizontalHeader().setSectionResizeMode(
            6,QHeaderView.ResizeMode.Stretch
        )
        for index in (0,2,3,4,5):
            self.queue_table.horizontalHeader().setSectionResizeMode(
                index,QHeaderView.ResizeMode.ResizeToContents
            )
        self.queue_table.itemDoubleClicked.connect(lambda _:self.open_queue_book())
        layout.addWidget(self.queue_table,stretch=1)
        actions=QHBoxLayout()
        for caption,func in (
            ("Move ↑",lambda:self.move_queued(-1)),
            ("Move ↓",lambda:self.move_queued(1)),
            ("Edit plan",self.edit_queue_plan),
            ("Open book",self.open_queue_book),
            ("Start reading",self.start_queue_book),
        ):
            actions.addWidget(button(caption,func))
        layout.addLayout(actions)
        layout.addWidget(button(
            "Remove selected from queue (book remains in your library)",
            self.remove_queue_book,danger=True
        ))
        layout.addWidget(label(
            "Moving entries changes the full queue order, even when a search or "
            "priority filter is active. Dates are private planning targets, "
            "not scheduled reminders.", "muted"
        ))
        self.pages.addWidget(page)

    def selected_queue_work_id(self):
        row=self.queue_table.currentRow()
        item=self.queue_table.item(row,1) if row>=0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_queue_page(self,*_args):
        current=self.selected_queue_work_id()
        entries=self.library.list_queue(
            search=self.queue_search.text(),
            priority=self.queue_priority.currentData()
        )
        full=self.library.list_queue()
        order={entry["work_id"]:i+1 for i,entry in enumerate(full)}
        self.queue_table.blockSignals(True)
        self.queue_table.setRowCount(len(entries))
        chosen=-1
        today=QDate.currentDate().toString("yyyy-MM-dd")
        for i,entry in enumerate(entries):
            if entry["work_id"]==current:
                chosen=i
            current_progress=entry["progress"]
            total=entry["total_units"]
            progress=(f"{current_progress} / {total}" if total
                      else str(current_progress))
            target=entry["target_date"] or "—"
            if entry["target_date"] and entry["target_date"]<today:
                target += " · overdue"
            values=(
                str(order[entry["work_id"]]),entry["title"],
                name(entry["priority"]),target,
                name(entry["status"]),progress,
                entry["note"].replace("\n"," ")[:150] or "—"
            )
            for col,value in enumerate(values):
                item=QTableWidgetItem(value)
                if col==1:
                    item.setData(Qt.ItemDataRole.UserRole,entry["work_id"])
                self.queue_table.setItem(i,col,item)
            self.queue_table.setRowHeight(i,39)
        self.queue_table.blockSignals(False)
        if chosen>=0:
            self.queue_table.selectRow(chosen)
        elif entries:
            self.queue_table.selectRow(0)
        summary=self.library.queue_summary()
        self.queue_summary_label.setText(
            f"{summary['total']} queued  ·  {summary['next']} marked next up  ·  "
            f"{summary['in_progress']} in progress  ·  "
            f"{summary['overdue']} overdue targets"
        )

    def queue_new_book(self):
        if not self.library.list_works():
            QMessageBox.information(
                self,"Empty library","Add a book before creating a reading plan."
            )
            return
        dialog=QueueEntryDialog(self,self.library)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        key=dialog.book.currentData()
        try:
            current=self.library.get_queue_entry(key)
            if current:
                QMessageBox.information(
                    self,"Already queued",
                    "This book is already in your queue. Select it and choose Edit plan."
                )
                return
            self.library.enqueue_work(key,**dialog.values())
            self.refresh_all(key)
            self.refresh_queue_page()
        except Exception as exc:
            fail(self,exc)

    def queue_current_book(self):
        book=self.selected_work()
        if not book:
            return
        try:
            self.library.enqueue_work(book["id"])
            self.refresh_all(book["id"])
            self.statusBar().showMessage("Added to reading queue.",6000)
        except Exception as exc:
            fail(self,exc)

    def queue_selected_books(self):
        selected=self.selected_ids()
        if not selected:
            QMessageBox.information(
                self,"Choose books","Select one or more library entries first."
            )
            return
        added=0
        try:
            for key in dict.fromkeys(selected):
                if self.library.get_queue_entry(key):
                    continue
                self.library.enqueue_work(key)
                added+=1
            self.refresh_all()
            self.statusBar().showMessage(
                f"Added {added} works to reading queue.",8000
            )
        except Exception as exc:
            fail(self,exc)

    def edit_queue_plan(self):
        key=self.selected_queue_work_id()
        if not key:
            return
        current=self.library.get_queue_entry(key)
        if not current:
            return
        dialog=QueueEntryDialog(self,self.library,existing=current)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        try:
            self.library.update_queue_entry(key,**dialog.values())
            self.refresh_all()
            self.refresh_queue_page()
        except Exception as exc:
            fail(self,exc)

    def move_queued(self,delta):
        key=self.selected_queue_work_id()
        if not key:
            return
        try:
            self.library.move_queue_entry(key,delta)
            self.refresh_queue_page()
        except Exception as exc:
            fail(self,exc)

    def remove_queue_book(self):
        key=self.selected_queue_work_id()
        if not key:
            return
        if QMessageBox.question(
            self,"Remove from reading queue?",
            "Remove this reading plan? The book and all reading history remain."
        )!=QMessageBox.StandardButton.Yes:
            return
        self.library.remove_from_queue(key)
        self.refresh_all()
        self.refresh_queue_page()

    def open_queue_book(self):
        key=self.selected_queue_work_id()
        if not key:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=key)

    def start_queue_book(self):
        key=self.selected_queue_work_id()
        if not key:
            return
        work=self.library.get_work(key)
        if work["status"]=="completed" and QMessageBox.question(
            self,"Start reread?",
            "This work is already completed. Create a new reread?"
        )!=QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.start_reading(
                key,reread=work["status"]=="completed"
            )
            self.library.remove_from_queue(key)
            self.refresh_all(key)
            self.refresh_queue_page()
            self.statusBar().showMessage(
                f"Started reading {work['title']}.",6500
            )
        except Exception as exc:
            fail(self,exc)

    def pick_queue_book(self):
        entries=self.library.list_queue(
            search=self.queue_search.text(),
            priority=self.queue_priority.currentData()
        )
        if not entries:
            QMessageBox.information(self,"Queue empty","No queued books match these filters.")
            return
        import secrets
        picked=secrets.choice(entries)
        self.queue_table.selectRow(next(
            i for i in range(self.queue_table.rowCount())
            if self.queue_table.item(i,1).data(Qt.ItemDataRole.UserRole)==picked["work_id"]
        ))
        self.statusBar().showMessage(
            f"Random pick: {picked['title']}",7500
        )

    def relink_library_files(self):
        folder=QFileDialog.getExistingDirectory(
            self,"Find missing book and audiobook files"
        )
        if not folder:
            return
        try:
            result=self.library.relink_from_directory(folder)
            self.refresh_all()
            QMessageBox.information(
                self,"Library file relinking",
                f"Relinked: {result['relinked']}\n"
                f"Missing: {result['missing']}\n"
                f"Ambiguous matches skipped: {result['ambiguous']}\n"
                f"Files scanned: {result['scanned']}\n\n"
                "No book/audio files were changed or copied."
            )
        except Exception as exc:
            fail(self,exc)

    def read_selected_epub(self):
        work=self.selected_work()
        if not work:
            QMessageBox.information(
                self,"Choose a book","Select a book with a linked EPUB first."
            )
            return
        files=[
            file for file in self.library.list_local_files(work["id"])
            if file["format"]=="epub"
        ]
        if not files:
            QMessageBox.information(
                self,"No EPUB linked",
                "Use Local files → + Attach files to link an EPUB first."
            )
            return
        if len(files)==1:
            selected=files[0]
        else:
            options=[
                f"{file['filename']} — {'Available' if file['available'] else 'Missing'}"
                for file in files
            ]
            name,okay=QInputDialog.getItem(
                self,"Choose an EPUB","Linked editions:",options,0,False
            )
            if not okay:
                return
            selected=files[options.index(name)]
        try:
            reader=EpubReader(self,self.library,selected["id"])
            reader.exec()
            self.refresh_all(work["id"])
        except (OSError,ValueError,KeyError) as exc:
            QMessageBox.warning(self,"EPUB reader",str(exc))

    def manage_local_files(self):
        work=self.selected_work()
        if not work:
            QMessageBox.information(self,"Choose book","Select a book first.")
            return
        dialog=LocalFilesDialog(self,self.library,work["id"])
        dialog.exec()
        self.refresh_all(work["id"])

    def create_habits_page(self):
        wrapper=QWidget()
        wrapper_layout=QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0,0,0,0)
        scroll=QScrollArea()
        scroll.setObjectName("habitsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea#habitsScroll { background: #10151f; border: none; }"
            "QWidget#habitsContent { background: #10151f; }"
        )
        wrapper_layout.addWidget(scroll)
        page=QWidget()
        page.setObjectName("habitsContent")
        layout=QVBoxLayout(page)
        layout.setSpacing(12)
        header=QHBoxLayout()
        header.addWidget(label("Reading habits","headline"),stretch=1)
        header.addWidget(button("Start timer",self.open_reading_timer,primary=True))
        header.addWidget(button("Export daily CSV",self.export_habits_csv))
        layout.addLayout(header)
        layout.addWidget(label(
            "Only actual dated milestones and manually logged reading/listening "
            "minutes count. Unrecorded time and completion dates are never guessed.",
            "muted"
        ))
        filters=QHBoxLayout()
        filters.addWidget(label("Period:"))
        self.habits_period=QComboBox()
        for days,text in (
            (7,"Last 7 days"),(30,"Last 30 days"),(90,"Last 90 days"),
            (365,"Last 365 days"),(730,"Last 2 years")
        ):
            self.habits_period.addItem(text,days)
        self.habits_period.setCurrentIndex(1)
        self.habits_period.currentIndexChanged.connect(self.refresh_habits_page)
        filters.addWidget(self.habits_period)
        filters.addStretch()
        self.habits_streak=label("","subheading")
        filters.addWidget(self.habits_streak)
        layout.addLayout(filters)
        summaries=QGridLayout()
        self.habits_cards={}
        for position,(key,title) in enumerate((
            ("active_days","Active days"),("minutes","Total logged time"),
            ("reading_minutes","Reading time"),("listening_minutes","Listening time"),
            ("chapters","Chapters"),("pages","Pages"),
            ("completions","Known completions"),("longest_streak","Longest streak")
        )):
            box=QFrame()
            box.setObjectName("statCard")
            inner=QVBoxLayout(box)
            inner.addWidget(label(title,"muted"))
            current=label("0","value")
            inner.addWidget(current)
            self.habits_cards[key]=current
            summaries.addWidget(box,position//4,position%4)
        layout.addLayout(summaries)
        panels=QHBoxLayout()
        weekdays=QVBoxLayout()
        weekdays.addWidget(label("Weekday activity","subheading"))
        self.habits_weekday_bars=[]
        for name_day in ("Mon","Tue","Wed","Thu","Fri","Sat","Sun"):
            line=QHBoxLayout()
            tag=label(name_day)
            tag.setFixedWidth(40)
            line.addWidget(tag)
            progress=QProgressBar()
            progress.setRange(0,100)
            progress.setValue(0)
            progress.setFormat("No time recorded")
            line.addWidget(progress,stretch=1)
            weekdays.addLayout(line)
            self.habits_weekday_bars.append(progress)
        weekdays.addWidget(label(
            "Bars compare logged reading + listening minutes by weekday. "
            "A milestone without a session counts as an active day but has no "
            "estimated duration.", "muted"
        ))
        panels.addLayout(weekdays,stretch=1)
        weekly=QVBoxLayout()
        weekly.addWidget(label("Week-by-week history","subheading"))
        self.habits_weeks=QTableWidget(0,5)
        self.habits_weeks.setMinimumHeight(240)
        self.habits_weeks.setHorizontalHeaderLabels([
            "Week of","Active days","Read","Listened","Chapters / pages"
        ])
        self.habits_weeks.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.habits_weeks.verticalHeader().setVisible(False)
        self.habits_weeks.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        for col in (1,2,3,4):
            self.habits_weeks.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        weekly.addWidget(self.habits_weeks,stretch=1)
        panels.addLayout(weekly,stretch=2)
        layout.addLayout(panels,stretch=2)
        layout.addWidget(label("Books with the most logged time","subheading"))
        self.habits_top=QTableWidget(0,3)
        self.habits_top.setMinimumHeight(135)
        self.habits_top.setHorizontalHeaderLabels(
            ["Book","Reading minutes","Listening minutes"]
        )
        self.habits_top.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.habits_top.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.habits_top.verticalHeader().setVisible(False)
        self.habits_top.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        for col in (1,2):
            self.habits_top.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        self.habits_top.itemDoubleClicked.connect(self.open_habits_work)
        layout.addWidget(self.habits_top,stretch=1)
        layout.addWidget(label(
            "Annual goal pace  ·  Set or change yearly targets in Dashboard",
            "subheading"
        ))
        self.habits_goal_summary=label("","muted")
        layout.addWidget(self.habits_goal_summary)
        self.habits_goal_bars={}
        goals_row=QHBoxLayout()
        for key,text in (("books","Books"),("chapters","Chapters"),("pages","Pages")):
            group=QVBoxLayout()
            group.addWidget(label(text))
            bar=QProgressBar()
            bar.setRange(0,100)
            bar.setValue(0)
            bar.setFormat("No goal set")
            group.addWidget(bar)
            self.habits_goal_bars[key]=bar
            goals_row.addLayout(group)
        layout.addLayout(goals_row)
        scroll.setWidget(page)
        self.pages.addWidget(wrapper)

    def refresh_habits_page(self,*_args):
        overview=self.library.reading_habits(days=self.habits_period.currentData())
        self._habits_data=overview
        self.habits_streak.setText(
            f"Current streak: {self.library.reading_streak()} days  ·  "
            f"{overview['active_days']}/{overview['days']} active days"
        )
        pace=self.library.annual_goal_pacing()
        self.habits_goal_summary.setText(
            f"{pace['year']} targets · {pace['elapsed_days']}/{pace['total_days']} "
            "calendar days elapsed. Projection assumes the same recorded pace "
            "for the rest of the year."
        )
        for key,bar in self.habits_goal_bars.items():
            goal=pace["metrics"][key]
            if goal["target"]:
                bar.setValue(min(100,round(goal["percent"])))
                ahead="On pace" if goal["on_pace"] else "Behind pace"
                bar.setFormat(
                    f"{goal['actual']:,} / {goal['target']:,} · {ahead} · "
                    f"Projected {goal['projected_year_end']:,.0f}"
                )
            else:
                bar.setValue(0)
                bar.setFormat(f"{goal['actual']:,} recorded; no goal")

        for key,widget in self.habits_cards.items():
            value=overview[key]
            if key.endswith("_minutes") or key=="minutes":
                widget.setText(f"{value/60:.1f} h")
            else:
                widget.setText(f"{value:,}")
        high=max((item["minutes"] for item in overview["weekdays"]),default=0)
        for i,entry in enumerate(overview["weekdays"]):
            bar=self.habits_weekday_bars[i]
            bar.setValue(round(100*entry["minutes"]/high) if high else 0)
            bar.setFormat(
                f"{entry['minutes']} min  ·  {entry['active_days']} active days"
            )
        shown=list(reversed(overview["weeks"][-16:]))
        self.habits_weeks.setRowCount(len(shown))
        for row,week in enumerate(shown):
            values=(
                week["week_start"],str(week["active_days"]),
                str(week["reading_minutes"]),str(week["listening_minutes"]),
                f"{week['chapters']} / {week['pages']}"
            )
            for col,value in enumerate(values):
                self.habits_weeks.setItem(row,col,QTableWidgetItem(value))
            self.habits_weeks.setRowHeight(row,34)
        top=overview["top_books"][:12]
        self.habits_top.setRowCount(len(top))
        for row,work in enumerate(top):
            for col,value in enumerate((
                work["title"],str(work["reading_minutes"]),
                str(work["listening_minutes"])
            )):
                item=QTableWidgetItem(value)
                if col==0:
                    item.setData(Qt.ItemDataRole.UserRole,work["work_id"])
                self.habits_top.setItem(row,col,item)
            self.habits_top.setRowHeight(row,35)

    def open_habits_work(self,item=None):
        row=self.habits_top.currentRow()
        cell=self.habits_top.item(row,0) if row>=0 else None
        if not cell:
            return
        work_id=cell.data(Qt.ItemDataRole.UserRole)
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=work_id)

    def export_habits_csv(self):
        data=self.library.reading_habits(days=self.habits_period.currentData())
        path,_=QFileDialog.getSaveFileName(
            self,"Export daily reading activity",
            f"BookTracker-habits-{data['start']}-to-{data['end']}.csv",
            "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with Path(path).open("w",encoding="utf-8-sig",newline="") as handle:
                writer=csv.writer(handle)
                writer.writerow([
                    "Date","Reading minutes","Listening minutes","Chapters",
                    "Pages","Sessions","Milestones"
                ])
                for day in data["daily"]:
                    writer.writerow([
                        day["date"],day["reading_minutes"],day["listening_minutes"],
                        day["chapters"],day["pages"],day["sessions"],day["milestones"]
                    ])
            self.statusBar().showMessage(
                f"Exported {len(data['daily'])} active days.",6500
            )
        except OSError as exc:
            fail(self,exc)

    def open_reading_timer(self, *, work_id=None, mode="reading"):
        if not self.library.list_works():
            QMessageBox.information(
                self,"Library empty","Add a book before timing your reading."
            )
            return
        dialog=ReadingTimerDialog(
            self,self.library,work_id=work_id or self.selected_id,mode=mode
        )
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        try:
            key=dialog.book.currentData()
            self.library.add_session(key,**dialog.values())
            self.refresh_all(key)
            self.statusBar().showMessage(
                f"Saved {dialog.values()['minutes']} real minutes.",7000
            )
        except Exception as exc:
            fail(self,exc)

    def import_kindle_clippings(self):
        path,_=QFileDialog.getOpenFileName(
            self,"Import Kindle My Clippings.txt","","Text files (*.txt)"
        )
        if not path:
            return
        try:
            parsed=read_clippings(path)
            groups=group_clippings(parsed["records"])
            if len(groups)>300:
                raise ValueError(
                    "This export contains over 300 distinct Kindle titles. "
                    "Split the file into smaller batches before importing."
                )
            dialog=ClippingsPreviewDialog(self,self.library,parsed)
            if dialog.exec()!=QDialog.DialogCode.Accepted:
                return
            result=self.library.import_clippings(
                parsed["records"],dialog.decisions()
            )
            self.refresh_all()
            if self.pages.currentIndex()==8:
                self.refresh_journal_work_filter()
                self.refresh_journal_page()
            QMessageBox.information(
                self,"Kindle highlights imported",
                f"Passages imported: {result['imported']}\n"
                f"Already imported: {result['duplicates']}\n"
                f"Skipped: {result['skipped']}\n"
                f"Custom books created: {result['created']}\n\n"
                "Your existing reading history was not changed."
            )
        except Exception as exc:
            fail(self,exc)

    def create_series_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Explore series", "headline"))
        layout.addWidget(label(
            "Browse known series; double-click one to see its books in your library.", "muted"
        ))
        self.series_table = QTableWidget(0, 4)
        self.series_table.setHorizontalHeaderLabels(
            ["Series", "Books", "Completed", "Active"]
        )
        self.series_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.series_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.series_table.verticalHeader().setVisible(False)
        self.series_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3):
            self.series_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.series_table.itemDoubleClicked.connect(self.open_series_from_table)
        layout.addWidget(self.series_table)
        self.pages.addWidget(page)

    def create_authors_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Browse authors", "headline"))
        layout.addWidget(label(
            "Every author credit in your library, including fanfiction writers. "
            "A combined coauthor credit is shown as entered.", "muted"
        ))
        controls = QHBoxLayout()
        self.author_search = QLineEdit()
        self.author_search.setPlaceholderText("Filter author names…")
        self.author_search.textChanged.connect(self.refresh_authors_page)
        controls.addWidget(self.author_search, stretch=1)
        controls.addWidget(button("View author's books", self.open_author_from_table))
        layout.addLayout(controls)
        self.authors_table = QTableWidget(0, 6)
        self.authors_table.setHorizontalHeaderLabels(
            ["Author credit", "Works", "Completed", "Active", "Favorites", "Avg. rating"]
        )
        self.authors_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.authors_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.authors_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.authors_table.verticalHeader().setVisible(False)
        self.authors_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in range(1, 6):
            self.authors_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.authors_table.itemDoubleClicked.connect(self.open_author_from_table)
        layout.addWidget(self.authors_table)
        self.author_count = label("", "muted")
        layout.addWidget(self.author_count)
        self.pages.addWidget(page)

    def refresh_authors_page(self, *_args):
        search = self.author_search.text().casefold().strip()
        authors = [
            item for item in self.library.author_summary()
            if search in item["author"].casefold()
        ]
        self.authors_table.setRowCount(len(authors))
        for row, entry in enumerate(authors):
            values = (
                entry["author"], str(entry["total"]), str(entry["completed"]),
                str(entry["active"]), str(entry["favorites"]),
                f"{entry['average_rating']:g} / 5"
                if entry["average_rating"] is not None else "—",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry["author"])
                self.authors_table.setItem(row, col, item)
            self.authors_table.setRowHeight(row, 38)
        self.author_count.setText(f"{len(authors)} author credits shown")

    def open_author_from_table(self, item=None):
        if item is None:
            row = self.authors_table.currentRow()
        else:
            row = item.row()
        if row < 0:
            return
        author = self.authors_table.item(row, 0)
        if not author:
            return
        self.clear_library_filters()
        target = self.author_filter.findData(author.data(Qt.ItemDataRole.UserRole))
        if target >= 0:
            self.author_filter.setCurrentIndex(target)
        self.navigate(0)
        self.refresh_library()

    def refresh_series_page(self):
        series = self.library.series_summary()
        self.series_table.setRowCount(len(series))
        for row, item in enumerate(series):
            values = (item["series"], str(item["total"]),
                      str(item["completed"]), str(item["active"]))
            for col, value in enumerate(values):
                self.series_table.setItem(row, col, QTableWidgetItem(value))
            self.series_table.setRowHeight(row, 39)

    def open_series_from_table(self, item):
        series_item = self.series_table.item(item.row(), 0)
        if not series_item:
            return
        series = series_item.text()
        self.clear_library_filters()
        self.refresh_catalog_filters()
        found = self.series_filter.findData(series)
        if found >= 0:
            self.series_filter.setCurrentIndex(found)
        self.navigate(0)
        self.refresh_library()

    def load_reading_goals(self, *_args):
        goal = self.library.get_goal(self.goal_year.value())
        for key, control in self.goal_inputs.items():
            control.setValue(goal[key + "_target"])
        self.refresh_goal_dashboard()

    def save_reading_goals(self):
        self.library.save_goal(
            self.goal_year.value(),
            books=self.goal_inputs["books"].value(),
            chapters=self.goal_inputs["chapters"].value(),
            pages=self.goal_inputs["pages"].value()
        )
        self.refresh_goal_dashboard()
        self.statusBar().showMessage("Reading goals saved locally.", 6000)

    def refresh_goal_dashboard(self):
        year = self.goal_year.value()
        goals = self.library.get_goal(year)
        actual = self.library.goal_progress(year)
        for key, bar in self.goal_progress_bars.items():
            value = actual[key]
            target = goals[key + "_target"]
            bar.setRange(0, max(value, target, 1))
            bar.setValue(value)
            bar.setFormat(
                f"{value:,} / {target:,}" if target else f"{value:,} recorded (no goal)"
            )
        current = self.library.list_works(sort="updated")
        reading = [w for w in current if w["status"] in ("reading", "caught_up")]
        self.current_reads.clear()
        for book in reading[:30]:
            progress = book["progress"]
            total = book["total_units"]
            ratio = f"{progress:,}/{total:,}" if total else str(progress)
            text = f"{book['title']} — {name(book['status'])} · {ratio} {book['progress_unit']}"
            tile = QListWidgetItem(text)
            tile.setData(Qt.ItemDataRole.UserRole, book["id"])
            self.current_reads.addItem(tile)
        if not reading:
            self.current_reads.addItem("No current reads. Mark a book as Reading to see it here.")
        self.serial_backlog.clear()
        for work in self.library.list_works(quality="serial_backlog", sort="updated")[:30]:
            remaining = work["total_units"] - work["progress"]
            tile = QListWidgetItem(
                f"{work['title']}  ·  {remaining} unread chapters "
                f"({work['progress']}/{work['total_units']})"
            )
            tile.setData(Qt.ItemDataRole.UserRole, work["id"])
            self.serial_backlog.addItem(tile)
        if not self.serial_backlog.count():
            self.serial_backlog.addItem("No known chapter backlogs.")
        self.recent_list.clear()
        for event in self.library.recent_activity(15):
            difference = event["new_progress"] - event["old_progress"]
            item = QListWidgetItem(
                f"{local_timestamp(event['recorded_at'])[:10]}  ·  {event['title']}  "
                f"({difference:+d} {event['progress_unit']})"
            )
            item.setData(Qt.ItemDataRole.UserRole, event["work_id"])
            self.recent_list.addItem(item)
        if not self.recent_list.count():
            self.recent_list.addItem("Reading milestones will appear here.")

    def open_current_read(self, item):
        work_id = item.data(Qt.ItemDataRole.UserRole)
        if not work_id:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=work_id)

    def refresh_all(self, select_id=None):
        self.refresh_shelves()
        self.refresh_catalog_filters()
        self.refresh_saved_views()
        self.refresh_library(select_id=select_id)
        self.refresh_history()
        self.refresh_stats()
        if self.pages.currentIndex() == 7:
            self.refresh_calendar_page()
        if self.pages.currentIndex() == 8:
            self.refresh_journal_page()
        if self.pages.currentIndex() == 9:
            self.refresh_updates_page()
        if self.pages.currentIndex() == 10:
            self.refresh_queue_page()
        if self.pages.currentIndex() == 11:
            self.refresh_habits_page()
        if self.pages.currentIndex() == 12:
            self.audio_page.refresh()

    def refresh_library(self, *_args, select_id=None):
        active_view = self.saved_view_combo.currentData()
        if active_view:
            selected = next(
                (v for v in self.library.list_views() if v["id"] == active_view), None
            )
            if not selected or selected["filters"] != self.current_view_filters():
                self.saved_view_combo.blockSignals(True)
                self.saved_view_combo.setCurrentIndex(0)
                self.saved_view_combo.blockSignals(False)
        books = self.library.list_works(
            self.search.text(), self.filter_status.currentData(),
            self.filter_kind.currentData(), self.only_fav.isChecked(),
            self.sort.currentData(), self.shelf_filter.currentData(),
            self.series_filter.currentData(), self.tag_filter.currentData(),
            self.author_filter.currentData(), self.quality_filter.currentData())
        current = select_id or self.selected_id
        self.table.blockSignals(True)
        self.table.setRowCount(len(books))
        chosen = -1
        for row, book in enumerate(books):
            if book["id"] == current:
                chosen = row
            total = book["total_units"] or (100 if book["progress_unit"] == "percent" else 0)
            progress = ("Read · unlogged"
                        if book["status"] == "completed" and book["progress"] == 0 else
                        (f"{book['progress']} / {total}" if total else str(book["progress"])))
            if book["status"] == "caught_up":
                progress += " ✓"
            values = (("★ " if book["favorite"] else "") + book["title"], book["author"],
                      name(book["kind"]), name(book["status"]), progress,
                      "—" if book["rating"] is None else f"{book['rating']:g} / 5")
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, book["id"])
                    url = cover_url_for(book)
                    item.setData(Qt.ItemDataRole.UserRole + 1, url)
                    if url:
                        cover = self.covers.get(url)
                        if cover is not None:
                            item.setIcon(QIcon(cover))
                    item.setFont(QFont("Noto Sans", 10, QFont.Weight.DemiBold))
                if col > 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 62)
        self.table.blockSignals(False)
        self.gallery.blockSignals(True)
        self.gallery.clear()
        self.gallery_books = books
        for book in books:
            tile = QListWidgetItem(book["title"] + "\n" + book["author"])
            tile.setData(Qt.ItemDataRole.UserRole, book["id"])
            tile.setToolTip(
                f"{book['title']}\n{book['author']}\n"
                f"Status: {name(book['status'])}\n"
                f"Progress: {book['progress']} {book['progress_unit']}\n"
                f"Rating: {book['rating'] if book['rating'] is not None else 'unrated'}"
            )
            tile.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            url = cover_url_for(book)
            cover = self.covers.get(url) if url else None
            tile.setIcon(QIcon(cover if cover is not None else placeholder_cover(book['title'])))
            self.gallery.addItem(tile)
        self.gallery.blockSignals(False)
        if chosen >= 0:
            self.table.selectRow(chosen)
            self.gallery.setCurrentRow(chosen)
        elif books:
            self.table.selectRow(0)
            self.gallery.setCurrentRow(0)
        else:
            self.selected_id = None
            self.clear_layout(self.details)
            self.details.addWidget(label("No books found. Add your first work!", "muted"))
        self.library_count.setText(f"{len(books)} works shown")

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                MainWindow.clear_layout(item.layout())

    def show_selected(self):
        if self.library_views.currentIndex() == 1:
            chosen = self.gallery.selectedItems()
            if not chosen:
                return
            work_id = chosen[0].data(Qt.ItemDataRole.UserRole)
        else:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return
            work_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self.selected_id = work_id
        book = self.library.get_work(work_id)
        if not book:
            return
        self.clear_layout(self.details)
        cover_url = cover_url_for(book)
        self.current_cover_url = cover_url
        self.current_cover_label = QLabel()
        self.current_cover_label.setFixedSize(105, 150)
        self.current_cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_cover_label.setStyleSheet(
            "background: #2b3a4d; border-radius: 6px; color: #9ba9bf;"
        )
        pixmap = self.covers.get(cover_url) if cover_url else None
        if pixmap is not None:
            self.current_cover_label.setPixmap(pixmap.scaled(
                105, 150, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self.current_cover_label.setText("No cover")
        self.details.addWidget(self.current_cover_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.details.addWidget(label(book["title"], "subheading"))
        self.details.addWidget(label(f"{book['author'] or 'Unknown author'} · {name(book['kind'])}", "muted"))
        linked = len(self.library.list_sources(work_id))
        editions = len(self.library.list_editions(work_id))
        if linked or editions:
            self.details.addWidget(label(
                f"{linked} source links  ·  {editions} editions", "muted"
            ))
        self.details.addWidget(button(
            "Manage sources && editions", self.manage_work_catalog
        ))
        self.details.addWidget(button(
            f"Local files ({len(self.library.list_local_files(work_id))})",
            self.manage_local_files
        ))
        linked_epubs=[
            file for file in self.library.list_local_files(work_id)
            if file["format"]=="epub"
        ]
        if linked_epubs:
            self.details.addWidget(button(
                "Read EPUB in BookTracker", self.read_selected_epub, primary=True
            ))
        audio_files=self.library.list_audio_files(work_id=work_id)
        if audio_files:
            hours=sum(f["duration_ms"] for f in audio_files
                      if not f["is_complete"] and f["available"])/3600000
            self.details.addWidget(label(
                f"Audiobooks: {len(audio_files)} M4B link(s)"
                + (f" · {hours:.1f} h of part audio" if hours else "")
                + " (duration ≠ time listened)","muted"
            ))
        self.details.addWidget(button(
            "♫ Audiobooks & M4B imports",lambda:self.navigate(12)
        ))
        self.details.addWidget(button(
            "Start reading / listening timer", self.open_reading_timer
        ))
        if book["kind"] in ("fanfiction","web_novel") and linked:
            follow=self.library.get_follow(work_id)
            self.details.addWidget(button(
                "Unfollow serialized story" if follow else "★ Follow serialized story",
                self.toggle_library_story_follow
            ))
            self.details.addWidget(button(
                "Open Update center",lambda: self.navigate(9)
            ))
        queued=self.library.get_queue_entry(work_id)
        if queued:
            self.details.addWidget(label(
                f"Reading queue: {name(queued['priority'])}"
                + (f" · Target {queued['target_date']}" if queued["target_date"] else ""),
                "muted"
            ))
            self.details.addWidget(button(
                "View reading queue", lambda: self.navigate(10)
            ))
        else:
            self.details.addWidget(button(
                "+ Add to reading queue", self.queue_current_book
            ))
        self.details.addSpacing(8)
        for text in (
            f"Reading: {name(book['status'])}",
            f"Publication: {name(book['publication_status'])}",
            f"Source: {book['source_site'] or 'Custom'}",
            f"Words: {book['word_count']:,}" if book["word_count"] else "",
            f"Fandom: {book['fandom']}" if book["fandom"] else "",
            f"Series: {book['series']}" if book["series"] else "",
        ):
            if text:
                self.details.addWidget(label(text))
        if book["tags"]:
            tags = book["tags"]
            summary = ", ".join(tags[:4])
            if len(tags) > 4:
                summary += f"  (+{len(tags) - 4} more)"
            self.details.addWidget(label("Tags: " + summary, "muted"))
            if len(tags) > 4:
                expanded = label(", ".join(tags), "muted")
                expanded.setVisible(False)
                self.details.addWidget(expanded)
                toggle = button(
                    "Show all tags", lambda: self.toggle_tag_details(expanded, toggle)
                )
                self.details.addWidget(toggle)
        runs = self.library.runs(work_id)
        progress = runs[0]["current_progress"] if runs else 0
        progress_text = ("Completed · progress not recorded"
                         if book["status"] == "completed" and progress == 0 else
                         (f"Progress: {progress} {book['progress_unit']}" +
                          (f" / {book['total_units']}" if book["total_units"] else "")))
        self.details.addWidget(label(progress_text, "subheading"))
        available = book["total_units"] or (100 if book["progress_unit"] == "percent" else 0)
        if available and progress >= 0:
            progress_bar = QProgressBar()
            progress_bar.setRange(0, available)
            progress_bar.setValue(min(progress, available))
            percentage = round(100 * progress / available)
            progress_bar.setFormat(f"{percentage}% of known length")
            self.details.addWidget(progress_bar)
        if book["kind"] in ("fanfiction", "web_novel") and book["progress_unit"] == "chapters":
            if book["total_units"]:
                unread = max(0, book["total_units"] - progress)
                self.details.addWidget(label(
                    f"Published: {book['total_units']:,} chapters  ·  "
                    f"{unread:,} remaining", "muted"
                ))
            self.details.addWidget(button(
                "Update published chapters", self.edit_published_chapters
            ))
            row = QHBoxLayout()
            row.addWidget(button("+1 chapter", lambda: self.advance_chapters(1)))
            row.addWidget(button("+5 chapters", lambda: self.advance_chapters(5)))
            self.details.addLayout(row)
            if book["total_units"]:
                self.details.addWidget(button(
                    "Catch up to published chapters", self.catch_up_to_published
                ))
        if book["notes"]:
            self.details.addWidget(label("Notes: " + book["notes"]))
        if book["description"]:
            summary = book["description"][:300]
            self.details.addWidget(label(summary + ("…" if len(book["description"]) > 300 else ""), "muted"))
        self.details.addSpacing(10)
        self.details.addWidget(button("Log progress", self.log_progress, primary=True))
        self.details.addWidget(button(
            "Log reading / listening time", self.log_reading_session
        ))
        self.details.addWidget(button(
            "Add bookmark / quote / note", self.add_annotation
        ))
        saved = len(self.library.list_annotations(work_id=work_id))
        if saved:
            self.details.addWidget(label(f"Saved bookmarks & notes: {saved}", "muted"))
        self.details.addWidget(button(
            "View this book's notes", self.show_work_annotations
        ))
        self.details.addWidget(button("View this book's history", self.show_work_history))
        self.details.addWidget(button("Edit details", self.edit_work))
        if book["kind"] == "book":
            self.details.addWidget(button("Fill missing metadata / cover", self.refresh_selected_metadata))
        self.details.addWidget(button("Mark caught up", lambda: self.change_status("caught_up")))
        self.details.addWidget(button("Mark completed", lambda: self.change_status("completed")))
        self.details.addWidget(button("Start a reread", self.reread))
        if book["source_url"].startswith("https://"):
            self.details.addWidget(button("Open source ↗", lambda: webbrowser.open(book["source_url"])))
        self.details.addWidget(button("Delete work", self.delete_work, danger=True))
        self.details.addSpacing(10)
        shelves = [s["name"] for s in self.library.list_shelves()
                   if s["id"] in self.library.shelves_for_work(work_id)]
        if shelves:
            self.details.addWidget(label("Shelves: " + ", ".join(shelves), "muted"))
        self.details.addWidget(label(f"Reading runs: {len(runs)}", "muted"))
        for run in runs[:5]:
            self.details.addWidget(label(
                f"• {run['started_at'][:10]} → {run['finished_at'][:10] or 'in progress'}", "muted"))
        self.details.addStretch(1)

    @staticmethod
    def toggle_tag_details(tag_label, control):
        expanded = not tag_label.isVisible()
        tag_label.setVisible(expanded)
        control.setText("Hide full tag list" if expanded else "Show all tags")

    def cover_available(self, url):
        pixmap = self.covers.get(url)
        if pixmap is None:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == url:
                item.setIcon(QIcon(pixmap))
        for i, work in enumerate(self.gallery_books):
            if cover_url_for(work) == url and i < self.gallery.count():
                self.gallery.item(i).setIcon(QIcon(pixmap))
        if self.current_cover_url == url and self.current_cover_label is not None:
            self.current_cover_label.setText("")
            self.current_cover_label.setPixmap(pixmap.scaled(
                105, 150, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def refresh_selected_metadata(self):
        work = self.selected_work()
        if not work:
            return
        if self.metadata_worker and self.metadata_worker.isRunning():
            self.statusBar().showMessage("A metadata lookup is already running.")
            return
        self.statusBar().showMessage(f"Checking public metadata for {work['title']}…")
        worker = LookupThread(missing_book_metadata, work)
        self.metadata_worker = worker
        work_id = work["id"]
        worker.found.connect(
            lambda response, book_id=work_id: self.apply_refreshed_metadata(book_id, response)
        )
        worker.failed.connect(
            lambda message: QMessageBox.warning(self, "Metadata lookup", message)
        )
        worker.finished.connect(lambda: self.statusBar().showMessage("Metadata lookup finished."))
        worker.start()

    def apply_refreshed_metadata(self, work_id, response):
        updates = response.get("updates") or {}
        if updates and self.library.get_work(work_id):
            self.library.update_work(work_id, updates)
            self.refresh_all(work_id)
        if response.get("warnings") and not updates:
            message = "Catalogs did not provide additional metadata. " + " | ".join(response["warnings"])
        elif updates:
            message = f"Updated {len(updates)} metadata fields without changing your reading history."
        else:
            message = "This book already has all metadata available from the catalog."
        self.statusBar().showMessage(message, 15000)

    def toggle_library_view(self, *_args):
        self.library_views.setCurrentIndex(self.view_selector.currentIndex())
        self.show_selected()

    def selected_ids(self):
        if self.library_views.currentIndex() == 1:
            return [item.data(Qt.ItemDataRole.UserRole) for item in self.gallery.selectedItems()]
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def select_all_visible(self):
        if self.library_views.currentIndex() == 1:
            self.gallery.selectAll()
        else:
            self.table.selectAll()

    def refresh_catalog_filters(self):
        for combo, entries, title, key in (
            (self.series_filter, self.library.series_summary(), "All series", "series"),
            (self.author_filter, self.library.author_summary(), "All authors", "author"),
            (self.tag_filter, self.library.tag_summary(), "All tags", "tag"),
        ):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, None)
            for entry in entries:
                combo.addItem(f"{entry[key]} ({entry['total']})", entry[key])
            found = combo.findData(current)
            combo.setCurrentIndex(max(0, found))
            combo.blockSignals(False)

    def clear_library_filters(self):
        self.search.clear()
        for combo in (self.filter_status, self.filter_kind, self.shelf_filter,
                      self.series_filter, self.author_filter, self.tag_filter,
                      self.quality_filter):
            combo.setCurrentIndex(0)
        self.only_fav.setChecked(False)
        self.refresh_library()

    def current_view_filters(self):
        return {
            "search": self.search.text(),
            "status": self.filter_status.currentData(),
            "kind": self.filter_kind.currentData(),
            "favorites": self.only_fav.isChecked(),
            "sort": self.sort.currentData(),
            "shelf_id": self.shelf_filter.currentData(),
            "series": self.series_filter.currentData(),
            "tag": self.tag_filter.currentData(),
            "author": self.author_filter.currentData(),
            "quality": self.quality_filter.currentData(),
        }

    def refresh_saved_views(self):
        existing = self.saved_view_combo.currentData()
        self.saved_view_combo.blockSignals(True)
        self.saved_view_combo.clear()
        self.saved_view_combo.addItem("Choose a saved filter…", None)
        for saved in self.library.list_views():
            self.saved_view_combo.addItem(saved["name"], saved["id"])
        at = self.saved_view_combo.findData(existing)
        self.saved_view_combo.setCurrentIndex(max(0, at))
        self.saved_view_combo.blockSignals(False)

    def save_current_view(self):
        title, ok = QInputDialog.getText(
            self, "Save library view", "Name these filters:"
        )
        if not ok:
            return
        try:
            view_id = self.library.save_view(title, self.current_view_filters())
            self.refresh_saved_views()
            selected = self.saved_view_combo.findData(view_id)
            self.saved_view_combo.blockSignals(True)
            self.saved_view_combo.setCurrentIndex(selected)
            self.saved_view_combo.blockSignals(False)
            self.statusBar().showMessage(f"Saved library view: {title}", 6000)
        except Exception as exc:
            fail(self, exc)

    def apply_saved_view(self, _index=None):
        view_id = self.saved_view_combo.currentData()
        if not view_id:
            return
        view = next((v for v in self.library.list_views() if v["id"] == view_id), None)
        if not view:
            return
        values = view["filters"]
        # Block dependent signal-driven refreshes while applying the snapshot.
        inputs = (self.search, self.filter_status, self.filter_kind, self.sort,
                  self.shelf_filter, self.series_filter, self.author_filter,
                  self.tag_filter, self.quality_filter, self.only_fav)
        previous = [widget.blockSignals(True) for widget in inputs]
        try:
            self.search.setText(values.get("search") or "")
            for combo, key in (
                (self.filter_status, "status"), (self.filter_kind, "kind"),
                (self.sort, "sort"), (self.shelf_filter, "shelf_id"),
                (self.series_filter, "series"), (self.author_filter, "author"),
                (self.tag_filter, "tag"), (self.quality_filter, "quality"),
            ):
                found = combo.findData(values.get(key))
                combo.setCurrentIndex(max(0, found))
            self.only_fav.setChecked(bool(values.get("favorites")))
        finally:
            for widget, old in zip(inputs, previous):
                widget.blockSignals(old)
        self.refresh_library()

    def delete_current_view(self):
        view_id = self.saved_view_combo.currentData()
        if not view_id:
            QMessageBox.information(self, "Choose view", "Select a saved view to remove.")
            return
        name = self.saved_view_combo.currentText()
        if QMessageBox.question(
            self, "Delete saved view?", f"Delete the saved filter {name!r}? No books are deleted."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.library.delete_view(view_id)
        self.refresh_saved_views()

    def refresh_shelves(self):
        current = self.shelf_filter.currentData()
        self.shelf_filter.blockSignals(True)
        self.shelf_filter.clear()
        self.shelf_filter.addItem("All shelves", None)
        for shelf in self.library.list_shelves():
            self.shelf_filter.addItem(f"{shelf['name']} ({shelf['total']})", shelf["id"])
        at = self.shelf_filter.findData(current)
        self.shelf_filter.setCurrentIndex(max(0, at))
        self.shelf_filter.blockSignals(False)

    def create_shelf(self):
        name, ok = QInputDialog.getText(self, "Create shelf", "Shelf name:")
        if not ok:
            return
        try:
            self.library.create_shelf(name)
            self.refresh_all()
        except Exception as exc:
            fail(self, exc)

    def manage_shelves(self):
        shelves = self.library.list_shelves()
        if not shelves:
            QMessageBox.information(self, "No shelves", "Create a shelf to organize your books.")
            return
        names = [shelf["name"] for shelf in shelves]
        choice, ok = QInputDialog.getItem(
            self, "Manage shelves", "Choose a shelf:", names, 0, False
        )
        if not ok:
            return
        selected = next(row for row in shelves if row["name"] == choice)
        action, ok = QInputDialog.getItem(
            self, f"Manage {choice}", "Action:",
            ["Rename shelf", "Delete shelf (keep books)", "Remove selected books from shelf"],
            0, False,
        )
        if not ok:
            return
        try:
            if action == "Rename shelf":
                new_name, accepted = QInputDialog.getText(
                    self, "Rename shelf", "New name:", text=choice
                )
                if accepted:
                    self.library.rename_shelf(selected["id"], new_name)
            elif action == "Delete shelf (keep books)":
                if QMessageBox.question(
                    self, "Delete shelf?",
                    f"Delete shelf {choice!r}? Books and reading history will remain."
                ) == QMessageBox.StandardButton.Yes:
                    self.library.delete_shelf(selected["id"])
            else:
                ids = self.selected_ids()
                if not ids:
                    QMessageBox.information(
                        self, "Choose books", "Select the books to remove first."
                    )
                    return
                self.library.set_shelf_membership(ids, selected["id"], include=False)
            self.refresh_all()
        except Exception as exc:
            fail(self, exc)

    def add_selected_to_shelf(self):
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Choose books", "Select one or more books first.")
            return
        shelves = self.library.list_shelves()
        if not shelves:
            self.create_shelf()
            shelves = self.library.list_shelves()
        if not shelves:
            return
        labels = [item["name"] for item in shelves]
        choice, ok = QInputDialog.getItem(
            self, "Add to shelf", f"Put {len(ids)} selected books on:", labels, 0, False
        )
        if ok:
            try:
                self.library.set_shelf_membership(
                    ids, next(item["id"] for item in shelves if item["name"] == choice)
                )
                self.refresh_all()
            except Exception as exc:
                fail(self, exc)

    def bulk_edit_selected(self):
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Choose books", "Select one or more books first.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Bulk edit {len(ids)} books")
        form = QFormLayout(dialog)
        status = QComboBox()
        status.addItem("Leave reading status unchanged", None)
        for value in STATUSES:
            status.addItem(name(value), value)
        favorite = QComboBox()
        favorite.addItem("Leave favorites unchanged", None)
        favorite.addItem("Set favorites", True)
        favorite.addItem("Clear favorites", False)
        publication = QComboBox()
        publication.addItem("Leave publication status unchanged", None)
        for value in PUBLICATION:
            publication.addItem(name(value), value)
        rating = QComboBox()
        rating.addItem("Leave rating unchanged", None)
        for n in range(11):
            rating.addItem(f"{n / 2:g} stars", n / 2)
        series_enabled = QCheckBox("Change series for all selected")
        series = QLineEdit()
        series.setPlaceholderText("Leave blank to clear series when checked")
        series.setEnabled(False)
        series_enabled.toggled.connect(series.setEnabled)
        tags = QLineEdit()
        tags.setPlaceholderText("Tags to add, separated by commas")
        remove_tags = QLineEdit()
        remove_tags.setPlaceholderText("Tags to remove, separated by commas")
        form.addRow(f"Editing {len(ids)} selected books", label("Changes affect all selected entries."))
        form.addRow("Reading status", status)
        form.addRow("Publication status", publication)
        form.addRow("Favorite", favorite)
        form.addRow("Rating", rating)
        form.addRow(series_enabled)
        form.addRow("Series", series)
        form.addRow("Add tags", tags)
        form.addRow("Remove tags", remove_tags)
        form.addRow(label("Historical reading dates and progress will not be invented.", "muted"))
        controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        form.addRow(controls)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.library.batch_edit(
                    ids, status=status.currentData(), favorite=favorite.currentData(),
                    add_tags=[v.strip() for v in tags.text().split(",") if v.strip()],
                    remove_tags=[v.strip() for v in remove_tags.text().split(",") if v.strip()],
                    rating=rating.currentData(),
                    publication_status=publication.currentData(),
                    series=series.text() if series_enabled.isChecked() else None,
                )
                self.refresh_all()
            except Exception as exc:
                fail(self, exc)

    def manual_backup(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save database snapshot",
            str(self.library.path.parent / "backups" / "BookTracker-manual.sqlite3"),
            "SQLite database (*.sqlite3)"
        )
        if not path:
            return
        try:
            self.library.backup_to(path)
            QMessageBox.information(self, "Backup complete", f"Verified backup saved to:\n{path}")
        except Exception as exc:
            fail(self, exc)

    def selected_work(self):
        return self.library.get_work(self.selected_id) if self.selected_id else None

    def add_work(self):
        dialog = WorkEditor(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dialog.bulk_selection:
                count = len(dialog.bulk_selection)
                status = dialog.bulk_status
                # All selected works are inserted atomically; previously saved
                # titles and their progress history remain untouched.
                result = self.library.bulk_add_works(
                    dialog.bulk_selection, status=status,
                    update_existing_status=dialog.bulk_update_existing,
                )
                self.refresh_all()
                message = (
                    f"Selected: {count}\nAdded: {result['added']}\n"
                    f"Updated existing statuses: {result['updated']}\n"
                    f"Skipped existing/duplicate editions: {result['skipped']}"
                )
                if status == "completed":
                    message += "\n\nHistorical completion dates were left unknown."
                QMessageBox.information(self, "Bulk books added", message)
                return
            values = dialog.values()
            if self.library.find_match(values):
                QMessageBox.warning(self, "Already in library",
                                    "A work with this source ID, URL, or ISBN already exists.")
                return
            work_id = self.library.add_work(values)
            self.refresh_all(work_id)
        except Exception as exc:
            fail(self, exc)

    def edit_work(self):
        work = self.selected_work()
        if not work:
            return
        dialog = WorkEditor(self, work)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            data = dialog.values()
            status = data.pop("status")
            old_status = work["status"]
            self.library.update_work(work["id"], data)
            if status != old_status:
                self.library.set_status(work["id"], status)
            self.refresh_all(work["id"])
        except Exception as exc:
            fail(self, exc)

    def edit_published_chapters(self):
        work = self.selected_work()
        if not work or work["kind"] not in ("fanfiction", "web_novel"):
            return
        count, ok = QInputDialog.getInt(
            self, "Published chapters",
            f"Total currently published chapters for {work['title']}:",
            value=work["total_units"], min=0, max=10_000_000,
        )
        if not ok:
            return
        try:
            self.library.set_published_units(work["id"], count)
            self.refresh_all(work["id"])
        except Exception as exc:
            fail(self, exc)

    def advance_chapters(self, count):
        work = self.selected_work()
        if not work or work["progress_unit"] != "chapters":
            return
        run = self.library.active_run(work["id"])
        current = run["current_progress"] if run else 0
        next_value = current + count
        if work["total_units"] > 0 and next_value > work["total_units"]:
            next_value = work["total_units"]
        if next_value <= current:
            self.statusBar().showMessage("Already at the known published chapter count.", 5000)
            return
        try:
            self.library.set_progress(work["id"], next_value)
            self.refresh_all(work["id"])
        except Exception as exc:
            fail(self, exc)

    def catch_up_to_published(self):
        work = self.selected_work()
        if not work:
            return
        if QMessageBox.question(
            self, "Catch up to published chapters?",
            f"Log progress through chapter {work['total_units']:,} and mark "
            f"{work['title']} caught up?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            advanced = self.library.catch_up_to_published(work["id"])
            self.refresh_all(work["id"])
            self.statusBar().showMessage(
                f"Marked caught up. Advanced {advanced} chapters.", 7000
            )
        except Exception as exc:
            fail(self, exc)

    def log_progress(self):
        work = self.selected_work()
        if not work:
            return
        runs = self.library.runs(work["id"])
        work["progress"] = runs[0]["current_progress"] if runs else 0
        dialog = ProgressDialog(self, work)
        latest = self.library.active_run(work["id"])
        if latest:
            dialog.progress.setValue(latest["current_progress"])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dialog.use_historical_date.isChecked():
                self.library.record_progress_on(
                    work["id"], dialog.progress.value(),
                    date=dialog.when.date().toString("yyyy-MM-dd"),
                    note=dialog.note.text().strip()
                )
            else:
                self.library.set_progress(
                    work["id"], dialog.progress.value(),
                    note=dialog.note.text().strip()
                )
            self.refresh_all(work["id"])
        except Exception as exc:
            fail(self, exc)

    def change_status(self, status):
        work = self.selected_work()
        if not work:
            return
        try:
            self.library.set_status(work["id"], status)
            self.refresh_all(work["id"])
        except Exception as exc:
            fail(self, exc)

    def reread(self):
        work = self.selected_work()
        if not work:
            return
        answer = QMessageBox.question(self, "Start a reread",
                                      f"Start a new reading run for {work['title']}?\nPrevious history is preserved.")
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.library.start_reading(work["id"], reread=True)
                self.refresh_all(work["id"])
            except Exception as exc:
                fail(self, exc)

    def delete_work(self):
        work = self.selected_work()
        if not work:
            return
        answer = QMessageBox.question(self, "Delete work",
                                      f"Delete {work['title']} and all of its reading history?\nThis cannot be undone.")
        if answer == QMessageBox.StandardButton.Yes:
            self.library.delete_work(work["id"])
            self.selected_id = None
            self.refresh_all()

    def refresh_history(self, *_args):
        current_year = self.history_year.currentData()
        self.history_year.blockSignals(True)
        self.history_year.clear()
        self.history_year.addItem("All years", None)
        for year in self.library.history_years():
            self.history_year.addItem(str(year), year)
        at = self.history_year.findData(current_year)
        self.history_year.setCurrentIndex(max(0, at))
        self.history_year.blockSignals(False)

        events = self.library.history_filtered(
            search=self.history_search.text(),
            year=self.history_year.currentData(),
            limit=5000
        )
        selected = self.selected_history_event_id()
        self.history_table.blockSignals(True)
        self.history_table.setRowCount(len(events))
        selected_row = -1
        for row, event in enumerate(events):
            delta = event["new_progress"] - event["old_progress"]
            values = (
                local_timestamp(event["recorded_at"]),
                event["title"],
                (f"{event['old_progress']} → {event['new_progress']} "
                 f"{event['progress_unit']} ({delta:+d})"),
                event["note"],
            )
            if event["id"] == selected:
                selected_row = row
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, event["id"])
                    cell.setData(Qt.ItemDataRole.UserRole + 1, event["work_id"])
                self.history_table.setItem(row, col, cell)
            self.history_table.setRowHeight(row, 38)
        self.history_table.blockSignals(False)
        if selected_row >= 0:
            self.history_table.selectRow(selected_row)
        self.history_count.setText(
            f"{len(events)} milestones shown"
            + (" (first 5,000)" if len(events) == 5000 else "")
        )

    def selected_history_event_id(self):
        if not hasattr(self, "history_table"):
            return None
        selection = self.history_table.selectionModel()
        rows = selection.selectedRows() if selection else []
        if not rows:
            return None
        item = self.history_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def edit_history_event(self):
        event_id = self.selected_history_event_id()
        if not event_id:
            QMessageBox.information(self, "Choose milestone", "Select a reading event to edit.")
            return
        event = self.library.get_progress_event(event_id)
        if not event:
            self.refresh_history()
            return
        dialog = MilestoneEditor(self, event)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.update_progress_event(
                event_id, date=dialog.date.date().toString("yyyy-MM-dd"),
                progress=dialog.progress.value(), note=dialog.note.text()
            )
            self.refresh_all(event["work_id"])
        except Exception as exc:
            fail(self, exc)

    def show_work_history(self):
        work = self.selected_work()
        if not work:
            return
        self.history_year.setCurrentIndex(0)
        self.history_search.setText(work["title"])
        self.navigate(1)

    def open_history_book(self):
        event_id = self.selected_history_event_id()
        if not event_id:
            QMessageBox.information(self, "Select milestone", "Choose an event first.")
            return
        event = self.library.get_progress_event(event_id)
        if not event:
            return
        self.clear_library_filters()
        self.navigate(0)
        self.refresh_library(select_id=event["work_id"])

    def export_history_csv(self):
        events = self.library.history_filtered(
            search=self.history_search.text(),
            year=self.history_year.currentData(), limit=10000
        )
        if not events:
            QMessageBox.information(self, "No history", "No milestones match the current filters.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export selected history", "BookTracker-reading-history.csv",
            "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            # Prefix strings beginning with spreadsheet formula operators.
            # This keeps CSV safe for opening in spreadsheet applications.
            def safe(value):
                text = str(value if value is not None else "")
                return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

            with open(path, "w", encoding="utf-8-sig", newline="") as output:
                writer = csv.writer(output)
                writer.writerow([
                    "Date", "Work", "Author", "Previous progress",
                    "New progress", "Unit", "Note",
                ])
                for event in events:
                    writer.writerow([
                        safe(event["recorded_at"]), safe(event["title"]),
                        safe(event["author"]), event["old_progress"],
                        event["new_progress"], safe(event["progress_unit"]),
                        safe(event["note"]),
                    ])
            self.statusBar().showMessage(f"Exported {len(events)} reading milestones.", 8000)
        except OSError as exc:
            fail(self, exc)

    def delete_history_event(self):
        event_id = self.selected_history_event_id()
        if not event_id:
            QMessageBox.information(self, "Select an entry", "Choose a reading milestone first.")
            return
        if QMessageBox.question(
            self, "Remove milestone?",
            "Remove this mistaken reading milestone? The reading run's progress "
            "will be recalculated from its remaining events."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.delete_progress_event(event_id)
            self.refresh_all()
        except Exception as exc:
            fail(self, exc)

    def refresh_stats(self):
        stats = self.library.stats()
        for key, widget in self.stat_labels.items():
            widget.setText(f"{stats['by_status']['reading']:,}" if key == "reading"
                           else f"{stats[key]:,}")
        breakdown = "   ·   ".join(f"{name(kind)}: {count}" for kind, count in stats["by_kind"].items())
        totals = self.library.session_totals()
        hours = totals["reading_minutes"] / 60
        listening = totals["listening_minutes"] / 60
        self.stat_breakdown.setText(
            "Library breakdown\n" + breakdown
            + f"\nLogged time: {hours:.1f} h reading · {listening:.1f} h listening"
        )
        self.refresh_goal_dashboard()

    def export_library(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export BookTracker library", "BookTracker-full.btbackup",
            "Portable full backup (*.btbackup);;JSON backup (*.json);;"
            "XML backup (*.xml);;CSV catalog (*.csv)")
        if not path:
            return
        try:
            export_file(self.library, path)
            message = (
                "Export complete.\n\nPortable .btbackup files include your reading history, "
                "shelves, goals, the reading queue, file-link metadata and imported EPUB covers. "
                "JSON/XML include reading data and file-link metadata but not local images; "
                "CSV is a flat catalog. EPUB/M4B/PDF contents and absolute paths are not "
                "included in any portable export."
            )
            QMessageBox.information(self, "Exported", message)
        except Exception as exc:
            fail(self, exc)

    def import_library(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import library", "",
            "BookTracker files (*.btbackup *.json *.xml *.csv)")
        if not path:
            return
        answer = QMessageBox.question(
            self, "Import library", "Merge imported books with your existing library?\n\n"
            "Yes = merge and skip duplicate history IDs.\nNo = replace your entire library.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
            QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Cancel:
            return
        strategy = "merge" if answer == QMessageBox.StandardButton.Yes else "replace"
        if strategy == "replace":
            confirm = QMessageBox.warning(
                self, "Replace library?", "Your current library will be overwritten.\n"
                "Export a backup first. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if confirm != QMessageBox.StandardButton.Yes:
                return
        try:
            if strategy == "replace":
                backup_folder = self.library.path.parent / "backups"
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                self.library.backup_to(
                    backup_folder / f"BookTracker-before-import-{timestamp}.sqlite3"
                )
            count = import_file(self.library, path, strategy=strategy)
            self.refresh_all()
            QMessageBox.information(self, "Imported", f"Imported {count} new works.")
        except Exception as exc:
            fail(self, exc)

    @staticmethod
    def _scan_epub_files(paths, cover_dir):
        works = []
        errors = []
        for filename in paths:
            try:
                data=read_epub_metadata(filename, cover_dir=cover_dir)
                data["_local_file_path"]=str(Path(filename).expanduser().resolve())
                works.append(data)
            except (OSError, ValueError, KeyError) as exc:
                errors.append(f"{Path(filename).name}: {exc}")
        return {"works": works, "errors": errors}

    def import_epub_files(self):
        if self.epub_worker and self.epub_worker.isRunning():
            self.statusBar().showMessage("An EPUB import is already in progress.", 6000)
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select EPUB files to add", "", "EPUB ebooks (*.epub)"
        )
        if not paths:
            return
        if len(paths) > 250:
            QMessageBox.warning(self, "Import limit", "Select up to 250 EPUBs at once.")
            return
        reply = QMessageBox.question(
            self, "Import reading status",
            "Mark these books as completed?\n\n"
            "Yes = Completed (historical finish date unknown).\n"
            "No = Plan to read.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
            QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        status = "completed" if reply == QMessageBox.StandardButton.Yes else "plan_to_read"
        worker = LookupThread(
            self._scan_epub_files, paths, self.library.path.parent / "imported_covers"
        )
        self.epub_worker = worker
        worker.found.connect(lambda data: self.finish_epub_import(data, status))
        worker.failed.connect(
            lambda err: QMessageBox.warning(self, "EPUB import", err)
        )
        self.statusBar().showMessage(f"Reading metadata from {len(paths)} EPUB files…")
        worker.start()

    def finish_epub_import(self, data, status):
        works = data["works"]
        errors = data["errors"]
        if works:
            preview = "\n".join("• " + w["title"] for w in works[:12])
            if len(works) > 12:
                preview += f"\n… and {len(works) - 12} more"
            proceed = QMessageBox.question(
                self, "Import these books?",
                f"Ready to add {len(works)} books ({name(status)}):\n\n"
                f"{preview}\n\nExisting entries will be enriched when metadata is missing.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if proceed == QMessageBox.StandardButton.Yes:
                try:
                    report = self.library.bulk_add_works(
                        works, status=status, enrich_duplicates=True
                    )
                    linked=0
                    link_errors=[]
                    for work_id, item in zip(report["resolved_ids"], works):
                        filename=item.get("_local_file_path")
                        if not filename:
                            continue
                        try:
                            self.library.add_local_file(work_id,filename)
                            linked+=1
                        except (OSError,ValueError) as exc:
                            link_errors.append(f"{Path(filename).name}: {exc}")
                    self.refresh_all()
                    QMessageBox.information(
                        self, "EPUB import complete",
                        f"Added: {report['added']}\n"
                        f"Existing books enriched: {report['enriched']}\n"
                        f"Skipped duplicates: {report['skipped']}\n"
                        f"EPUB file links: {linked}"
                        + (f"\nUnreadable EPUBs: {len(errors)}" if errors else "")
                        + (f"\nFiles not linked: {len(link_errors)}" if link_errors else "")
                    )
                    if link_errors:
                        QMessageBox.warning(
                            self,"Some EPUB links failed","\n".join(link_errors[:12])
                        )
                except Exception as exc:
                    fail(self, exc)
        if errors:
            details = "\n".join(errors[:15])
            if len(errors) > 15:
                details += f"\n… and {len(errors)-15} more"
            QMessageBox.warning(self, "EPUBs that could not be read", details)
        self.statusBar().showMessage("EPUB import finished.", 6000)

    def import_goodreads_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Goodreads export", "", "CSV (*.csv)")
        if not path:
            return
        try:
            created, skipped = import_goodreads(self.library, path)
            self.refresh_all()
            QMessageBox.information(self, "Goodreads import",
                                    f"Added {created} books; skipped {skipped} duplicates.")
        except Exception as exc:
            fail(self, exc)

    def closeEvent(self, event):
        if self.audio_page.worker and self.audio_page.worker.isRunning():
            self.audio_page.stop_scan()
            if not self.audio_page.worker.wait(20000):
                event.ignore()
                self.statusBar().showMessage(
                    "Finishing the active audio metadata probe before closing.",8000
                )
                return
        if self.serial_worker and self.serial_worker.isRunning():
            self.serial_worker.requestInterruption()
            if not self.serial_worker.wait(12000):
                event.ignore()
                self.statusBar().showMessage(
                    "Finishing the current website request before closing.",7000
                )
                return
        for worker in (self.metadata_worker, self.epub_worker):
            if worker and worker.isRunning():
                worker.wait()
        if self._serial_tray:
            self._serial_tray.hide()
        if self.audio_page._player_dialog is not None:
            self.audio_page._player_dialog.close()
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("library_view", self.view_selector.currentData())
        self.library.close()
        super().closeEvent(event)


def run(db_path):
    app = QApplication(sys.argv)
    app.setApplicationName("BookTracker")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    library = Library(db_path)
    window = MainWindow(library)
    window.show()
    return app.exec()
