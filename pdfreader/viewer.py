from collections import OrderedDict
from bisect import bisect_right
import math
import pymupdf as fitz
from PySide6.QtCore import Qt, QRectF, QPointF, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem, QApplication, QMenu
from .text_selection import TextPage, distance_to_rect
from .rendering import PageRenderer
from .application import shortcut_text

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
            editor=self.viewer.inline_editor
            renderer=editor.background_renderer if editor and editor.number==self.number and editor.background_renderer else self.viewer.renderer
            renderer.paint(painter,0 if renderer is not self.viewer.renderer else self.number,(area.left(),area.top(),area.right(),area.bottom()))
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
            painter.setPen(QPen(self.viewer.ink_color,self.viewer.ink_width))
            points = [fitz.Point(p) * page.rotation_matrix for p in self.viewer.ink]
            for a, b in zip(points, points[1:]):
                painter.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))


class PdfView(QGraphicsView):
    textInsertRequested = Signal(int, object)
    inlineStarted = Signal()
    inlineFinished = Signal()
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
        self.inline_editor = None
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
        self.ink_color=QColor("#25364c"); self.ink_width=1.1
        self.press_pos = None
        self.point_dragged = False
        self.annotations_by_page = {}
        self.link_page = None
        self.page_links = []
        self.fit_mode = "width"
        self.last_fit_viewport = None
        self.rebuilding = self.fitting = False
        self.verticalScrollBar().valueChanged.connect(self._page_at_center)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.apply_fit)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(25)
        self.scroll_timer.timeout.connect(self._auto_scroll)
        self.horizontalScrollBar().valueChanged.connect(lambda:self.inline_editor.position() if self.inline_editor else None)

    def clear_selection(self):
        marked = set(self.text_boxes)
        if self.selection:
            marked.add(self.selection[0])
        self.text_boxes.clear()
        self.selected_text = ""
        self.selection = self.text_anchor = self.text_focus = None
        self.selectionChanged.emit("")
        for number in marked:
            if number < len(self.items_by_page):
                self.items_by_page[number].update()

    def refresh_annotations(self, numbers):
        self.renderer.invalidate_pages(numbers)
        self.clear_selection()
        for number in numbers:
            entries = self.document.annotations(number)
            if entries:
                self.annotations_by_page[number] = entries
            else:
                self.annotations_by_page.pop(number, None)
            self.items_by_page[number].update()
        if self.link_page in numbers:
            self.link_page = None

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
        self.annotations_by_page.clear()
        if self.document.count:
            for entry in self.document.annotations():
                self.annotations_by_page.setdefault(entry["page"], []).append(entry)
        self.link_page = None
        self.page_links.clear()
        if self.inline_editor: self.inline_editor.detach()
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
            self.apply_fit(number)
        if offset is not None:
            self.centerOn(self.items_by_page[number].pos() + offset)
        else:
            self.verticalScrollBar().setValue(0)
        self.rebuilding = False
        self.pageChanged.emit(number)
        if self.inline_editor: self.inline_editor.position()

    def text_page(self, number):
        key = self.document.revision, number
        if key not in self.text_cache:
            self.text_cache[key] = TextPage(self.document.doc[number])
            while len(self.text_cache) > 24:
                self.text_cache.popitem(last=False)
        self.text_cache.move_to_end(key)
        return self.text_cache[key]

    def begin_inline_text(self,number,point):
        if not self.finish_inline_text(): return
        try: self.document.require_permission()
        except Exception as exc: self.message.emit(str(exc)); return
        from .inline_text import InlineTextEditor
        page=self.document.doc[number]
        point=fitz.Point(point)*page.rotation_matrix
        point.x=max(0,min(point.x,page.rect.width-min(144,page.rect.width)))
        point.y=max(0,min(point.y,page.rect.height-30))
        self.inlineStarted.emit()
        self.inline_editor=InlineTextEditor(self,number,tuple(point))

    def begin_inline_edit(self,number,region):
        if len(region.text)>25000:
            self.message.emit("这段文字过长，请拖框选择较小的范围编辑。"); return
        if not self.finish_inline_text(): return
        try: self.document.require_permission()
        except Exception as exc: self.message.emit(str(exc)); return
        from .inline_text import InlineTextEditor
        self.inlineStarted.emit(); self.clear_selection()
        self.inline_editor=InlineTextEditor(self,number,(0,0),region=region)
        self.viewport().update()

    def finish_inline_text(self,cancel=False):
        editor=self.inline_editor
        if editor is None: return True
        if not cancel: QApplication.inputMethod().commit()
        if cancel or (not editor.region and not editor.input.toPlainText().strip()) or (editor.region and editor.input.toPlainText()==editor.original_text and not editor.size_modified and not editor.color_modified and editor.fontname=="native" and editor.point==editor.original_point):
            self.dismiss_inline_text(); return True
        editor.reflow()  # Revalidate after IME commits or font acquisition.
        try: payload=editor.payload()
        except ValueError as exc: editor.show_error(str(exc)); return False
        self.textInsertRequested.emit(editor.number,payload)
        return self.inline_editor is None

    def dismiss_inline_text(self):
        editor,self.inline_editor=self.inline_editor,None
        if editor:
            editor.dispose()
            self.inlineFinished.emit()
        self.viewport().update()

    def set_mode(self, mode):
        if mode in ("highlight", "underline", "strike"):
            mode = "select"
        self.mode = mode
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if mode == "hand" else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if mode == "hand" else Qt.CursorShape.IBeamCursor if mode in TEXT_TOOLS else Qt.CursorShape.CrossCursor)

    def set_zoom(self, value, manual=True, anchor=None):
        if not math.isfinite(value) or (not manual and value <= 0):
            return
        anchor = anchor or self.viewport().rect().center()
        before = self.mapToScene(anchor)
        self.zoom = max(.15, min(5, value)) if manual else value
        if manual:
            self.fit_mode = None
            self.resize_timer.stop()
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        display_zoom = self.zoom * self.points_to_pixels
        self.setTransform(QTransform.fromScale(display_zoom, display_zoom))
        center = self.viewport().rect().center()
        self.centerOn(before + QPointF((center.x() - anchor.x()) / display_zoom, (center.y() - anchor.y()) / display_zoom))
        self.zoomChanged.emit(self.zoom)
        self.viewport().update()
        if self.inline_editor: self.inline_editor.position()

    def fit_width(self):
        self.fit_mode = "width"
        self.apply_fit()

    def fit_page(self):
        self.fit_mode = "page"
        if self.items_by_page:
            self.goto(self.current_page)

    def apply_fit(self, number=None):
        if not self.fit_mode or not self.items_by_page or self.fitting:
            return
        item = self.items_by_page[self.current_page if number is None else number]
        viewport = self.viewport()
        if self.fit_mode == "width":
            scale = max(1, viewport.width() - 4) / (item.size.width() + 64)
        else:
            scale = min(max(1, viewport.width() - 64) / item.size.width(),
                        max(1, viewport.height() - 48) / item.size.height())
        # All page centers share one scene X even in mixed-size documents.
        # Preserve the reading position while scroll bars react to the transform.
        center = self.mapToScene(viewport.rect().center())
        center.setX(item.pos().x() + item.size.width() / 2)
        if self.fit_mode == "page" and viewport.size() != self.last_fit_viewport:
            center = item.mapToScene(item.size.center())
        self.last_fit_viewport = viewport.size()
        self.fitting = True
        try:
            # QGraphicsView uses the platform scrollbar extent internally, which
            # can exceed the styled bar's actual width by several pixels.
            policy = (Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self.sceneRect().width() * scale <= viewport.width()
                      else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if policy != self.horizontalScrollBarPolicy():
                self.setHorizontalScrollBarPolicy(policy)
            if not math.isclose(scale, self.transform().m11(), rel_tol=1e-8):
                self.set_zoom(scale / self.points_to_pixels, manual=False)
            self.centerOn(center)
        finally:
            self.fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.inline_editor: self.inline_editor.position()
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
        if self.inline_editor: self.inline_editor.position()
        if not self.items_by_page or self.rebuilding or self.fitting:
            return
        y = self.mapToScene(self.viewport().rect().center()).y()
        # The midpoint of the 24-point gutter separates adjacent pages.
        number = max(0, bisect_right(self.items_by_page, y + 12, key=lambda item: item.pos().y()) - 1)
        if number != self.current_page:
            old_size = self.items_by_page[self.current_page].size.size()
            new_size = self.items_by_page[number].size.size()
            self.current_page = number
            self.pageChanged.emit(number)
            if self.fit_mode and old_size != new_size:
                self.resize_timer.start(70)

    def goto(self, page, rect=None, *, destination=None):
        if not self.items_by_page:
            return
        page = max(0, min(int(page), len(self.items_by_page) - 1))
        item = self.items_by_page[page]
        if self.fit_mode:
            self.apply_fit(page)
        if destination is not None:
            # MuPDF resolves PDF destinations in the target's rotated display space.
            x, y = destination
            x = x if math.isfinite(x) else 0
            y = y if math.isfinite(y) else 0
            target = self.mapFromScene(item.mapToScene(QPointF(x, y)))
            if target.x() < 0 or target.x() >= self.viewport().width():
                hbar = self.horizontalScrollBar()
                hbar.setValue(hbar.value() + target.x() - 24)
            vbar = self.verticalScrollBar()
            vbar.setValue(vbar.value() + target.y() - 24)
        elif rect is not None:
            r = fitz.Rect(rect) * self.document.doc[page].rotation_matrix
            self.centerOn(item.mapToScene(QPointF((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)))
        elif self.fit_mode == "page":
            self.centerOn(item.mapToScene(item.size.center()))
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
        if self.inline_editor:
            proxy=self.inline_editor.proxy
            if proxy.contains(proxy.mapFromScene(self.mapToScene(event.position().toPoint()))):
                return super().mousePressEvent(event)
        if self.inline_editor and not self.finish_inline_text():
            event.accept(); return
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
                if not self.text_page(number).glyphs and not self.link_at(number, point):
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
            self.selection = None if self.mode in ("comment", "ink") else (number, tuple(fitz.Rect(point, point)))
        self.viewport().update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.inline_editor and self.scene_obj.mouseGrabberItem() is self.inline_editor.proxy:
            return super().mouseMoveEvent(event)
        if (self.mode == "select" and self.press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self.press_pos).manhattanLength() >= QApplication.startDragDistance()):
            self.point_dragged = True
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
            if self.mode == "ink":
                self.ink.append(tuple(point))
            else:
                self.selection = (self.drag_page, tuple(fitz.Rect(self.drag_start, point).normalize()))
            self.viewport().update()
            event.accept()
        else:
            if self.mode == "select":
                hit = self.locate(event.position().toPoint())
                cursor = Qt.CursorShape.ArrowCursor
                if hit and self.link_at(*hit):
                    cursor = Qt.CursorShape.PointingHandCursor
                elif hit and self.text_page(hit[0]).caret(hit[1], near=True) is not None:
                    cursor = Qt.CursorShape.IBeamCursor
                self.viewport().setCursor(cursor)
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
            if not self.text_boxes and not event.modifiers():
                self.activate_at(event.position().toPoint())
            event.accept()
            return
        if self.drag_page is None:
            if self.mode == "select" and not event.modifiers():
                self.activate_at(event.position().toPoint())
            return super().mouseReleaseEvent(event)
        number, point, selection, ink = self.drag_page, self.drag_start, self.selection, self.ink[:]
        self.drag_page = self.drag_start = None
        self.ink = []
        if self.mode == "ink":
            self.selection = None
            if len(ink) > 1:
                self.inkChosen.emit(number, [ink])
        elif self.mode == "text":
            self.selection = None
            self.begin_inline_text(number,point)
        elif self.mode == "comment":
            self.selection = None
            moved = (event.position().toPoint() - self.press_pos).manhattanLength() if self.press_pos else 0
            if not self.point_dragged and moved < QApplication.startDragDistance():
                self.regionChosen.emit(self.mode, number, tuple(fitz.Rect(point.x, point.y, point.x + 18, point.y + 18)))
            else:
                self.message.emit("便笺只需单击。文字注释请先划词，再点注释。")
        elif self.mode=="signature" and (not selection or fitz.Rect(selection[1]).width<6):
            p=self.document.doc[number]
            display=point*p.rotation_matrix
            display.x=max(0,min(display.x,p.rect.width-180)); display.y=max(0,min(display.y,p.rect.height-60))
            self.regionChosen.emit(self.mode,number,tuple(fitz.Rect(display.x,display.y,display.x+180,display.y+60)*p.derotation_matrix))
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

    def link_at(self, number, point):
        if self.link_page != number:
            page = self.document.doc[number]
            links = []
            link = page.first_link
            while link:
                uri = link.uri or ""
                target = None
                if uri.startswith("#") and not link.is_external:
                    target = uri
                elif link.is_external:
                    url = QUrl(uri, QUrl.ParsingMode.StrictMode)
                    if url.isValid() and url.scheme() in ("http", "https") and url.host():
                        target = url
                if target is not None:
                    links.append((link.rect * page.derotation_matrix, target))
                link = link.next
            self.link_page, self.page_links = number, links
        return next((target for rect, target in self.page_links if rect.contains(point)), None)

    def activate_at(self, position):
        if self.press_pos is None or self.point_dragged or (position - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
            return
        hit = self.locate(position)
        if not hit:
            return
        for entry in reversed(self.annotations_by_page.get(hit[0], ())):
            boxes = entry.get("marked_boxes") or [entry["rect"]]
            if any(fitz.Rect(rect).contains(hit[1]) for rect in boxes):
                self.annotationActivated.emit(entry["page"], entry["xref"])
                return
        target = self.link_at(*hit)
        start = self.locate(self.press_pos)
        if not target or not start or start[0] != hit[0] or self.link_at(*start) != target:
            return
        if isinstance(target, QUrl):
            if not QDesktopServices.openUrl(target):
                self.message.emit("无法在系统浏览器中打开此网址。")
        else:
            page, x, y = self.document.doc.resolve_link(target)
            if 0 <= page < self.document.count:
                self.goto(page, destination=(x, y))
            else:
                self.message.emit("文档中的链接目标不可用。")

    def contextMenuEvent(self, event):
        if not self.text_boxes:
            return super().contextMenuEvent(event)
        menu = QMenu(self)
        copy = menu.addAction("复制  " + shortcut_text("Ctrl+C"), self.copy_selection)
        copy.setEnabled(bool(self.selected_text))
        for kind, name in (("highlight", "高亮"), ("underline", "下划线"), ("strike", "删除线")):
            menu.addAction(name, lambda checked=False, m=kind: self.markupChosen.emit(m, dict(self.text_boxes)))
        menu.addAction("注释所选文字", lambda: self.commentRequested.emit(dict(self.text_boxes), self.selected_text))
        menu.addSeparator()
        ai = menu.addAction("询问 AI", lambda: self.aiRequested.emit())
        ai.setEnabled(bool(self.selected_text))
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event):
        if self.inline_editor and self.scene_obj.focusItem() is self.inline_editor.proxy:
            return super().keyPressEvent(event)
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
