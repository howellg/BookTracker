"""Async, disk-cached book covers for the Qt library UI.

Only known public book-cover hosts are contacted. Both memory and disk caches
store small thumbnails; cover lookup never blocks the library's UI thread.
"""
from __future__ import annotations

import hashlib
import os
from collections import deque
from pathlib import Path

from platformdirs import user_cache_dir
from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

TRUSTED_COVERS = {
    "covers.openlibrary.org", "books.google.com", "books.googleusercontent.com",
    "lh3.googleusercontent.com",
}
MAX_IMAGE_BYTES = 2_000_000
THUMB_SIZE = (124, 178)


def cover_url_for(book: dict) -> str:
    """Use a source cover URL, or a public ISBN cover as a fallback."""
    url = str(book.get("cover_url") or "")
    if is_allowed_cover(url):
        return url
    isbn = str(book.get("isbn") or "").strip()
    if isbn and isbn.replace("-", "").isalnum():
        return "https://covers.openlibrary.org/b/isbn/" + isbn.replace("-", "") + "-M.jpg?default=false"
    return ""


def is_allowed_cover(url: str) -> bool:
    parsed = QUrl(url)
    return parsed.isValid() and parsed.scheme() == "https" and parsed.host() in TRUSTED_COVERS


def _cache_file(folder: Path, url: str) -> Path:
    return folder / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".png")


class CoverCache(QObject):
    """A small bounded cover downloader with persistent thumbnails."""

    cover_ready = Signal(str)

    def __init__(self, parent=None, *, folder: str | Path | None = None):
        super().__init__(parent)
        self.folder = Path(folder) if folder else Path(user_cache_dir("BookTracker", "BookTracker")) / "covers"
        self.network = QNetworkAccessManager(self)
        self.memory: dict[str, QPixmap] = {}
        self.waiting: deque[str] = deque()
        self.pending: set[str] = set()
        self.active: set[str] = set()
        self.max_active = 3

    def get(self, url: str) -> QPixmap | None:
        if not is_allowed_cover(url):
            return None
        if url in self.memory:
            return self.memory[url]
        path = _cache_file(self.folder, url)
        if path.exists():
            image = QPixmap(str(path))
            if not image.isNull():
                self.memory[url] = image
                return image
            try:
                path.unlink()
            except OSError:
                pass
        if url not in self.pending:
            self.pending.add(url)
            self.waiting.append(url)
            self._pump()
        return None

    def _pump(self):
        while len(self.active) < self.max_active and self.waiting:
            url = self.waiting.popleft()
            self.active.add(url)
            self._request(url, url, 0)

    def _request(self, original_url: str, request_url: str, hops: int):
        request = QNetworkRequest(QUrl(request_url))
        request.setTransferTimeout(8000)
        # Only follow redirects when the destination is an approved HTTPS
        # public cover host. This avoids unexpected arbitrary-host requests.
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        reply = self.network.get(request)

        def on_progress(received, _total, reply=reply):
            if received > MAX_IMAGE_BYTES:
                reply.abort()

        reply.downloadProgress.connect(on_progress)
        reply.finished.connect(
            lambda url=original_url, reply=reply, hops=hops: self._finished(url, reply, hops)
        )

    def _finished(self, url, reply, hops):
        redirecting = False
        try:
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if code in (301, 302, 303, 307, 308) and hops < 3:
                location = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
                if isinstance(location, QUrl):
                    target = reply.url().resolved(location).toString()
                    if is_allowed_cover(target):
                        redirecting = True
                        self._request(url, target, hops + 1)
                        return
            if reply.error() != QNetworkReply.NetworkError.NoError or code != 200:
                return
            data = bytes(reply.readAll())
            if not data or len(data) > MAX_IMAGE_BYTES:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return
            if pixmap.width() > 6000 or pixmap.height() > 6000:
                return
            thumb = pixmap.scaled(
                *THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.memory[url] = thumb
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                dest = _cache_file(self.folder, url)
                temp = dest.with_suffix(".tmp.png")
                if thumb.save(str(temp), "PNG"):
                    os.replace(temp, dest)
            except OSError:
                pass
            self.cover_ready.emit(url)
        finally:
            reply.deleteLater()
            if not redirecting:
                self.active.discard(url)
                self.pending.discard(url)
                self._pump()
