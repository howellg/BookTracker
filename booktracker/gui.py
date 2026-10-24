"""PySide6 desktop interface. All user data stays in the local SQLite database."""
from __future__ import annotations

import sys
import webbrowser

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
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

from .book_search import _normalize, cached_book_search
from .covers import CoverCache, cover_url_for
from .database import KINDS, PUBLICATION, STATUSES, UNITS, Library
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
QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QListWidget {
 background: #202c3c; border: 1px solid #34445a; border-radius: 7px;
 padding: 7px; selection-background-color: #305c8f;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
 border: 1px solid #7bb6ff;
}
QPushButton { background: #2b3d54; border: 1px solid #38506a; border-radius: 8px;
 padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #3b5575; }
QPushButton:disabled { color: #8290a4; background: #222b37; }
QPushButton#primary { background: #3274c2; border: 1px solid #3274c2; color: #ffffff; }
QPushButton#primary:hover { background: #468adb; }
QPushButton#danger { background: #613540; border: 1px solid #85505a; }
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
    if primary:
        result.setObjectName("primary")
    elif danger:
        result.setObjectName("danger")
    result.clicked.connect(callback)
    return result


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
        self.unit = self.combo(UNITS, self.work.get("progress_unit", "chapters"))
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
                          (self.fandom, "fandom"), (self.source_site, "source_site"),
                          (self.source_url, "source_url"), (self.isbn, "isbn"),
                          (self.published, "published_at"), (self.language, "language")):
            if info.get(key):
                attr.setText(str(info[key]))
        self.source_id = info.get("source_id", "")
        self.cover_url = info.get("cover_url", "")
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
        form.addRow(name(work["progress_unit"]) + " reached", self.progress)
        form.addRow("Note", self.note)
        layout.addLayout(form)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)


