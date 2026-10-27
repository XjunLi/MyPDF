from collections import OrderedDict
import math
import pymupdf as fitz
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem, QApplication, QMenu
from .text_selection import TextPage, distance_to_rect
from .rendering import PageRenderer

TEXT_TOOLS = {"select"}


def qrect(rect):
    return QRectF(rect.x0, rect.y0, rect.width, rect.height)


class PageItem(QGraphicsItem):
    def __init__(self, viewer, number):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.viewer, self.number = viewer, number
        self.size = qrect(viewer.document.doc[number].rect)

    def boundingRect(self):
        return self.size.adjusted(-3, -2, 5, 6)

    def paint(self, painter, option, widget=None):
        painter.fillRect(self.size.translated(1.5, 2.5), QColor("#d9dce0"))
        painter.fillRect(self.size, Qt.GlobalColor.white)
        try:
            area = option.exposedRect.intersected(self.size)
            self.viewer.renderer.paint(painter, self.number, (area.left(), area.top(), area.right(), area.bottom()))
        except Exception as exc:
            painter.setPen(QColor("#a03e3e"))
            painter.drawText(self.size.adjusted(24, 24, -24, -24), Qt.TextFlag.TextWordWrap, str(exc))
        page = self.viewer.document.doc[self.number]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 194, 58, 90))
        for rect in self.viewer.search_boxes.get(self.number, []):
            painter.drawRect(qrect(fitz.Rect(rect) * page.rotation_matrix))
        painter.setBrush(QColor(63, 130, 244, 82))
        for rect in self.viewer.text_boxes.get(self.number, []):
            painter.drawRect(qrect(fitz.Rect(rect) * page.rotation_matrix))
        selected = self.viewer.selection
        if selected and selected[0] == self.number:
            painter.setBrush(QColor(45, 103, 221, 26))
            pen = QPen(QColor("#4e7ec9"), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(qrect(fitz.Rect(selected[1]) * page.rotation_matrix))
        if self.viewer.drag_page == self.number and self.viewer.ink:
            painter.setPen(QPen(QColor("#24364c"), 1.4))
            points = [fitz.Point(p) * page.rotation_matrix for p in self.viewer.ink]
            for a, b in zip(points, points[1:]):
                painter.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))


