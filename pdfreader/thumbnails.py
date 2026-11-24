"""One model row per page, with thumbnail work scheduled only by visible paints."""
from collections import OrderedDict

import pymupdf as fitz
from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate


class ThumbnailModel(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.aspects = ()
        self.icons = OrderedDict()
        self.targets = {}
        self.cache_bytes = 0

    def reset_pages(self, aspects):
        self.beginResetModel()
        self.icons.clear()
        self.targets.clear()
        self.cache_bytes = 0
        self.aspects = aspects
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.aspects)

    def data(self, index, role):
        if not index.isValid():
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row + 1)
        if role in (Qt.ItemDataRole.AccessibleTextRole, Qt.ItemDataRole.ToolTipRole):
            return f"第 {row + 1} 页"
        return None

    def discard(self, row):
        image = self.icons.pop(row, None)
        if image is not None:
            self.cache_bytes -= image.width() * image.height() * 4
            del self.targets[row]

    def store(self, row, image, target):
        self.discard(row)
        self.icons[row] = image
        self.targets[row] = target
        self.cache_bytes += image.width() * image.height() * 4
        view = self.parent()
        while len(self.icons) > 32 or self.cache_bytes > 24 * 1024**2:
            # On an unusually tall or high-DPI display the visible set itself
            # can exceed the budget. Evicting it would start a repaint loop.
            victim = next((n for n in self.icons if not view.visualRect(self.index(n)).intersects(view.viewport().rect())), None)
            if victim is None:
                break
            self.discard(victim)


class ThumbnailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        view = self.parent()
        size = view.paper_size(index.row())
        return QSize(1, size.height() + view.fontMetrics().height() + 23)

    def paint(self, painter, option, index):
        view, row = self.parent(), index.row()
        size = view.paper_size(row)
        area = option.rect
        paper = QRect(area.x() + (area.width() - size.width()) // 2, area.y() + 9,
                      size.width(), size.height())
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setClipRect(area)
        painter.fillRect(paper, Qt.GlobalColor.white)
        image = view.pages.icons.get(row)
        if image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(paper, image, image.rect())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#5083d5" if selected else "#a8b6ca" if hovered else "#d9dee6"), 2 if selected else 1))
        painter.drawRect(paper.adjusted(-1, -1, 0, 0))
        painter.setPen(QColor("#285bbc" if selected else "#616c7a"))
        caption = QRect(area.x(), paper.bottom() + 5, area.width(), view.fontMetrics().height() + 2)
        painter.drawText(caption, Qt.AlignmentFlag.AlignCenter, str(row + 1))
        painter.restore()
        view.request_thumbnail(row)


class ThumbnailView(QListView):
    def __init__(self):
        super().__init__()
        self.document = None
        self.pages = ThumbnailModel(self)
        self.setModel(self.pages)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setAccessibleName("页面缩略图")
        self.pending = {}
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.render_next)
        self.reveal_timer = QTimer(self)
        self.reveal_timer.setSingleShot(True)
        self.reveal_timer.setInterval(70)
        self.reveal_timer.timeout.connect(self.reveal_current)

    def set_document(self, document, items):
        self.timer.stop()
        self.reveal_timer.stop()
        self.pending.clear()
        self.document = document
        self.pages.reset_pages(tuple(item.size.width() / max(1, item.size.height()) for item in items))

    def paper_size(self, row):
        width = max(1, self.viewport().width() - 32)
        aspect = max(.0001, self.pages.aspects[row])
        height = min(320, round(width * 1.45), max(1, round(width / aspect)))
        return QSize(max(1, min(width, round(height * aspect))), height)

    def invalidate_pages(self, numbers):
        for row in numbers:
            if row in self.pages.icons:
                self.pages.discard(row)
                self.viewport().update(self.visualRect(self.pages.index(row)))

    def select_page(self, number):
        self.setCurrentIndex(self.pages.index(number))

    def request_thumbnail(self, row):
        image = self.pages.icons.get(row)
        if image is not None:
            self.pages.icons.move_to_end(row)
            if self.pages.targets[row] == (self.paper_size(row), self.devicePixelRatioF()):
                return
        if self.isVisible():
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
            size = self.paper_size(row)
            dpr = self.devicePixelRatioF()
            # Derive the scale from the longer edge. Rounding the shorter edge
            # to one pixel must not shrink the long edge of a panoramic page.
            scale = dpr * (size.width() / page.rect.width if page.rect.width >= page.rect.height
                           else size.height() / page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            thumbnail = QPixmap.fromImage(image.copy())
            thumbnail.setDevicePixelRatio(dpr)
            self.pages.store(row, thumbnail, (size, dpr))
            self.viewport().update(self.visualRect(index))
            break
        if not self.pending:
            self.timer.stop()

    def hideEvent(self, event):
        self.timer.stop()
        self.reveal_timer.stop()
        self.pending.clear()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.reveal_current()

    def reveal_current(self):
        if self.currentIndex().isValid():
            self.scrollTo(self.currentIndex())

    def resizeEvent(self, event):
        old_viewport = QRect(0, 0, event.oldSize().width(), event.oldSize().height())
        followed_page = old_viewport.intersects(self.visualRect(self.currentIndex()))
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            # Qt's non-wrapping list can retain the previous Y positions even
            # though the delegate now reports taller or shorter rows.
            self.scheduleDelayedItemsLayout()
            if followed_page:
                self.reveal_timer.start()

    def keyPressEvent(self, event):
        previous = self.currentIndex()
        super().keyPressEvent(event)
        if self.currentIndex() != previous and self.currentIndex().isValid():
            self.activated.emit(self.currentIndex())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.viewport().update()