class MainWindow(QMainWindow):
    def __init__(self, library):
        super().__init__()
        self.library = library
        self.selected_id = None
        self.covers = CoverCache(self)
        self.covers.cover_ready.connect(self.cover_available)
        self.current_cover_label = None
        self.current_cover_url = ""
        self.metadata_worker = None
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
                             ("▥   Statistics", 2)):
            nav = button(title, lambda _, i=index: self.navigate(i))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            sidebar.addWidget(nav)
            self.nav_buttons.append(nav)
        sidebar.addStretch()
        sidebar.addWidget(button("Import library", self.import_library))
        sidebar.addWidget(button("Export library", self.export_library))
        sidebar.addWidget(label("Offline-first · SQLite", "muted"))
        outer.addWidget(side)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, stretch=1)
        self.create_library_page()
        self.create_history_page()
        self.create_stats_page()
        self.navigate(0)
        self.refresh_all()
        self.statusBar().showMessage(f"Local database: {self.library.path}")
        self.make_menu()

    def make_menu(self):
        menu = self.menuBar().addMenu("&File")
        for title, callback in (("Add work", self.add_work),
                                ("Import Goodreads CSV", self.import_goodreads_csv),
                                ("Import BookTracker file", self.import_library),
                                ("Export BookTracker file", self.export_library)):
            action = QAction(title, self)
            action.triggered.connect(callback)
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
                           ("Author", "author"), ("Recently added", "added"), ("Rating", "rating")):
            self.sort.addItem(title, key)
        self.sort.currentIndexChanged.connect(self.refresh_library)
        self.only_fav = QCheckBox("★ Favorites")
        self.only_fav.toggled.connect(self.refresh_library)
        filters.addWidget(self.search, stretch=3)
        filters.addWidget(self.filter_status, stretch=1)
        filters.addWidget(self.filter_kind, stretch=1)
        filters.addWidget(self.sort, stretch=1)
        filters.addWidget(self.only_fav)
        layout.addLayout(filters)
        layout.addSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Title", "Author", "Type", "Reading", "Progress", "Rating"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
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
        splitter.addWidget(self.table)

        details = QFrame()
        details.setObjectName("details")
        details.setMinimumWidth(285)
        details.setMaximumWidth(430)
        d_outer = QVBoxLayout(details)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        detail_content = QWidget()
        for surface in (scroll.viewport(), detail_content):
            palette = surface.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#192231"))
            surface.setPalette(palette)
            surface.setAutoFillBackground(True)
        self.details = QVBoxLayout(detail_content)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
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
        layout.addWidget(label("Chapter and page milestones across every work and reread.", "muted"))
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Date", "Work", "Progress", "Note"])
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.history_table)
        self.pages.addWidget(page)

    def create_stats_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Reading statistics", "headline"))
        layout.addWidget(label("A snapshot of your reading history.", "muted"))
        grid = QGridLayout()
        grid.setSpacing(14)
        self.stat_labels = {}
        for i, (key, title) in enumerate((
            ("works", "Works in library"), ("completed_works", "Completed works"),
            ("completed_reads", "Finished reading runs"), ("chapters_logged", "Chapters logged"),
            ("favorites", "Favorites"), ("reading", "Currently reading"))):
            frame = QFrame()
            frame.setObjectName("statCard")
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(22, 22, 22, 22)
            inner.addWidget(label(title, "muted"))
            value = label("0", "value")
            inner.addWidget(value)
            self.stat_labels[key] = value
            grid.addWidget(frame, i // 3, i % 3)
        layout.addLayout(grid)
        layout.addSpacing(30)
        self.stat_breakdown = label("", "subheading")
        layout.addWidget(self.stat_breakdown)
        layout.addStretch()
        self.pages.addWidget(page)

    def refresh_all(self, select_id=None):
        self.refresh_library(select_id=select_id)
        self.refresh_history()
        self.refresh_stats()

    def refresh_library(self, *_args, select_id=None):
        books = self.library.list_works(
            self.search.text(), self.filter_status.currentData(),
            self.filter_kind.currentData(), self.only_fav.isChecked(), self.sort.currentData())
        current = select_id or self.selected_id
        self.table.blockSignals(True)
        self.table.setRowCount(len(books))
        chosen = -1
        for row, book in enumerate(books):
            if book["id"] == current:
                chosen = row
            total = book["total_units"] or (100 if book["progress_unit"] == "percent" else 0)
            progress = f"{book['progress']} / {total}" if total else str(book["progress"])
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
        if chosen >= 0:
            self.table.selectRow(chosen)
        elif books:
            self.table.selectRow(0)
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
                item.widget().deleteLater()
            elif item.layout():
                MainWindow.clear_layout(item.layout())

    def show_selected(self):
        items = self.table.selectedItems()
        if not items:
            return
        work_id = self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
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
            f"Tags: {', '.join(book['tags'])}" if book["tags"] else "",
        ):
            if text:
                self.details.addWidget(label(text))
        runs = self.library.runs(work_id)
        progress = runs[0]["current_progress"] if runs else 0
        self.details.addWidget(label(
            f"Progress: {progress} {book['progress_unit']}" +
            (f" / {book['total_units']}" if book["total_units"] else ""), "subheading"))
        if book["notes"]:
            self.details.addWidget(label("Notes: " + book["notes"]))
        if book["description"]:
            summary = book["description"][:300]
            self.details.addWidget(label(summary + ("…" if len(book["description"]) > 300 else ""), "muted"))
        self.details.addSpacing(10)
        self.details.addWidget(button("Log progress", self.log_progress, primary=True))
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
        self.details.addWidget(label(f"Reading runs: {len(runs)}", "muted"))
        for run in runs[:5]:
            self.details.addWidget(label(
                f"• {run['started_at'][:10]} → {run['finished_at'][:10] or 'in progress'}", "muted"))

    def cover_available(self, url):
        pixmap = self.covers.get(url)
        if pixmap is None:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == url:
                item.setIcon(QIcon(pixmap))
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
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.set_progress(work["id"], dialog.progress.value(),
                                      note=dialog.note.text().strip())
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

    def refresh_history(self):
        events = self.library.history(limit=1000)
        self.history_table.setRowCount(len(events))
        for row, event in enumerate(events):
            delta = event["new_progress"] - event["old_progress"]
            values = (event["recorded_at"][:16].replace("T", " "), event["title"],
                      f"{event['old_progress']} → {event['new_progress']} {event['progress_unit']} ({delta:+d})",
                      event["note"])
            for col, value in enumerate(values):
                self.history_table.setItem(row, col, QTableWidgetItem(value))
            self.history_table.setRowHeight(row, 38)

    def refresh_stats(self):
        stats = self.library.stats()
        for key, widget in self.stat_labels.items():
            widget.setText(f"{stats['by_status']['reading']:,}" if key == "reading"
                           else f"{stats[key]:,}")
        breakdown = "   ·   ".join(f"{name(kind)}: {count}" for kind, count in stats["by_kind"].items())
        self.stat_breakdown.setText("Library breakdown\n" + breakdown)

    def export_library(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export BookTracker library", "BookTracker-backup.json",
            "JSON backup (*.json);;XML backup (*.xml);;CSV catalog (*.csv)")
        if not path:
            return
        try:
            export_file(self.library, path)
            message = "Export complete.\n\nJSON and XML preserve reading history; CSV is a flat catalog only."
            QMessageBox.information(self, "Exported", message)
        except Exception as exc:
            fail(self, exc)

    def import_library(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import library", "", "BookTracker files (*.json *.xml *.csv)")
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
            count = import_file(self.library, path, strategy=strategy)
            self.refresh_all()
            QMessageBox.information(self, "Imported", f"Imported {count} new works.")
        except Exception as exc:
            fail(self, exc)

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
        if self.metadata_worker and self.metadata_worker.isRunning():
            self.metadata_worker.wait()
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