class PdfView(QGraphicsView):
    regionChosen = Signal(str, int, object)
    markupChosen = Signal(str, object)
    inkChosen = Signal(int, object)
    selectionChanged = Signal(str)
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    filesDropped = Signal(object)
    editRequested = Signal(int, object)
    aiRequested = Signal()
    commentRequested = Signal(object, str)
    annotationActivated = Signal(int, int)
    message = Signal(str)

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.setBackgroundBrush(QColor("#edeef0"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.mode, self.zoom, self.current_page = "select", 1.0, 0
        self.points_to_pixels = self.logicalDpiX() / 72.0
        self.items_by_page = []
        self.renderer = PageRenderer(document)
        self.text_cache = OrderedDict()
        self.text_boxes, self.search_boxes = {}, {}
        self.selected_text = ""
        self.text_anchor = self.text_focus = self.selection = None
        self.drag_page = self.drag_start = self.drag_pos = None
        self.dragging_text = self.double_clicked = False
        self.ink = []
        self.press_pos = None
        self.point_dragged = False
        self.annotation_hits = []
        self.fit_mode, self.rebuilding = True, False
        self.verticalScrollBar().valueChanged.connect(self._page_at_center)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(lambda: self.fit_width() if self.fit_mode else None)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(25)
        self.scroll_timer.timeout.connect(self._auto_scroll)

    def clear_selection(self):
        self.text_boxes.clear()
        self.selected_text = ""
        self.selection = self.text_anchor = self.text_focus = None
        self.selectionChanged.emit("")
        self.viewport().update()

    def rebuild(self, keep_position=True):
        number = min(self.current_page, max(0, self.document.count - 1)) if keep_position else 0
        center = self.mapToScene(self.viewport().rect().center())
        offset = center - self.items_by_page[number].pos() if keep_position and number < len(self.items_by_page) else None
        self.rebuilding = True
        self.scroll_timer.stop()
        self.renderer.clear()
        self.text_cache.clear()
        self.clear_selection()
        self.dragging_text = False
        self.drag_page = self.drag_start = None
        self.ink = []
        self.annotation_hits = self.document.annotations() if self.document.count else []
        self.scene_obj.clear()
        self.items_by_page.clear()
        if not self.document.count:
            self.rebuilding = False
            return
        maxwidth = max(p.rect.width for p in self.document.doc)
        y = 24
        for n, p in enumerate(self.document.doc):
            item = PageItem(self, n)
            item.setPos(32 + (maxwidth - p.rect.width) / 2, y)
            self.scene_obj.addItem(item)
            self.items_by_page.append(item)
            y += p.rect.height + 24
        self.scene_obj.setSceneRect(0, 0, maxwidth + 64, y)
        self.current_page = number
        if self.fit_mode:
            self.fit_width()
        if offset is not None:
            self.centerOn(self.items_by_page[number].pos() + offset)
        else:
            self.verticalScrollBar().setValue(0)
        self.rebuilding = False
        self.pageChanged.emit(number)

    def text_page(self, number):
        key = self.document.revision, number
        if key not in self.text_cache:
            self.text_cache[key] = TextPage(self.document.doc[number])
            while len(self.text_cache) > 24:
                self.text_cache.popitem(last=False)
        self.text_cache.move_to_end(key)
        return self.text_cache[key]

    def set_mode(self, mode):
        if mode in ("highlight", "underline", "strike"):
            mode = "select"
        self.mode = mode
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if mode == "hand" else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if mode == "hand" else Qt.CursorShape.IBeamCursor if mode in TEXT_TOOLS else Qt.CursorShape.CrossCursor)

    def set_zoom(self, value, manual=True, anchor=None):
        if not math.isfinite(value):
            return
        anchor = anchor or self.viewport().rect().center()
        before = self.mapToScene(anchor)
        self.zoom = max(.15, min(5, value))
        if manual:
            self.fit_mode = False
            self.resize_timer.stop()
        display_zoom = self.zoom * self.points_to_pixels
        self.setTransform(QTransform.fromScale(display_zoom, display_zoom))
        center = self.viewport().rect().center()
        self.centerOn(before + QPointF((center.x() - anchor.x()) / display_zoom, (center.y() - anchor.y()) / display_zoom))
        self.zoomChanged.emit(self.zoom)
        self.viewport().update()

    def fit_width(self):
        if self.document.count:
            self.set_zoom((self.viewport().width() - 4) / self.scene_obj.sceneRect().width() / self.points_to_pixels, manual=False)

    def fit_page(self):
        if self.document.count:
            rect = self.document.doc[self.current_page].rect
            self.set_zoom(min((self.viewport().width() - 64) / rect.width, (self.viewport().height() - 48) / rect.height) / self.points_to_pixels)
            self.goto(self.current_page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.resize_timer.start(70)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.set_zoom(self.zoom * (1.12 if delta > 0 else 1 / 1.12), anchor=event.position().toPoint())
            event.accept()
        else:
            super().wheelEvent(event)

    def _page_at_center(self):
        if not self.items_by_page or self.rebuilding:
            return
        y = self.mapToScene(self.viewport().rect().center()).y()
        number = min(range(len(self.items_by_page)), key=lambda n: abs(self.items_by_page[n].pos().y() + self.items_by_page[n].size.height() / 2 - y))
        if number != self.current_page:
            self.current_page = number
            self.pageChanged.emit(number)

    def goto(self, page, rect=None):
        if not self.items_by_page:
            return
        page = max(0, min(int(page), len(self.items_by_page) - 1))
        item = self.items_by_page[page]
        if rect is not None:
            r = fitz.Rect(rect) * self.document.doc[page].rotation_matrix
            self.centerOn(item.mapToScene(QPointF((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)))
        else:
            delta = self.mapFromScene(item.pos()).y() - 24
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta)
        self.current_page = page
        self.pageChanged.emit(page)

    def locate(self, pos, fixed_page=None, nearest=False):
        scene_pos = self.mapToScene(pos)
        if fixed_page is None:
            item = next((i for i in self.scene_obj.items(scene_pos) if isinstance(i, PageItem)), None)
            if item is None and nearest and self.items_by_page:
                point = fitz.Point(scene_pos.x(), scene_pos.y())
                item = min(self.items_by_page, key=lambda i: distance_to_rect(point, fitz.Rect(i.pos().x(), i.pos().y(), i.pos().x() + i.size.width(), i.pos().y() + i.size.height())))
        else:
            item = self.items_by_page[fixed_page]
        if item is None:
            return None
        p = self.document.doc[item.number]
        local = item.mapFromScene(scene_pos)
        if not nearest and fixed_page is None and not item.size.contains(local):
            return None
        point = fitz.Point(max(0, min(local.x(), p.rect.width)), max(0, min(local.y(), p.rect.height))) * p.derotation_matrix
        return item.number, point

    def update_text_selection(self):
        self.text_boxes = {}
        pieces = []
        if self.text_anchor is not None and self.text_focus is not None:
            start, end = sorted((self.text_anchor, self.text_focus))
            for number in range(start[0], end[0] + 1):
                page = self.text_page(number)
                first = start[1] if number == start[0] else 0
                last = end[1] if number == end[0] else len(page.glyphs)
                text, boxes = page.range(first, last)
                if boxes:
                    self.text_boxes[number] = boxes
                    pieces.append(text)
        self.selected_text = "\n\n".join(pieces) if self.document.doc.permissions & fitz.PDF_PERM_COPY else ""
        self.selectionChanged.emit(self.selected_text)
        self.viewport().update()

    def _extend_text_selection(self, position):
        hit = self.locate(position, nearest=True)
        if hit:
            number, point = hit
            index = self.text_page(number).caret(point)
            self.text_focus = (number, index if index is not None else 0)
            self.update_text_selection()

    def _auto_scroll(self):
        if not self.dragging_text or self.drag_pos is None:
            self.scroll_timer.stop()
            return
        y, edge, height = self.drag_pos.y(), 32, self.viewport().height()
        delta = min(48, max(2, (y - height + edge) * .45)) if y > height - edge else -min(48, max(2, (edge - y) * .45)) if y < edge else 0
        if delta:
            bar = self.verticalScrollBar()
            previous = bar.value()
            bar.setValue(previous + round(delta))
            if previous != bar.value():
                self._extend_text_selection(self.drag_pos)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.mode == "hand":
            return super().mousePressEvent(event)
        self.double_clicked = False
        self.press_pos = event.position().toPoint()
        self.point_dragged = False
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        hit = self.locate(event.position().toPoint())
        if not hit:
            self.clear_selection()
            event.accept()
            return
        number, point = hit
        if self.mode in TEXT_TOOLS:
            caret = self.text_page(number).caret(point, near=True)
            if caret is None:
                self.clear_selection()
                if not self.text_page(number).glyphs:
                    self.message.emit("此页没有文字层，完成 OCR 后即可划词。")
                event.accept()
                return
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.text_anchor):
                self.text_anchor = (number, caret)
            self.text_focus = (number, caret)
            self.selection = None
            self.dragging_text = True
            self.drag_pos = event.position().toPoint()
            self.update_text_selection()
            self.scroll_timer.start()
        else:
            self.clear_selection()
            self.drag_page, self.drag_start = number, point
            self.ink = [tuple(point)] if self.mode == "ink" else []
            self.selection = None if self.mode == "comment" else (number, tuple(fitz.Rect(point, point)))
        self.viewport().update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging_text:
            self.drag_pos = event.position().toPoint()
            self._extend_text_selection(self.drag_pos)
            event.accept()
        elif self.drag_page is not None and self.drag_start is not None:
            if self.mode == "comment":
                if self.press_pos and (event.position().toPoint() - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
                    self.point_dragged = True
                event.accept()
                return
            _, point = self.locate(event.position().toPoint(), self.drag_page)
            self.selection = (self.drag_page, tuple(fitz.Rect(self.drag_start, point).normalize()))
            if self.mode == "ink":
                self.ink.append(tuple(point))
            self.viewport().update()
            event.accept()
        else:
            if self.mode == "select":
                hit = self.locate(event.position().toPoint())
                on_text = hit and self.text_page(hit[0]).caret(hit[1], near=True) is not None
                self.viewport().setCursor(Qt.CursorShape.IBeamCursor if on_text else Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.mode == "hand":
            return super().mouseReleaseEvent(event)
        self.scroll_timer.stop()
        if self.double_clicked:
            self.double_clicked = False
            event.accept()
            return
        if self.dragging_text:
            self._extend_text_selection(event.position().toPoint())
            self.dragging_text = False
            if not self.text_boxes:
                self.activate_annotation(event.position().toPoint())
            event.accept()
            return
        if self.drag_page is None:
            if self.mode == "select":
                self.activate_annotation(event.position().toPoint())
            return super().mouseReleaseEvent(event)
        number, point, selection, ink = self.drag_page, self.drag_start, self.selection, self.ink[:]
        self.drag_page = self.drag_start = None
        self.ink = []
        if self.mode == "ink":
            self.selection = None
            if len(ink) > 1:
                self.inkChosen.emit(number, [ink])
        elif self.mode == "comment":
            self.selection = None
            moved = (event.position().toPoint() - self.press_pos).manhattanLength() if self.press_pos else 0
            if not self.point_dragged and moved < QApplication.startDragDistance():
                self.regionChosen.emit(self.mode, number, tuple(fitz.Rect(point.x, point.y, point.x + 18, point.y + 18)))
            else:
                self.message.emit("便笺只需单击。文字注释请先划词，再点注释。")
        elif self.mode == "edit" and selection and fitz.Rect(selection[1]).width < 6:
            self.editRequested.emit(number, tuple(point))
        elif selection and fitz.Rect(selection[1]).width > 2 and fitz.Rect(selection[1]).height > 2:
            self.regionChosen.emit(self.mode, number, selection[1])
        else:
            self.clear_selection()
        self.viewport().update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        hit = self.locate(event.position().toPoint())
        if hit and self.mode in TEXT_TOOLS:
            found = self.text_page(hit[0]).word(hit[1])
            self.scroll_timer.stop()
            self.dragging_text = False
            self.double_clicked = True
            if found:
                self.text_anchor = hit[0], found[0]
                self.text_focus = hit[0], found[1]
                self.update_text_selection()
            event.accept()
        elif hit and self.mode == "edit":
            self.drag_page = self.drag_start = None
            self.editRequested.emit(hit[0], tuple(hit[1]))
        else:
            super().mouseDoubleClickEvent(event)

    def copy_selection(self):
        if self.selected_text:
            QApplication.clipboard().setText(self.selected_text)

    def activate_annotation(self, position):
        if self.press_pos is None or (position - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
            return
        hit = self.locate(position)
        if not hit:
            return
        for entry in reversed(self.annotation_hits):
            boxes = entry.get("marked_boxes") or [entry["rect"]]
            if entry["page"] == hit[0] and any(fitz.Rect(rect).contains(hit[1]) for rect in boxes):
                self.annotationActivated.emit(entry["page"], entry["xref"])
                break

    def contextMenuEvent(self, event):
        if not self.text_boxes:
            return super().contextMenuEvent(event)
        menu = QMenu(self)
        copy = menu.addAction("复制  Ctrl+C", self.copy_selection)
        copy.setEnabled(bool(self.selected_text))
        for kind, name in (("highlight", "高亮"), ("underline", "下划线"), ("strike", "删除线")):
            menu.addAction(name, lambda m=kind: self.markupChosen.emit(m, dict(self.text_boxes)))
        menu.addAction("注释所选文字", lambda: self.commentRequested.emit(dict(self.text_boxes), self.selected_text))
        menu.addSeparator()
        ai = menu.addAction("询问 AI", lambda: self.aiRequested.emit())
        ai.setEnabled(bool(self.selected_text))
        menu.exec(event.globalPos())

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if ctrl and event.key() == Qt.Key.Key_C:
            self.copy_selection()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.dragging_text = False
            self.scroll_timer.stop()
            self.clear_selection()
            event.accept()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")]
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)
