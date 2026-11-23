"""One model row per page, with thumbnail work scheduled only by visible paints."""
from collections import OrderedDict

import pymupdf as fitz
from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QListView, QStyledItemDelegate


class ThumbnailModel(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.page_count = 0
        self.icons = OrderedDict()

    def reset_pages(self, count):
        self.beginResetModel()
        self.icons.clear()
        self.page_count = count
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.page_count

    def data(self, index, role):
        if not index.isValid():
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row + 1)
        if role == Qt.ItemDataRole.DecorationRole:
            return self.icons.get(row)
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(138, 172)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        return None


class ThumbnailDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        self.parent().request_thumbnail(index.row())


class ThumbnailView(QListView):
    def __init__(self):
        super().__init__()
        self.document = None
        self.pages = ThumbnailModel(self)
        self.setModel(self.pages)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(108, 144))
        self.setSpacing(3)
        self.pending = {}
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.render_next)

    def set_document(self, document):
        self.timer.stop()
        self.pending.clear()
        self.document = document
        self.pages.reset_pages(document.count)

    def invalidate_pages(self, numbers):
        for row in numbers:
            if self.pages.icons.pop(row, None) is not None:
                index = self.pages.index(row)
                self.pages.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def select_page(self, number):
        self.setCurrentIndex(self.pages.index(number))

    def request_thumbnail(self, row):
        if row in self.pages.icons:
            self.pages.icons.move_to_end(row)
        elif self.isVisible():
            self.pending[row] = None
            if not self.timer.isActive():
                self.timer.start()

    def render_next(self):
        while self.pending:
            row = next(iter(self.pending))
            del self.pending[row]
            index = self.pages.index(row)
            if not self.isVisible() or not self.visualRect(index).intersects(self.viewport().rect()):
                continue
            page = self.document.doc[row]
            scale = min(108 / max(page.rect.width, 1), 144 / max(page.rect.height, 1))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            self.pages.icons[row] = QIcon(QPixmap.fromImage(image.copy()))
            limit = max(32, self.viewport().height() // 172 + 2)
            while len(self.pages.icons) > limit:
                self.pages.icons.popitem(last=False)
            self.pages.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
            break
        if not self.pending:
            self.timer.stop()

    def hideEvent(self, event):
        self.timer.stop()
        self.pending.clear()
        super().hideEvent(event)
