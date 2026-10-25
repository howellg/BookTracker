"""Manual shared-folder sync: review per-field conflicts, never touch peer databases."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import sync


def _txt(string):
    text=QLabel(str(string))
    text.setWordWrap(True)
    return text

def _btn(text,call):
    widget=QPushButton(text)
    widget.setMinimumHeight(34)
    widget.clicked.connect(call)
    return widget

def _short(value,limit=90):
    text=str(value)
    return text if len(text)<=limit else text[:limit-1]+"…"


class SyncReview(QDialog):
    """Conservative, explicit per-field decision for a single peer revision."""

    def __init__(self,parent,review):
        super().__init__(parent)
        self.review=review
        self.setWindowTitle(f"Review sync: {review.peer.device_name} revision {review.peer.revision}")
        self.resize(1040,650)
        self.setMinimumSize(730,470)
        layout=QVBoxLayout(self)
        layout.addWidget(_txt(
            f"From {review.peer.device_name} · revision {review.peer.revision} "
            f"· {review.new_rows} incoming records · "
            f"{len(review.safe)} unambiguous field updates · "
            f"{len(review.conflicts)} items needing review."
        ))
        layout.addWidget(_txt(
            "New additive records and unambiguous field changes are applied. "
            "For conflicting fields, leave Keep local (default), or tick Use peer. "
            "Deletions and other unsupported edits are NEVER automatically applied. "
            "Possible same-title duplicates are imported as separate books, not merged. "
            "Cancel the entire import if that is not what you want. "
            "A full local SQLite backup is created before applying."
        ))
        self.table=QTableWidget(len(review.conflicts),5)
        self.table.setHorizontalHeaderLabels([
            "Source record","Field / issue","Your computer","Other computer","Decision"
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().resizeSection(0,220)
        for i in (1,2,3):
            self.table.horizontalHeader().setSectionResizeMode(
                i,QHeaderView.ResizeMode.Stretch
            )
        self.table.horizontalHeader().setSectionResizeMode(
            4,QHeaderView.ResizeMode.ResizeToContents
        )
        self.boxes=[]
        for row,change in enumerate(review.conflicts):
            values=(
                change.table+" / "+_short(",".join(change.key),24),
                change.field,_short(change.local),_short(change.remote)
            )
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value)
                cell.setToolTip(str(change.local if col==2 else
                                     change.remote if col==3 else value))
                self.table.setItem(row,col,cell)
            if change.kind=="conflict":
                choose=QCheckBox("Use peer")
                choose.setToolTip("Replace just this field with the peer value.")
                self.table.setCellWidget(row,4,choose)
                self.boxes.append((row,choose))
            else:
                meaning=(
                    "Will import separately" if change.kind=="duplicate"
                    else "Keep local (not auto-applied)"
                )
                self.table.setItem(row,4,QTableWidgetItem(meaning))
            self.table.setRowHeight(row,39)
        layout.addWidget(self.table,stretch=1)
        self.warning=_txt(
            "Note: This is sync of library records, not audiobook files or live "
            "SQLite databases. A deleted record stays deleted locally and is "
            "not automatically removed from other computers. Device sync "
            "requires a trusted folder: snapshots are not encrypted."
        )
        layout.addWidget(self.warning)
        actions=QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("Apply reviewed changes")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def selections(self):
        return [row for row,box in self.boxes if box.isChecked()]


class SyncPage(QWidget):
    def __init__(self,window,library):
        super().__init__(window)
        self.main=window
        self.library=library
        self.peers=[]
        self.setObjectName("syncPage")
        layout=QVBoxLayout(self)
        layout.addWidget(_txt("Cross-device sync"))
        layout.addWidget(_txt(
            "Share complete BookTracker snapshots through a folder synchronized "
            "by Syncthing, a network drive, or a removable disk. Every computer "
            "keeps its own SQLite database. Only manually approved changes import."
        ))
        naming=QHBoxLayout()
        naming.addWidget(_txt("This computer:"))
        self.device_name=QLineEdit(sync.device_info(library)["display_name"])
        self.device_name.setMaximumWidth(240)
        naming.addWidget(self.device_name)
        naming.addWidget(_btn("Rename",self.rename))
        naming.addWidget(_btn("New device ID",self.reset_identity))
        self.device_id=_txt("Device ID: "+sync.device_info(library)["device_id"])
        naming.addWidget(self.device_id)
        naming.addStretch()
        layout.addLayout(naming)
        location=QHBoxLayout()
        self.folder=QLineEdit(
            self.main.settings.value("sync_folder","",type=str)
        )
        self.folder.setPlaceholderText("Choose a trusted shared folder …")
        location.addWidget(self.folder,stretch=1)
        location.addWidget(_btn("Choose folder",self.choose_folder))
        layout.addLayout(location)
        controls=QHBoxLayout()
        controls.addWidget(_btn("Publish my changes",self.publish))
        controls.addWidget(_btn("Find peers",self.scan))
        controls.addWidget(_btn("Publish + find peers",self.publish_and_scan))
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(_txt(
            "1. Configure the SAME Syncthing folder on each computer. "
            "2. Publish on each computer. 3. Find peers here and review "
            "their changes. 4. Publish again after merging so other devices "
            "can receive your merged library."
        ))
        self.message=_txt("No sync operations have run.")
        layout.addWidget(self.message)
        self.table=QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels([
            "Computer","Latest snapshot","Last imported","Status","File"
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4,QHeaderView.ResizeMode.Stretch
        )
        for col in (1,2,3):
            self.table.horizontalHeader().setSectionResizeMode(
                col,QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.itemDoubleClicked.connect(lambda _:self.review_peer())
        layout.addWidget(self.table,stretch=1)
        actions=QHBoxLayout()
        actions.addWidget(_btn("Review selected peer",self.review_peer))
        actions.addWidget(_btn("Open sync folder",self.show_folder))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(_txt(
            "Safety: synchronized snapshots contain book details, notes, "
            "reading history and covers. The folder must be trusted and "
            "access-controlled; files are not encrypted. Media content and "
            "machine-specific absolute paths are never included. "
            "Automatic deletion propagation is intentionally disabled."
        ))

    def path(self,create=False):
        value=self.folder.text().strip()
        if not value:
            raise ValueError("Choose a shared sync folder first.")
        path=Path(value).expanduser()
        if create:
            path.mkdir(parents=True,exist_ok=True)
        if not path.is_dir():
            raise ValueError("The shared sync folder is unavailable.")
        self.main.settings.setValue("sync_folder",str(path.resolve()))
        return path

    def choose_folder(self):
        selected=QFileDialog.getExistingDirectory(
            self,"Choose a trusted Syncthing or shared folder",
            self.folder.text() or str(Path.home())
        )
        if selected:
            self.folder.setText(selected)
            self.main.settings.setValue("sync_folder",selected)

    def rename(self):
        try:
            sync.set_device_name(self.library,self.device_name.text())
            self.message.setText("Computer name saved.")
        except Exception as exc:
            QMessageBox.warning(self,"Computer name",str(exc))

    def reset_identity(self):
        if QMessageBox.question(
            self,"Assign a new sync identity?",
            "ONLY do this if you copied/restored the entire BookTracker SQLite "
            "database from another computer and both now share a device ID. "
            "This computer will start publishing as a separate device and "
            "forget the previously imported sync revision markers. "
            "All books, notes and reading history remain. Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )!=QMessageBox.StandardButton.Yes:
            return
        try:
            identifier=sync.new_device_identity(self.library)
            self.device_id.setText("Device ID: "+identifier)
            self.peers=[]
            self.table.setRowCount(0)
            self.message.setText(
                "A new device identity was assigned. Review your peers "
                "before importing; previous sync baselines were cleared."
            )
        except Exception as exc:
            QMessageBox.warning(self,"Cannot change device identity",str(exc))

    def publish(self):
        try:
            file=sync.publish(self.library,self.path(create=True))
            self.message.setText(
                f"Published {file.name}. Other computers can now find it. "
                "This does not import any peer changes."
            )
            return True
        except Exception as exc:
            QMessageBox.warning(self,"Cannot publish sync snapshot",str(exc))
            return False

    def publish_and_scan(self):
        if self.publish():
            self.scan()

    def scan(self):
        try:
            peers,errors=sync.available_peers(self.library,self.path())
            self.peers=peers
            self.table.setRowCount(len(peers))
            for i,peer in enumerate(peers):
                row=self.library.db.execute(
                    "SELECT revision FROM sync_peers WHERE device_id=?",
                    (peer.device_id,)
                ).fetchone()
                last=row["revision"] if row else 0
                fields=(
                    peer.device_name,str(peer.revision),
                    str(last) if last else "Never",
                    "New changes" if peer.revision>last else "Up to date",
                    peer.path.name
                )
                for col,value in enumerate(fields):
                    cell=QTableWidgetItem(value)
                    if col==0:
                        cell.setData(Qt.ItemDataRole.UserRole,peer.device_id)
                    self.table.setItem(i,col,cell)
                self.table.setRowHeight(i,36)
            if peers:
                self.table.selectRow(0)
            self.message.setText(
                f"Found {len(peers)} other computers."
                + (f" {len(errors)} damaged or unreadable snapshot(s) ignored."
                   if errors else "")
            )
        except Exception as exc:
            QMessageBox.warning(self,"Sync folder scan",str(exc))

    def selected_peer(self):
        row=self.table.currentRow()
        cell=self.table.item(row,0) if row>=0 else None
        key=cell.data(Qt.ItemDataRole.UserRole) if cell else None
        return next((peer for peer in self.peers if peer.device_id==key),None)

    def review_peer(self):
        peer=self.selected_peer()
        if not peer:
            return
        try:
            review=sync.plan(self.library,peer)
            dialog=SyncReview(self,review)
            if dialog.exec()!=QDialog.DialogCode.Accepted:
                return
            result=sync.apply(self.library,review,use_peer=dialog.selections())
            self.message.setText(
                f"Imported revision {peer.revision} from {peer.device_name}. "
                f"New works: {result['new_works']}. "
                f"Safe updates: {result['safe_updates']}. "
                f"Peer choices: {result['chosen_conflicts']}. "
                f"Kept local: {result['kept_local']}. "
                "Publish again to share merged changes."
            )
            self.main.refresh_all()
            self.scan()
        except Exception as exc:
            QMessageBox.warning(self,"Sync review failed",str(exc))

    def show_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path())))
        except Exception as exc:
            QMessageBox.warning(self,"Cannot open sync folder",str(exc))
