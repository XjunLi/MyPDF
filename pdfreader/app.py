from pathlib import Path
import json
import os
import sys

import pymupdf as fitz
from PySide6.QtCore import Qt, QSize, QTimer, QProcess, QTemporaryDir
from PySide6.QtGui import (QAction, QActionGroup, QIcon, QImage, QPixmap, QKeySequence,
    QPainter, QPageLayout)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QToolBar, QSplitter, QTabWidget, QStackedWidget,
    QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QLineEdit, QSpinBox,
    QComboBox, QCheckBox, QProgressBar, QFileDialog, QMessageBox, QInputDialog,
    QDialog, QDialogButtonBox, QFormLayout, QPlainTextEdit)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog

from . import __version__
from .engine import PdfDocument, PdfError, PasswordRequired, parse_page_range
from .viewer import PdfView
from .dialogs import TextDialog, SignatureDialog, CommentDialog
from .panels import AIPanel
from .terminal import TerminalPanel
from .config import Settings, plugins, validate_plugin
from .paths import data_root, resource_root, tessdata_path
from .style import STYLE, prepare_fonts
from .layout import ReaderLayout
from .documents import DocumentTabs


class MainWindow(DocumentTabs, ReaderLayout, QMainWindow):
    def __init__(self):
        super().__init__()
        self.document = PdfDocument()
        self.sessions = []
        self.active_session = None
        self.untitled_sequence = 0
        self.settings = Settings()
        self.selection_text = ""
        self.ocr_process = None
        self.ocr_temp = None
        self.ocr_buffer = ""
        self.ocr_state = None
        self.ocr_cancelled = False
        self.ocr_pending_open = None
        self.search_results = []
        self.search_index = -1
        self.thumb_done = set()
        self.thumb_queue = []
        self.edit_actions = []
        self.doc_actions = []
        self.document_epoch = 0
        self.search_generation = 0
        self.search_query = ""
        self.search_running = False
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(220)
        self.search_timer.timeout.connect(self.search)
        self.setWindowTitle("PDF Reader")
        self.resize(1380, 900)
        self.setMinimumSize(1000, 640)
        icon_path = resource_root() / "assets" / "reader.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._build_ui()
        self._build_menus()
        self.thumb_timer = QTimer(self)
        self.thumb_timer.setInterval(30)
        self.thumb_timer.timeout.connect(self.render_next_thumb)
        self.thumb_timer.start()
        self.ocr_timer = QTimer(self)
        self.ocr_timer.setInterval(350)
        self.ocr_timer.timeout.connect(self.poll_ocr_progress)
        self.update_plugins()
        self.update_state()

    def action(self, text, callback, shortcut=None, document=False, edit=False, checkable=False):
        a = QAction(text, self)
        a.setCheckable(checkable)
        a.triggered.connect(callback)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if document or edit:
            self.doc_actions.append(a)
        if edit:
            self.edit_actions.append(a)
        self.addAction(a)
        return a

    def _build_menus(self):
        file = self.root_menu.addMenu("文件")
        file.addAction(self.action("新建", self.new_document, "Ctrl+N"))
        file.addAction(self.open_action)
        file.addAction(self.save_action)
        file.addAction(self.action("另存为…", lambda: self.save(True), "Ctrl+Shift+S", document=True))
        file.addSeparator()
        file.addAction(self.action("导出指定页面…", self.export_pages, document=True))
        file.addAction(self.action("当前页导出为 PNG…", self.export_image, document=True))
        file.addAction(self.action("导出全文为 TXT…", self.export_text, document=True))
        file.addAction(self.action("打印…", self.print_document, "Ctrl+P", document=True))
        file.addAction(self.action("关闭当前文档", lambda: self.close_document(), "Ctrl+W", document=True))
        file.addSeparator()
        file.addAction(self.action("退出", self.close, "Alt+F4"))
        edit = self.root_menu.addMenu("编辑")
        edit.addAction(self.undo_action)
        edit.addAction(self.redo_action)
        edit.addAction(self.search_action)
        edit.addAction(self.action("填写当前页表单…", self.fill_form, document=True))
        page = self.root_menu.addMenu("页面")
        page.addAction(self.action("顺时针旋转 90°", lambda: self.mutate(lambda: self.document.rotate(self.view.current_page)), "Ctrl+R", edit=True))
        page.addAction(self.action("逆时针旋转 90°", lambda: self.mutate(lambda: self.document.rotate(self.view.current_page, -90)), edit=True))
        page.addAction(self.action("在后面插入空白页", lambda: self.mutate(lambda: self.document.insert_blank(self.view.current_page)), edit=True))
        page.addAction(self.action("合并其他 PDF…", self.merge_pdf, edit=True))
        page.addAction(self.action("移动当前页…", self.move_page, edit=True))
        page.addAction(self.action("删除当前页…", self.delete_page, edit=True))
        page.addSeparator()
        page.addMenu(self.ocr_menu)
        view = self.root_menu.addMenu("视图")
        self.reading_tools_menu = view.addMenu("阅读工具")
        self.reading_tools_menu.addAction(self.mode_actions["select"])
        self.reading_tools_menu.addAction(self.mode_actions["hand"])
        view.addSeparator()
        view.addAction(self.left_action)
        view.addAction(self.action("显示 / 隐藏侧栏", lambda: self.toggle_panel(None) if self.right.isVisible() else self.toggle_panel("ai"), "F9"))
        view.addAction(self.footer.actions()[-1])
        self.action("下一个文档", lambda: self.cycle_document(1), "Ctrl+Tab")
        self.action("上一个文档", lambda: self.cycle_document(-1), "Ctrl+Shift+Tab")
        extension = self.root_menu.addMenu("插件")
        extension.addAction(self.action("AI 连接设置…", self.ai.configure))
        extension.addAction(self.action("导入插件清单…", self.import_plugin))
        self.plugin_menu = extension.addMenu("AI 服务预设")
        help_menu = self.root_menu.addMenu("帮助")
        help_menu.addAction(self.action("使用说明与功能范围", self.show_help, "F1"))

    def set_mode(self, mode, hint):
        if mode in ("highlight", "underline", "strike"):
            self.markup_button.setDefaultAction(self.mode_actions[mode])
            if self.view.text_boxes:
                self.apply_markup(mode, dict(self.view.text_boxes))
            else:
                self.status_label.setText("请先选择需要标记的文字。")
            self.mode_actions["select"].setChecked(True)
            self.view.set_mode("select")
            return
        elif mode in ("text", "image"):
            self.insert_button.setDefaultAction(self.mode_actions[mode])
        elif mode == "comment" and self.view.text_boxes:
            self.comment_selection(dict(self.view.text_boxes), self.view.selected_text)
            self.mode_actions["select"].setChecked(True)
            self.view.set_mode("select")
            return
        self.mode_actions[mode].setChecked(True)
        self.view.set_mode(mode)
        self.status_label.setText(hint)

    def apply_markup(self, kind, groups):
        if groups:
            self.mutate(lambda: self.document.add_markup_ranges(groups, kind))

    def comment_selection(self, groups, quote):
        if not groups:
            return
        dialog = CommentDialog(self, quote=quote)
        if dialog.exec() and dialog.text.toPlainText().strip():
            self.mutate(lambda: self.document.add_markup_ranges(groups, content=dialog.text.toPlainText().strip()))
            self.show_panel("comment")

    def focus_annotation(self, page, xref):
        self.show_panel("comment")
        for n in range(self.comment_list.count()):
            item = self.comment_list.item(n)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry and entry["page"] == page and entry["xref"] == xref:
                self.comment_list.setCurrentItem(item)
                self.comment_list.scrollToItem(item)
                break

    def toggle_left(self):
        self.left.setVisible(not self.left.isVisible())
        self.left_action.setChecked(self.left.isVisible())
        self.queue_visible_thumbs()

    def show_panel(self, key):
        widget = {"ai": self.ai, "comment": self.comments, "terminal": self.terminal}[key]
        self.right.setCurrentWidget(widget)
        self.right.show()
        self.sync_panel_actions()

    def toggle_panel(self, key):
        widget = {"ai": self.ai, "comment": self.comments, "terminal": self.terminal}.get(key)
        if key is None or (self.right.isVisible() and self.right.currentWidget() is widget):
            self.right.hide()
            self.sync_panel_actions()
        else:
            self.show_panel(key)

    def sync_panel_actions(self, *_):
        for key, widget in [("ai", self.ai), ("comment", self.comments), ("terminal", self.terminal)]:
            self.panel_actions[key].setChecked(self.right.isVisible() and self.right.currentWidget() is widget)

    def ask_selection(self):
        self.show_panel("ai")
        if self.ai.attachment_kind != 3:
            self.ai.set_attachment(1)
        self.ai.update_preview()
        self.ai.input.setFocus()

    def open_ai_history(self, path, state):
        if self.open_file(path):
            self.ai.cancel()
            self.ai.restore_state(state)
            self.ai.flush()
            self.show_panel("ai")

    def show_search(self):
        self.search_bar.show()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def hide_search(self):
        self.search_timer.stop()
        self.search_generation += 1
        self.search_running = False
        self.search_bar.hide()
        self.view.search_boxes.clear()
        self.search_results.clear()
        self.search_query = ""
        self.search_index = -1
        self.search_count.clear()
        self.view.viewport().update()
        self.view.setFocus()

    def schedule_search(self):
        self.search_generation += 1
        self.search_timer.start()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")]
        if paths:
            event.acceptProposedAction()
            self.open_files(paths)

    def update_state(self):
        ready = self.document.count > 0
        for action in self.doc_actions:
            action.setEnabled(ready)
        self.undo_action.setEnabled(bool(self.document.undo_stack))
        self.redo_action.setEnabled(bool(self.document.redo_stack))
        self.page_spin.setEnabled(ready)
        self.zoom_combo.setEnabled(ready)
        self.search_input.setEnabled(ready)
        self.page_spin.setMaximum(max(1, self.document.count))
        self.page_total.setText(f" / {self.document.count} ")
        filename = self.active_session.name if self.active_session else "PDF Reader"
        self.setWindowTitle(f"{'● ' if self.document.dirty else ''}{filename} — PDF Reader" if ready else "PDF Reader")
        self.modified_label.setText("未保存" if self.document.dirty else "")
        self.previous_action.setEnabled(ready and self.view.current_page > 0)
        self.next_action.setEnabled(ready and self.view.current_page + 1 < self.document.count)
        self.update_selection_actions()
        self.refresh_tabs()

    def update_selection_actions(self):
        enabled = bool(self.document.count and self.view.text_boxes and self.document.doc.permissions & fitz.PDF_PERM_ANNOTATE)
        for kind in ("highlight", "underline", "strike"):
            self.mode_actions[kind].setEnabled(enabled)
        self.mode_actions["comment"].setText("注释所选文字" if self.view.text_boxes else "添加便笺")

    def error(self, exc):
        QMessageBox.warning(self, "操作未完成", str(exc))

    def after_change(self, keep_position=True):
        self.stack.setCurrentWidget(self.view)
        self.search_timer.stop()
        self.search_generation += 1
        self.search_running = False
        self.search_query = ""
        self.search_count.clear()
        self.view.search_boxes.clear()
        self.search_results.clear()
        self.search_index = -1
        self.view.rebuild(keep_position)
        self.rebuild_sidebar()
        self.update_comments()
        self.update_state()
        self.ai.update_preview()
        if self.search_bar.isVisible() and self.search_input.text().strip():
            self.search_timer.start()

    def mutate(self, function):
        if not self.document.count:
            return
        resume_ocr = (self.ocr_process is not None and self.auto_ocr.isChecked()
                      and (not self.ocr_cancelled or self.ocr_pending_open == self.document_epoch))
        self.cancel_ocr()
        try:
            function()
            self.after_change()
        except Exception as exc:
            self.after_change()
            self.error(exc)
        finally:
            if resume_ocr:
                if self.ocr_process is not None:
                    self.ocr_pending_open = self.document_epoch
                else:
                    self.start_ocr(automatic=True)

    def undo(self):
        self.mutate(self.document.undo)

    def redo(self):
        self.mutate(self.document.redo)

    def rebuild_sidebar(self):
        current = self.view.current_page
        scroll_value = self.thumbnails.verticalScrollBar().value()
        self.thumbnails.clear()
        self.thumb_done.clear()
        self.thumb_queue.clear()
        for n in range(self.document.count):
            item = QListWidgetItem(str(n + 1))
            item.setData(Qt.ItemDataRole.UserRole, n)
            item.setSizeHint(QSize(138, 172))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumbnails.addItem(item)
        self.thumbnails.setCurrentRow(current)
        self.thumbnails.verticalScrollBar().setValue(scroll_value)
        self.bookmarks.clear()
        parents = [self.bookmarks.invisibleRootItem()]
        for level, title, page in self.document.doc.get_toc() if self.document.count else []:
            while len(parents) > level:
                parents.pop()
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, max(0, page - 1))
            parents[-1].addChild(item)
            parents.append(item)
        self.bookmarks.expandToDepth(0)
        QTimer.singleShot(0, self.queue_visible_thumbs)

    def queue_visible_thumbs(self, *_):
        rect = self.thumbnails.viewport().rect()
        visible = [n for n in range(self.thumbnails.count()) if self.thumbnails.visualItemRect(self.thumbnails.item(n)).intersects(rect)]
        self.thumb_queue = [n for n in visible if n not in self.thumb_done]

    def render_next_thumb(self):
        if not self.thumb_queue or not self.document.count:
            return
        number = self.thumb_queue.pop(0)
        if number >= self.document.count:
            return
        p = self.document.doc[number]
        scale = min(108 / max(p.rect.width, 1), 144 / max(p.rect.height, 1))
        pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.thumbnails.item(number).setIcon(QIcon(QPixmap.fromImage(image)))
        self.thumb_done.add(number)

    def page_changed(self, number):
        # Do not replace a page number while the user is typing a destination.
        if not self.page_spin.lineEdit().isModified():
            self.page_spin.setValue(number + 1)
        self.thumbnails.setCurrentRow(number)
        self.previous_action.setEnabled(bool(self.document.count and number > 0))
        self.next_action.setEnabled(number + 1 < self.document.count)
        self.ai.update_preview()

    def commit_page(self):
        self.page_spin.lineEdit().setModified(False)
        self.view.goto(self.page_spin.value() - 1)

    def navigate_page(self, delta):
        self.page_spin.lineEdit().setModified(False)
        self.view.goto(self.view.current_page + delta)

    def zoom_changed(self, value):
        self.zoom_combo.setCurrentText(f"{value * 100:.0f}%")

    def change_zoom(self, *_):
        try:
            value = float(self.zoom_combo.currentText().replace("%", "")) / 100
            self.view.set_zoom(value)
        except ValueError:
            pass
        self.zoom_changed(self.view.zoom)

    def fit_width(self):
        self.view.fit_mode = True
        self.view.fit_width()

    def selection_changed(self, text):
        self.selection_text = text
        self.ai.update_preview()
        self.update_selection_actions()

    def ai_context(self, kind):
        if kind == 1:
            return self.selection_text
        if kind == 2 and self.document.count:
            return f"第 {self.view.current_page + 1} 页 · 图片"
        return ""

    def ai_page_image(self):
        if not self.document.count:
            return None
        page = self.document.doc[self.view.current_page]
        # Render just the PDF page, including edits and annotations. No desktop
        # capture, temporary image file, or access to any other document.
        scale = min(2, 1600 / max(page.rect.width, page.rect.height))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False, annots=True)
        return {"png": pixmap.tobytes("png"), "label": f"第 {self.view.current_page + 1} 页 · 图片"}

    def ai_document_info(self):
        return {"pages": self.document.count, "name": self.active_session.name if self.active_session else "",
                "source_state": self.document.state, "source_session": self.active_session.key if self.active_session else ""}

    def ai_document_context(self, mode):
        from .ai_context import build_document_context
        info = self.ai_document_info()
        return build_document_context(self.document, info["name"], info["source_session"], mode)

    def region_chosen(self, mode, page, rect):
        if mode in ("highlight", "underline", "strike"):
            self.mutate(lambda: self.document.add_markup(page, rect, mode))
        elif mode == "comment":
            dialog = CommentDialog(self)
            if dialog.exec() and dialog.text.toPlainText().strip():
                self.mutate(lambda: self.document.add_comment(page, fitz.Rect(rect).tl, dialog.text.toPlainText().strip()))
                self.show_panel("comment")
            self.view.clear_selection()
            self.set_mode("select", "拖动选择文字")
        elif mode in ("text", "edit"):
            old = self.document.selection_text(page, rect) if mode == "edit" else ""
            self.text_dialog(page, rect, old, mode == "edit")
        elif mode == "signature":
            dialog = SignatureDialog(self)
            if dialog.exec():
                p = self.document.doc[page]
                r = fitz.Rect(rect) * p.rotation_matrix
                strokes = [[tuple(fitz.Point(r.x0 + x * r.width, r.y0 + y * r.height) * p.derotation_matrix) for x, y in s] for s in dialog.pad.normalized()]
                self.mutate(lambda: self.document.add_ink(page, strokes, signature=True))
        elif mode == "image":
            filename, _ = QFileDialog.getOpenFileName(self, "插入图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
            if filename:
                self.mutate(lambda: self.document.add_image(page, rect, filename))

    def text_dialog(self, page, rect, text, editing):
        region = self.document.text_region(page, rect) if editing else None
        dialog = TextDialog(self, region.text if region else text, editing, region=region)
        if dialog.exec():
            if editing and dialog.preserve.isChecked():
                self.mutate(lambda: self.document.replace_text(page, region, dialog.text.toPlainText()))
            else:
                self.mutate(lambda: self.document.add_text(page, rect, dialog.text.toPlainText(), dialog.size.value(), editing,
                    dialog.raster_replace.isChecked(), color=dialog.rgb(), fontname=dialog.font.currentData()))

    def edit_at(self, page, point):
        block = self.document.text_block_at(page, point)
        if block:
            self.text_dialog(page, block[0], block[1], True)
        else:
            self.status_label.setText("这里没有文字块；扫描页请先 OCR，或使用「加文字」绘制文字框。")

    def update_comments(self):
        self.comment_list.clear()
        if not self.document.count:
            return
        labels = {"Highlight": "高亮", "Underline": "下划线", "StrikeOut": "删除线", "Text": "评论", "Ink": "笔迹"}
        for entry in self.document.annotations():
            title = entry["subject"] or labels.get(entry["type"], entry["type"])
            body = entry["text"] or "双击可添加备注"
            quote = f"「{entry['quote'][:140]}」\n" if entry.get("quote") else ""
            item = QListWidgetItem(f"第 {entry['page'] + 1} 页 · {title}\n{quote}{body[:250]}")
            item.setToolTip((quote + entry["text"]).strip())
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.comment_list.addItem(item)
        if not self.comment_list.count():
            item = QListWidgetItem("还没有批注\n使用高亮、评论或画笔记录你的想法。")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.comment_list.addItem(item)

    def select_comment(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry:
            self.view.goto(entry["page"], entry["rect"])

    def edit_comment(self, item=None):
        item = item if isinstance(item, QListWidgetItem) else self.comment_list.currentItem()
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry:
            dialog = CommentDialog(self, text=entry["text"], quote=entry.get("quote", ""), editing=True)
            if dialog.exec():
                self.mutate(lambda: self.document.edit_comment(entry["page"], entry["xref"], dialog.text.toPlainText()))

    def delete_comment(self):
        item = self.comment_list.currentItem()
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry:
            self.mutate(lambda: self.document.delete_annotation(entry["page"], entry["xref"]))

    def search(self):
        if not self.document.count:
            return
        self.search_timer.stop()
        query = self.search_input.text().strip()
        if query and query == self.search_query and self.search_results and not self.search_running:
            self.next_search(1)
            return
        self.search_generation += 1
        generation = self.search_generation
        self.search_query = query
        self.search_running = bool(query)
        self.search_results.clear()
        self.view.search_boxes.clear()
        self.search_index = -1
        self.search_count.clear()
        if not query:
            self.view.viewport().update()
            return
        def batch(first):
            if generation != self.search_generation:
                return
            last = min(first + 12, self.document.count)
            for number in range(first, last):
                boxes = self.document.doc[number].search_for(query)
                if boxes:
                    self.view.search_boxes[number] = [tuple(r) for r in boxes]
                    self.search_results.extend((number, tuple(r)) for r in boxes)
            if self.search_index < 0 and self.search_results:
                self.next_search(1)
            self.view.viewport().update()
            if last < self.document.count:
                self.search_count.setText(f"{len(self.search_results)} · 查找中")
                QTimer.singleShot(0, lambda: batch(last))
            else:
                self.search_running = False
                self.search_count.setText(f"{self.search_index + 1} / {len(self.search_results)}" if self.search_results else "没有结果")
        batch(0)

    def next_search(self, delta):
        if not self.search_results:
            self.status_label.setText("没有匹配结果；扫描页需要先完成 OCR。")
            return
        self.search_index = (self.search_index + delta) % len(self.search_results)
        number, rect = self.search_results[self.search_index]
        self.view.goto(number, rect)
        self.search_count.setText(f"{self.search_index + 1} / {len(self.search_results)}")
        self.status_label.setText(f"搜索结果 {self.search_index + 1} / {len(self.search_results)} · 第 {number + 1} 页")

    def delete_page(self):
        if QMessageBox.question(self, "删除页面", f"删除第 {self.view.current_page + 1} 页？保存前可撤销。") == QMessageBox.StandardButton.Yes:
            self.mutate(lambda: self.document.delete_page(self.view.current_page))

    def move_page(self):
        target, ok = QInputDialog.getInt(self, "移动页面", "移到第几页：", self.view.current_page + 1, 1, self.document.count)
        if ok:
            self.mutate(lambda: self.document.move_page(self.view.current_page, target - 1))
            self.view.goto(target - 1)

    def merge_pdf(self):
        filename, _ = QFileDialog.getOpenFileName(self, "在当前页后合并 PDF", "", "PDF (*.pdf)")
        if filename:
            password = ""
            try:
                other = fitz.open(filename)
                locked = other.needs_pass
                other.close()
                if locked:
                    password, ok = QInputDialog.getText(self, "密码", "合并文件的密码：", QLineEdit.EchoMode.Password)
                    if not ok:
                        return
                self.mutate(lambda: self.document.merge(filename, self.view.current_page, password))
            except Exception as exc:
                self.error(exc)

    def export_pages(self):
        value, ok = QInputDialog.getText(self, "导出页面", "页码范围，例如 1-3,5：", text=f"1-{self.document.count}")
        if not ok:
            return
        try:
            pages = parse_page_range(value, self.document.count)
            path, _ = QFileDialog.getSaveFileName(self, "导出页面", "导出页面.pdf", "PDF (*.pdf)")
            if path:
                self.document.export_pages(pages, path)
                self.status_label.setText(f"已导出 {len(pages)} 页：{path}")
        except Exception as exc:
            self.error(exc)

    def export_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出当前页", f"page-{self.view.current_page + 1}.png", "PNG (*.png)")
        if path:
            try:
                self.document.doc[self.view.current_page].get_pixmap(dpi=180, alpha=False).save(path)
                self.status_label.setText("已导出 PNG：" + path)
            except Exception as exc:
                self.error(exc)

    def export_text(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出文字", "文档文字.txt", "文本 (*.txt)")
        if path:
            try:
                content = "\n\n".join(f"--- 第 {n + 1} 页 ---\n{self.document.page_text(n)}" for n in range(self.document.count))
                Path(path).write_text(content, encoding="utf-8")
                self.status_label.setText("已导出文字：" + path)
            except Exception as exc:
                self.error(exc)

    def toggle_auto_ocr(self, enabled):
        self.settings.values["auto_ocr"] = enabled
        try:
            self.settings.save()
        except Exception as exc:
            self.error(exc)
        if enabled and self.document.count:
            self.start_ocr(automatic=True)
        elif not enabled:
            self.cancel_ocr()

    def start_ocr(self, pages=None, automatic=False):
        if not self.document.count or (automatic and not self.auto_ocr.isChecked()):
            return
        if self.ocr_process is not None:
            if automatic:
                self.ocr_pending_open = self.document_epoch
            else:
                self.status_label.setText("OCR 正在进行，请等待或取消当前任务。")
            return
        try:
            self.document.require_permission()
            pages = self.document.scan_candidates() if pages is None else [p for p in pages if len(self.document.doc[p].get_text().strip()) < 4]
            if not pages:
                if not automatic:
                    self.status_label.setText("没有需要识别的页面，现有文字层无需重复 OCR。")
                return
            language = self.language.currentData()
            for code in language.split("+"):
                if not (tessdata_path() / f"{code}.traineddata").exists():
                    raise PdfError(f"缺少离线 OCR 模型：{code}.traineddata")
            self.settings.values["language"] = language
            self.settings.save()
            self.ocr_temp = QTemporaryDir(str(data_root() / "ocr-XXXXXX"))
            if not self.ocr_temp.isValid():
                raise PdfError("无法创建 OCR 临时目录。")
            directory = Path(self.ocr_temp.path())
            # A decrypted, private temporary snapshot is needed by the worker.
            (directory / "input.pdf").write_bytes(self.document.doc.tobytes())
            self.ocr_state = (self.document_epoch, self.document.state)
            self.ocr_buffer = ""
            self.ocr_cancelled = False
            self.ocr_process = QProcess(self)
            args = ["--ocr-worker", str(directory / "input.pdf"), str(directory / "layers"), json.dumps(pages), language]
            if not getattr(sys, "frozen", False):
                args.insert(0, str(resource_root() / "main.py"))
            self.ocr_process.setProgram(sys.executable)
            self.ocr_process.setArguments(args)
            self.ocr_process.readyReadStandardOutput.connect(self.ocr_output)
            self.ocr_process.finished.connect(self.ocr_finished)
            self.ocr_process.errorOccurred.connect(self.ocr_error)
            self.ocr_progress.setRange(0, len(pages))
            self.ocr_progress.setValue(0)
            self.ocr_progress_action.setVisible(True)
            self.ocr_stop_action.setVisible(True)
            self.status_label.setText(f"本地 OCR · 正在识别 {len(pages)} 页，阅读可继续")
            self.ocr_process.start()
            self.ocr_timer.start()
        except Exception as exc:
            if not automatic:
                self.error(exc)
            else:
                self.status_label.setText("自动 OCR 未启动：" + str(exc))

    def ocr_output(self):
        if self.ocr_process is None:
            return
        self.ocr_buffer += bytes(self.ocr_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self.ocr_buffer:
            line, self.ocr_buffer = self.ocr_buffer.split("\n", 1)
            try:
                progress = json.loads(line)
                if self.ocr_progress.value() != progress["done"]:
                    self.ocr_progress.setValue(progress["done"])
                    self.status_label.setText(f"本地 OCR · 已识别 {progress['done']}/{progress['total']} 页")
            except (ValueError, KeyError):
                continue

    def poll_ocr_progress(self):
        if not self.ocr_temp:
            return
        try:
            progress = json.loads((Path(self.ocr_temp.path()) / "layers" / "progress.json").read_text(encoding="utf-8"))
            if self.ocr_progress.value() != progress["done"]:
                self.ocr_progress.setValue(progress["done"])
                self.status_label.setText(f"本地 OCR · 已识别 {progress['done']}/{progress['total']} 页")
        except (OSError, ValueError, KeyError):
            pass

    def ocr_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.ocr_finished(1)

    def ocr_finished(self, exit_code, *_):
        if self.ocr_process is None:
            return
        process, self.ocr_process = self.ocr_process, None
        self.ocr_timer.stop()
        self.ocr_progress_action.setVisible(False)
        self.ocr_stop_action.setVisible(False)
        try:
            if self.ocr_cancelled:
                if self.ocr_state and self.ocr_state[0] == self.document_epoch:
                    self.status_label.setText("OCR 已取消，可随时重新识别。")
                return
            if exit_code:
                detail = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
                error_path = Path(self.ocr_temp.path()) / "layers" / "error.txt"
                if error_path.exists():
                    detail = error_path.read_text(encoding="utf-8")
                raise PdfError("OCR 未完成：" + (detail[-1400:] or process.errorString()))
            if self.ocr_state != (self.document_epoch, self.document.state):
                self.status_label.setText("文档已变化，本次 OCR 结果未应用。请重新识别。")
                return
            result = json.loads((Path(self.ocr_temp.path()) / "layers" / "result.json").read_text(encoding="utf-8"))
            if result:
                self.document.apply_ocr(result)
                self.after_change()
                self.status_label.setText(f"OCR 完成 · {len(result)} 页已添加可搜索文字层，请保存文档")
            else:
                self.status_label.setText("OCR 完成，但没有识别到文字。")
        except Exception as exc:
            self.error(exc)
        finally:
            process.deleteLater()
            if self.ocr_temp:
                self.ocr_temp.remove()
                self.ocr_temp = None
            if self.ocr_pending_open == self.document_epoch and self.auto_ocr.isChecked():
                QTimer.singleShot(0, lambda: self.start_ocr(automatic=True))
            self.ocr_pending_open = None

    def cancel_ocr(self):
        self.ocr_pending_open = None
        if self.ocr_process:
            self.ocr_cancelled = True
            self.ocr_process.kill()

    def fill_form(self):
        page = self.document.doc[self.view.current_page]
        widgets = list(page.widgets() or [])
        if not widgets:
            QMessageBox.information(self, "表单", "当前页没有交互表单字段。普通扫描表格可用「加文字」填写。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("填写当前页表单")
        dialog.resize(460, 360)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        fields = []
        for w in widgets:
            if w.field_flags & 1 or w.field_type in (fitz.PDF_WIDGET_TYPE_SIGNATURE, fitz.PDF_WIDGET_TYPE_BUTTON, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                continue
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                control = QCheckBox()
                control.setChecked(w.field_value not in ("Off", "", None))
                fields.append((w.xref, control, w.on_state()))
            elif w.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
                control = QComboBox()
                control.addItems(w.choice_values or [])
                control.setCurrentText(str(w.field_value or ""))
                fields.append((w.xref, control, None))
            else:
                control = QLineEdit(str(w.field_value or ""))
                fields.append((w.xref, control, None))
            form.addRow(w.field_label or w.field_name, control)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            def fill():
                with self.document.change("填写表单", annotation=True):
                    for xref, control, on in fields:
                        p = self.document.doc[self.view.current_page]
                        w = p.load_widget(xref)
                        w.field_value = (on if control.isChecked() else "Off") if isinstance(control, QCheckBox) else control.currentText() if isinstance(control, QComboBox) else control.text()
                        w.update()
            self.mutate(fill)

    def print_document(self):
        if not (self.document.doc.permissions & fitz.PDF_PERM_PRINT):
            return self.error(PdfError("此 PDF 禁止打印。"))
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setResolution(150)
        dialog = QPrintDialog(printer, self)
        dialog.setMinMax(1, self.document.count)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        painter = QPainter()
        if not painter.begin(printer):
            return self.error(PdfError("无法连接打印机。"))
        try:
            first = max(1, printer.fromPage()) - 1
            last = printer.toPage() or self.document.count
            for n in range(first, last):
                if n > first:
                    printer.newPage()
                p = self.document.doc[n]
                pix = p.get_pixmap(dpi=150, colorspace=fitz.csRGB, alpha=False)
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                rect = printer.pageRect(QPrinter.Unit.DevicePixel).toRect()
                size = image.size().scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
                rect.setSize(size)
                painter.drawImage(rect, image)
        finally:
            painter.end()

    def import_plugin(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入本地插件清单", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = validate_plugin(json.loads(Path(path).read_text(encoding="utf-8")))
            directory = data_root() / "plugins"
            directory.mkdir(exist_ok=True)
            import uuid
            (directory / f"{uuid.uuid4().hex}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.update_plugins()
            self.status_label.setText("已导入插件：" + data["name"] + "；可在终端列表或 AI 服务预设中选择。")
        except Exception as exc:
            self.error(exc)

    def update_plugins(self):
        entries = plugins()
        self.terminal.add_plugins(entries)
        self.plugin_menu.clear()
        for entry in entries:
            if entry["kind"] == "ai":
                self.plugin_menu.addAction(self.action(entry["name"], lambda checked=False, e=entry: self.apply_ai_plugin(e)))
        self.plugin_menu.setEnabled(not self.plugin_menu.isEmpty())

    def apply_ai_plugin(self, entry):
        self.settings.values["ai"] = {"endpoint": entry["endpoint"], "model": entry["model"], "enabled": False}
        self.ai.update_connection()
        self.ai.configure()

    def show_help(self):
        text = ("PDF Reader " + __version__ + "\n\n"
                "多文档：可一次打开或拖入多份 PDF；两份起在底部左侧显示标签，支持拖动排序。Ctrl+Tab 切换，Ctrl+W 关闭当前文档。各自保留位置、缩放、撤销和 AI 会话。\n\n"
                "阅读：默认选择文字，滚轮连续滚动，Ctrl+滚轮缩放；抓手在菜单“视图 → 阅读工具”中。左侧缩略图与目录跳页；Ctrl+F 搜索。\n\n"
                "批注：先划词，再点高亮或注释。没有选区时，注释工具单击页面放置便笺；右侧双击可修改备注。\n\n"
                "编辑：加文字/插图先拖框；改文字点击文字行或拖框。默认保留原字体、字号和颜色；无法匹配原字体时会提示。复杂排版不会自动重排。\n\n"
                "签名：拖出放置框后手写。保存的是可见笔迹，不是证书数字签名。\n\n"
                "OCR：内置中英识别，当前文档的扫描页默认自动识别，结果需保存。切换标签会停止当前识别，切回后自动重新识别未完成的页面。\n\n"
                "AI：在侧栏右上角 … 配置连接。回复支持 Markdown。新对话默认整份 PDF 背景 + 当前页截图 + 选区，只在点击发送时请求服务。每轮携带全文和最近对话；PDF 不受支持时可在设置中改用全文文字。输入框 ＋ 可调整范围，点标签预览；旧的单页截图不重发。\n\n"
                "聊天记录：SQLite 自动保存到本机，Windows 当前用户加密；重新打开同一 PDF 可继续。侧栏时钟查看历史，… 新建、导出或删除；所有本机记录包含已关闭及未保存 PDF 的对话。密钥不写入聊天数据库。联网服务的数据政策由服务提供方决定。\n\n"
                "终端：点击启动运行 PowerShell 或插件命令。Ctrl+C 中断，Ctrl+V 粘贴；有选区时 Ctrl+C 复制。终端程序可自行联网。\n\n"
                "范围：本版本提供主要阅读与编辑流程。暂不包含证书签章、PDF/A 校验、XFA、专业印前处理及 Word 排版转换。")
        QMessageBox.information(self, "使用说明", text)

    def closeEvent(self, event):
        # Collect decisions before closing any tab, so Cancel keeps the entire
        # workspace open even when an earlier document was marked Discard.
        self.document_epoch += 1
        self.cancel_ocr()
        for index in range(self.document_tabs.count()):
            if not self.confirm_unsaved(self.session_at(index)):
                self.schedule_auto_ocr()
                event.ignore()
                return
        self.capture_session()
        results = [self.ai.persist_state(s.ai_state) for s in self.sessions if s is not self.active_session]
        results.append(self.ai.flush())
        if not all(results) and not self.ai.confirm_history_loss():
            self.schedule_auto_ocr()
            event.ignore()
            return
        self.cancel_ocr()
        self.search_generation += 1
        self.search_timer.stop()
        self.thumb_timer.stop()
        if self.ocr_process:
            self.ocr_process.waitForFinished(2000)
        self.ai.shutdown()
        self.terminal.stop()
        for session in self.sessions:
            session.view.resize_timer.stop()
            session.view.scroll_timer.stop()
            session.document.close()
        self.empty_document.close()
        event.accept()


def run(filenames=None):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PDF Reader")
    app.setOrganizationName("PDF Reader")
    prepare_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    if filenames:
        paths = [filenames] if isinstance(filenames, (str, Path)) else filenames
        QTimer.singleShot(0, lambda: window.open_files(paths))
    return app.exec()
