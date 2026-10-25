"""PySide6 desktop interface. All user data stays in the local SQLite database."""
from __future__ import annotations

import csv
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .backups import automatic_backup
from .book_search import _normalize, cached_book_search
from .covers import CoverCache, cover_url_for
from .database import KINDS, PUBLICATION, STATUSES, UNITS, Library
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

LABELS = {
    "book": "Book", "fanfiction": "Fanfiction", "web_novel": "Web novel", "other": "Other",
    "plan_to_read": "Plan to read", "reading": "Reading", "caught_up": "Caught up",
    "completed": "Completed", "paused": "Paused", "dropped": "Dropped",
    "unknown": "Unknown", "ongoing": "Ongoing", "hiatus": "Hiatus",
    "chapters": "Chapters", "pages": "Pages", "percent": "Percent",
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
QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTableWidget, QListWidget {
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
        parsed = QDate.fromString(event["recorded_at"][:10], "yyyy-MM-dd")
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
        sidebar.addSpacing(24)
        self.nav_buttons = []
        for title, index in (("▤   Library", 0), ("◷   Reading history", 1),
                             ("▥   Dashboard", 2), ("▦   Series", 3),
                             ("♧   Authors", 4)):
            nav = button(title, lambda _, i=index: self.navigate(i))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            sidebar.addWidget(nav)
            self.nav_buttons.append(nav)
        sidebar.addStretch()
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

    def make_menu(self):
        menu = self.menuBar().addMenu("&File")
        for title, callback, shortcut in (
            ("Add work", self.add_work, "Ctrl+N"),
            ("Import EPUB files", self.import_epub_files, "Ctrl+Shift+E"),
            ("Import Goodreads CSV", self.import_goodreads_csv, ""),
            ("Import BookTracker file", self.import_library, ""),
            ("Export BookTracker file", self.export_library, "Ctrl+Shift+S"),
            ("Back up database", self.manual_backup, "Ctrl+B"),
            ("Focus library search", self.focus_library_search, "Ctrl+F"),
            ("Reading history", lambda: self.navigate(1), "Ctrl+H"),
            ("Authors", lambda: self.navigate(4), "Ctrl+Shift+A"),
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
        for i, nav in enumerate(self.nav_buttons):
            nav.setChecked(i == index)
        if index == 1:
            self.refresh_history()
        elif index == 2:
            self.refresh_stats()
        elif index == 3:
            self.refresh_series_page()
        elif index == 4:
            self.refresh_authors_page()

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
        self.search.setPlaceholderText("Search titles, authors, fandoms, tags…")
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
                           ("Recently finished", "finished"), ("Series", "series")):
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
        commands.addWidget(button("New shelf", self.create_shelf))
        commands.addWidget(button("Manage shelves", self.manage_shelves))
        commands.addStretch()
        layout.addLayout(commands)
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
        details.setMaximumWidth(430)
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
        self.recent_list.clear()
        for event in self.library.recent_activity(15):
            difference = event["new_progress"] - event["old_progress"]
            item = QListWidgetItem(
                f"{event['recorded_at'][:10]}  ·  {event['title']}  "
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
        self.refresh_library(select_id=select_id)
        self.refresh_history()
        self.refresh_stats()

    def refresh_library(self, *_args, select_id=None):
        books = self.library.list_works(
            self.search.text(), self.filter_status.currentData(),
            self.filter_kind.currentData(), self.only_fav.isChecked(),
            self.sort.currentData(), self.shelf_filter.currentData(),
            self.series_filter.currentData(), self.tag_filter.currentData(),
            self.author_filter.currentData())
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
        if book["notes"]:
            self.details.addWidget(label("Notes: " + book["notes"]))
        if book["description"]:
            summary = book["description"][:300]
            self.details.addWidget(label(summary + ("…" if len(book["description"]) > 300 else ""), "muted"))
        self.details.addSpacing(10)
        self.details.addWidget(button("Log progress", self.log_progress, primary=True))
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
                      self.series_filter, self.author_filter, self.tag_filter):
            combo.setCurrentIndex(0)
        self.only_fav.setChecked(False)
        self.refresh_library()

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
        tags = QLineEdit()
        tags.setPlaceholderText("Optional tags separated by commas")
        form.addRow(f"Editing {len(ids)} selected books", label("Changes affect all selected entries."))
        form.addRow("Reading status", status)
        form.addRow("Favorite", favorite)
        form.addRow("Add tags", tags)
        form.addRow(label("Historical completion dates will not be fabricated.", "muted"))
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
                event["recorded_at"][:16].replace("T", " "),
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
        self.stat_breakdown.setText("Library breakdown\n" + breakdown)
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
                "shelves, goals and imported EPUB covers. JSON/XML include reading data "
                "but not local images; CSV is a flat catalog."
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
                works.append(read_epub_metadata(filename, cover_dir=cover_dir))
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
                    self.refresh_all()
                    QMessageBox.information(
                        self, "EPUB import complete",
                        f"Added: {report['added']}\n"
                        f"Existing books enriched: {report['enriched']}\n"
                        f"Skipped duplicates: {report['skipped']}"
                        + (f"\nUnreadable EPUBs: {len(errors)}" if errors else "")
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
        for worker in (self.metadata_worker, self.epub_worker):
            if worker and worker.isRunning():
                worker.wait()
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
