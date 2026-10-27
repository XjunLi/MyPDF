"""Document tabs and their independent reading / editing sessions."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import uuid

from PySide6.QtCore import QTimer, QSize, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QInputDialog, QLineEdit, QTabBar

from .engine import PdfDocument, PdfError, PasswordRequired
from .viewer import PdfView
from .icons import icon_button
from .chat_store import document_key


@dataclass(eq=False)
class DocumentSession:
    document: PdfDocument
    view: PdfView
    untitled_name: str
    key: str = field(default_factory=lambda: uuid.uuid4().hex)
    search_text: str = ""
    search_visible: bool = False
    search_results: list = field(default_factory=list)
    search_index: int = -1
    search_query: str = ""
    search_count: str = ""
    search_pending: bool = False
    ai_state: dict = field(default_factory=dict)
    center: object = None

    @property
    def name(self):
        return self.document.path.name if self.document.path else self.untitled_name


class DocumentTabs:
    def connect_view(self, view):
        # Hidden views can emit page/zoom signals while Qt adjusts the layout.
        # Only the active view may update shared controls or edit a document.
        def active(callback):
            return lambda *args: callback(*args) if self.view is view else None
        view.regionChosen.connect(active(self.region_chosen))
        view.markupChosen.connect(active(self.apply_markup))
        view.inkChosen.connect(active(lambda page, strokes: self.mutate(lambda: self.document.add_ink(page, strokes))))
        view.editRequested.connect(active(self.edit_at))
        view.pageChanged.connect(active(self.page_changed))
        view.zoomChanged.connect(active(self.zoom_changed))
        view.selectionChanged.connect(active(self.selection_changed))
        view.filesDropped.connect(self.open_files)
        view.aiRequested.connect(active(self.ask_selection))
        view.commentRequested.connect(active(self.comment_selection))
        view.annotationActivated.connect(active(self.focus_annotation))
        view.message.connect(active(lambda text: self.status_label.setText(text)))

    def session_at(self, index):
        key = self.document_tabs.tabData(index) if index >= 0 else None
        return next((s for s in self.sessions if s.key == key), None)

    def tab_changed(self, index):
        session = self.session_at(index)
        if session:
            self.activate_session(session)

    def refresh_tabs(self):
        for index in range(self.document_tabs.count()):
            session = self.session_at(index)
            if session:
                self.document_tabs.setTabText(index, ("● " if session.document.dirty else "") + session.name.replace("&", "&&"))
                self.document_tabs.setTabToolTip(index, str(session.document.path or session.name))
                button = self.document_tabs.tabButton(index, QTabBar.ButtonPosition.RightSide)
                button.setToolTip("关闭 " + session.name)
        visible = len(self.sessions) > 1
        if visible != (not self.document_tabs.isHidden()):
            self.document_tabs.setVisible(visible)

    def capture_session(self):
        session = self.active_session
        if not session:
            self.ai.take_state()
            return
        view = session.view
        session.center = view.mapToScene(view.viewport().rect().center())
        view.resize_timer.stop()
        view.scroll_timer.stop()
        # Rendered pages can be large at high DPI; keep document and selection,
        # but let inactive tabs release their disposable bitmap cache.
        view.cache.clear()
        session.search_text = self.search_input.text()
        session.search_visible = not self.search_bar.isHidden()
        session.search_results = list(self.search_results)
        session.search_index = self.search_index
        session.search_query = self.search_query
        session.search_count = self.search_count.text()
        session.search_pending = self.search_timer.isActive() or self.search_running
        session.ai_state = self.ai.take_state()

    def activate_session(self, session):
        if session is self.active_session:
            return
        self.capture_session()
        self.cancel_ocr()
        self.document_epoch += 1
        self.search_generation += 1
        self.search_timer.stop()
        self.search_running = False
        self.active_session = session
        self.document = session.document if session else self.empty_document
        self.view = session.view if session else self.empty_view
        self.selection_text = self.view.selected_text
        self.search_results = list(session.search_results) if session else []
        self.search_index = session.search_index if session else -1
        self.search_query = session.search_query if session else ""
        self.search_input.blockSignals(True)
        self.search_input.setText(session.search_text if session else "")
        self.search_input.blockSignals(False)
        self.search_count.setText(session.search_count if session else "")
        self.search_bar.setVisible(bool(session and session.search_visible))
        if session:
            self.ai.open_document(session.document.path, session.key, session.name, session.ai_state)
        else:
            self.ai.open_document()
        self.stack.setCurrentWidget(self.view if session else self.welcome)
        if session and not self.view.items_by_page:
            self.view.rebuild(False)
        elif session and session.center is not None:
            self.view.centerOn(session.center)
            self.view._page_at_center()
        self.rebuild_sidebar()
        self.update_comments()
        self.update_state()
        self.page_spin.lineEdit().setModified(False)
        self.page_changed(self.view.current_page)
        self.zoom_changed(self.view.zoom)
        self.mode_actions[self.view.mode].setChecked(True)
        if self.view.mode in ("text", "image"):
            self.insert_button.setDefaultAction(self.mode_actions[self.view.mode])
        self.ai.update_preview()
        self.status_label.setText("")
        if session:
            self.document_tabs.blockSignals(True)
            for index in range(self.document_tabs.count()):
                if self.session_at(index) is session:
                    self.document_tabs.setCurrentIndex(index)
                    break
            self.document_tabs.blockSignals(False)
            if session.search_pending and session.search_text.strip():
                # A cancelled batch must restart, not jump to its partial result.
                self.search_query = ""
                self.search_timer.start()
            self.schedule_auto_ocr()
            self.view.setFocus()

    def schedule_auto_ocr(self):
        if self.auto_ocr.isChecked() and self.document.count:
            epoch = self.document_epoch
            QTimer.singleShot(450, lambda: self.start_ocr(automatic=True)
                if epoch == self.document_epoch and self.auto_ocr.isChecked() else None)

    def add_document(self, document):
        if document.path is None:
            self.untitled_sequence += 1
        session = DocumentSession(document, PdfView(document), f"未命名 {self.untitled_sequence}.pdf")
        self.sessions.append(session)
        self.connect_view(session.view)
        self.stack.addWidget(session.view)
        self.document_tabs.blockSignals(True)
        index = self.document_tabs.addTab(session.name)
        self.document_tabs.setTabData(index, session.key)
        close_button = icon_button("close", "关闭 " + session.name)
        close_button.setObjectName("documentClose")
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.setIconSize(QSize(12, 12))
        close_button.setFixedSize(22, 22)
        close_button.clicked.connect(lambda checked=False, key=session.key: self.close_tab_key(key))
        self.document_tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, close_button)
        self.document_tabs.blockSignals(False)
        self.activate_session(session)
        return session

    @staticmethod
    def path_key(path):
        return os.path.normcase(str(Path(path).resolve()))

    def session_for_path(self, path):
        key = self.path_key(path)
        return next((s for s in self.sessions if s.document.path and self.path_key(s.document.path) == key), None)

    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "打开 PDF", "", "PDF (*.pdf)")
        self.open_files(paths)

    def open_files(self, paths):
        for path in paths:
            self.open_file(path)

    def open_file(self, path):
        existing = self.session_for_path(path)
        if existing:
            self.activate_session(existing)
            return True
        document = PdfDocument()
        try:
            password = ""
            while True:
                try:
                    document.open(path, password)
                    break
                except PasswordRequired:
                    password, ok = QInputDialog.getText(self, "PDF 密码", f"{Path(path).name}\n输入打开密码：", QLineEdit.EchoMode.Password)
                    if not ok:
                        document.close()
                        return False
            self.add_document(document)
            if document.doc.get_sigflags() > 0:
                QMessageBox.information(self, "文档含有签名字段", "修改并保存此文件可能使已有证书签名失效。建议另存副本。")
            return True
        except Exception as exc:
            if not any(s.document is document for s in self.sessions):
                document.close()
            self.error(exc)
            return False

    def new_document(self):
        document = PdfDocument()
        document.new()
        self.add_document(document)

    def cycle_document(self, delta):
        if self.document_tabs.count() > 1:
            self.document_tabs.setCurrentIndex((self.document_tabs.currentIndex() + delta) % self.document_tabs.count())

    def close_tab_key(self, key):
        for index in range(self.document_tabs.count()):
            if self.document_tabs.tabData(index) == key:
                self.close_document(index)
                break

    def confirm_unsaved(self, session=None):
        session = session or self.active_session
        if not session or not session.document.dirty:
            return True
        answer = QMessageBox.question(self, "保存更改", f"“{session.name}”有未保存的更改。要保存后关闭吗？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_session(session)
        return answer == QMessageBox.StandardButton.Discard

    def close_document(self, index=None):
        session = self.active_session if index is None else self.session_at(index)
        if not session:
            return False
        if session is self.active_session:
            # A modal Save dialog pumps events too. Stop OCR before asking so
            # it cannot dirty this document after the user chooses Save.
            self.document_epoch += 1
            self.cancel_ocr()
        if not self.confirm_unsaved(session):
            if session is self.active_session:
                self.schedule_auto_ocr()
            return False
        if session is self.active_session:
            self.capture_session()
        if not self.ai.persist_state(session.ai_state):
            if not self.ai.confirm_history_loss():
                return False
        if session is self.active_session:
            self.ai.conversation_id = session.ai_state.get("conversation_id")
            self.ai.revision = session.ai_state.get("revision", 0)
        index = next(i for i in range(self.document_tabs.count()) if self.session_at(i) is session)
        if session is self.active_session:
            replacement = self.session_at(index - 1) if index else self.session_at(index + 1)
            self.activate_session(replacement)
        self.document_tabs.blockSignals(True)
        self.document_tabs.removeTab(index)
        self.document_tabs.blockSignals(False)
        self.sessions.remove(session)
        self.stack.removeWidget(session.view)
        session.view.resize_timer.stop()
        session.view.scroll_timer.stop()
        session.view.deleteLater()
        session.document.close()
        self.refresh_tabs()
        self.update_state()
        return True

    def save(self, save_as=False):
        return self.save_session(self.active_session, save_as) if self.active_session else False

    def save_session(self, session, save_as=False):
        document = session.document
        path = document.path
        if save_as or path is None:
            chosen, _ = QFileDialog.getSaveFileName(self, "保存 PDF", str(path or session.name), "PDF (*.pdf)")
            if not chosen:
                return False
            path = Path(chosen)
            if path.suffix.lower() != ".pdf":
                path = path.with_suffix(".pdf")
        try:
            other = self.session_for_path(path)
            if other and other is not session:
                raise PdfError("该文件已在另一个标签中打开，请选择其他文件名。")
            document.save(path)
            if session is self.active_session:
                self.ai.rebind_document(document.path, session.name)
                session.ai_state = self.ai.snapshot()
            elif session.ai_state:
                state = session.ai_state
                if state.get("document_key") != document_key(document.path):
                    state.update(conversation_id=None, revision=0, document_key=document_key(document.path))
                state["document_name"] = session.name
                state["document_path"] = str(document.path)
                self.ai.persist_state(state)
            self.update_state()
            if session is self.active_session:
                self.status_label.setText("已保存并验证：" + str(path))
            return True
        except Exception as exc:
            self.error(exc)
            return False
