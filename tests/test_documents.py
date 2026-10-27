from pathlib import Path
import pytest
import pymupdf as fitz
from PySide6.QtCore import Qt, QMimeData, QUrl, QPointF, QPoint
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolButton, QLabel, QTabBar, QInputDialog

from pdfreader.app import MainWindow
from pdfreader.style import STYLE


@pytest.fixture
def reader(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    window = MainWindow()
    window.setStyleSheet(STYLE)
    window.auto_ocr.setChecked(False)
    window.show()
    window.open_file(pdf_path)
    qapp.processEvents()
    yield window
    window.ocr_process = None
    window.document_epoch += 1
    window.auto_ocr.blockSignals(True)
    window.auto_ocr.setChecked(False)
    window.auto_ocr.blockSignals(False)
    for session in window.sessions:
        session.document.saved_state = session.document.state
    window.close()
    qapp.processEvents()


def another_pdf(pdf_path, tmp_path, name="second.pdf"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_path.read_bytes())
    return path


def test_tabs_only_appear_for_multiple_documents_and_preserve_reading(reader, pdf_path, tmp_path, qapp):
    first = reader.active_session
    assert reader.document_tabs.isHidden()
    reader.view.set_zoom(1.75)
    reader.view.goto(1)
    qapp.processEvents()
    center = reader.view.mapToScene(reader.view.viewport().rect().center())
    reader.open_file(another_pdf(pdf_path, tmp_path))
    second = reader.active_session
    assert first is not second and reader.document is not first.document
    qapp.processEvents()
    assert reader.document_tabs.isVisible()
    tabs_rect = reader.document_tabs.rect().translated(reader.document_tabs.mapTo(reader.footer, QPoint(0, 0)))
    assert reader.footer.rect().contains(tabs_rect)
    assert tabs_rect.right() < reader.footer.widgetForAction(reader.previous_action).x()
    assert reader.status_label.isHidden()
    reader.view.set_zoom(.85)
    reader.view.goto(0)
    reader.document_tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert reader.document is first.document and reader.view is first.view
    assert reader.view.zoom == 1.75 and reader.view.current_page == 1
    assert reader.page_spin.value() == 2 and reader.zoom_combo.currentText() == "175%"
    now = reader.view.mapToScene(reader.view.viewport().rect().center())
    assert abs(now.y() - center.y()) < 3
    reader.document_tabs.setCurrentIndex(1)
    assert reader.view.zoom == .85 and reader.view.current_page == 0
    assert reader.close_document()
    assert reader.document is first.document and reader.document_tabs.isHidden()


def test_each_document_keeps_edits_undo_and_text_selection(reader, pdf_path, tmp_path):
    first = reader.active_session
    reader.mutate(lambda: reader.document.add_comment(0, (100, 210), "first note"))
    reader.view.selected_text = "reading"
    reader.view.text_boxes = {0: [tuple(reader.document.doc[0].search_for("reading")[0])]}
    reader.selection_changed("reading")
    reader.open_file(another_pdf(pdf_path, tmp_path))
    second = reader.active_session
    assert first.document.dirty and not second.document.dirty
    assert reader.document_tabs.tabText(0).startswith("● ")
    assert not reader.mode_actions["highlight"].isEnabled()
    reader.mutate(lambda: reader.document.add_comment(1, (50, 140), "second note"))
    reader.activate_session(first)
    assert reader.selection_text == "reading"
    assert reader.mode_actions["highlight"].isEnabled()
    reader.undo()
    assert not first.document.annotations() and not first.document.dirty
    assert second.document.annotations()[0]["text"] == "second note"
    reader.activate_session(second)
    assert reader.document.annotations()[0]["page"] == 1
    reader.undo()
    assert not second.document.dirty
    reader.activate_session(first)
    reader.redo()
    assert first.document.annotations()[0]["text"] == "first note"


def test_reopening_same_path_focuses_existing_unsaved_tab(reader, pdf_path, tmp_path):
    first = reader.active_session
    reader.mutate(lambda: reader.document.add_comment(0, (100, 200), "keep this"))
    reader.open_file(another_pdf(pdf_path, tmp_path))
    assert reader.open_file(pdf_path)
    assert reader.active_session is first and len(reader.sessions) == 2
    assert reader.document.annotations()[0]["text"] == "keep this"
    assert reader.open_file(str(pdf_path).upper())
    assert len(reader.sessions) == 2


def test_failed_or_cancelled_open_keeps_current_document(reader, tmp_path, monkeypatch):
    active = reader.active_session
    errors = []
    monkeypatch.setattr(reader, "error", lambda exc: errors.append(str(exc)))
    assert not reader.open_file(tmp_path / "missing.pdf")
    assert reader.active_session is active and len(reader.sessions) == 1 and errors
    encrypted = tmp_path / "locked.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="password", owner_pw="owner")
    doc.close()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))
    assert not reader.open_file(encrypted)
    assert reader.active_session is active and reader.document.count == 2


@pytest.mark.parametrize("way", ["file_dialog", "view_drop", "window_drop"])
def test_open_multiple_files_and_deduplicate(reader, pdf_path, tmp_path, monkeypatch, way):
    paths = [pdf_path, another_pdf(pdf_path, tmp_path), another_pdf(pdf_path, tmp_path, "third.pdf")]
    if way == "file_dialog":
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(p) for p in paths], "PDF"))
        reader.open_dialog()
    else:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths] + [QUrl("https://example.com/ignored.pdf")])
        event = QDropEvent(QPointF(100, 100), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        (reader.view if way == "view_drop" else reader).dropEvent(event)
        assert event.isAccepted()
    assert len(reader.sessions) == 3
    assert reader.document.path == paths[-1]


def test_closing_background_tab_saves_that_document_only(reader, pdf_path, tmp_path, monkeypatch):
    reader.mutate(lambda: reader.document.add_comment(0, (50, 200), "save the first"))
    reader.open_file(another_pdf(pdf_path, tmp_path))
    active = reader.active_session
    reader.mutate(lambda: reader.document.add_comment(0, (50, 220), "keep the second unsaved"))
    prompts = []
    def save_answer(*args):
        prompts.append(args[2])
        return QMessageBox.StandardButton.Save
    monkeypatch.setattr(QMessageBox, "question", save_answer)
    assert reader.close_document(0)
    assert "source.pdf" in prompts[0]
    assert reader.active_session is active and reader.document.dirty
    with fitz.open(pdf_path) as document:
        page = document[0]
        assert next(page.annots()).info["content"] == "save the first"
    with fitz.open(reader.document.path) as document:
        assert not list(document[0].annots() or [])


def test_cancel_tab_close_and_cancel_save_as_keep_unsaved_document(reader, monkeypatch):
    reader.new_document()
    active = reader.active_session
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    assert not reader.close_document()
    assert len(reader.sessions) == 2 and reader.active_session is active
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert not reader.close_document()
    assert len(reader.sessions) == 2 and reader.document.dirty


def test_cancel_window_close_keeps_all_tabs_even_after_discard(reader, monkeypatch):
    reader.mutate(lambda: reader.document.add_comment(0, (80, 200), "first unsaved"))
    reader.new_document()
    sessions = list(reader.sessions)
    answers = iter([QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: next(answers))
    assert not reader.close()
    assert reader.isVisible() and reader.sessions == sessions
    assert all(s.document.dirty and s.document.count for s in sessions)


def test_save_as_cannot_overwrite_another_open_tab(reader, pdf_path, tmp_path, monkeypatch):
    first_bytes = pdf_path.read_bytes()
    reader.new_document()
    active = reader.active_session
    errors = []
    monkeypatch.setattr(reader, "error", lambda exc: errors.append(str(exc)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(pdf_path), "PDF"))
    assert not reader.save(True)
    assert pdf_path.read_bytes() == first_bytes and active.document.path is None and errors
    new_path = tmp_path / "renamed.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new_path), "PDF"))
    assert reader.save(True)
    assert reader.document_tabs.tabText(1) == "renamed.pdf"
    assert reader.document_tabs.tabToolTip(1) == str(new_path)
    assert not active.document.dirty


def test_moving_tabs_keeps_identity_and_close_button_closes_right_file(reader, pdf_path, tmp_path, qapp):
    first = reader.active_session
    reader.open_file(another_pdf(pdf_path, tmp_path))
    second = reader.active_session
    reader.open_file(another_pdf(pdf_path, tmp_path, "third.pdf"))
    third = reader.active_session
    reader.document_tabs.moveTab(0, 2)
    assert reader.session_at(0) is second and reader.session_at(2) is first
    reader.cycle_document(1)
    assert reader.active_session is first
    reader.cycle_document(-1)
    assert reader.active_session is third
    qapp.processEvents()
    close_button = reader.document_tabs.tabButton(0, QTabBar.ButtonPosition.RightSide)
    QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
    assert second not in reader.sessions and reader.active_session is third


def test_last_tab_close_returns_to_welcome_and_can_open_again(reader, pdf_path, qapp):
    assert reader.close_document()
    qapp.processEvents()
    assert not reader.sessions and reader.document.count == 0
    assert reader.stack.currentWidget() is reader.welcome
    assert reader.document_tabs.isHidden()
    assert not reader.save_action.isEnabled()
    assert reader.page_total.text().strip() == "/ 0"
    reader.open_file(pdf_path)
    assert len(reader.sessions) == 1 and reader.document.count == 2


def test_ai_context_drafts_and_history_belong_to_their_document(reader, pdf_path, tmp_path):
    first = reader.active_session
    reader.ai.messages = [{"role": "user", "content": "Only first document"}]
    reader.ai.entries = [("user", "Only first document")]
    reader.ai.input.setPlainText("first draft")
    reader.ai.set_attachment(2)
    reader.open_file(another_pdf(pdf_path, tmp_path))
    second = reader.active_session
    assert not reader.ai.messages and not reader.ai.input.toPlainText()
    assert reader.ai.attachment_kind == 3 and not reader.ai.attachment_chip.isHidden()
    reader.ai.input.setPlainText("second draft")
    reader.activate_session(first)
    assert reader.ai.messages[0]["content"] == "Only first document"
    assert reader.ai.input.toPlainText() == "first draft" and reader.ai.attachment_kind == 2
    reader.activate_session(second)
    assert reader.ai.input.toPlainText() == "second draft" and not reader.ai.messages


def test_late_ai_stream_from_previous_tab_cannot_change_new_conversation(reader, pdf_path, tmp_path):
    class Job:
        cancelled = False
        def cancel(self):
            self.cancelled = True
    first = reader.active_session
    job = Job()
    reader.ai.job = job
    reader.ai.entries = [("user", "Question A"), ("assistant", "Partial A")]
    reader.ai.answer = "Partial A"
    generation = reader.ai.generation
    reader.open_file(another_pdf(pdf_path, tmp_path))
    assert job.cancelled
    reader.ai.on_chunk("Late A", generation)
    reader.ai.on_finished("Complete A", generation)
    assert not reader.ai.entries and not reader.ai.messages
    reader.activate_session(first)
    assert "Partial A" in reader.ai.entries[-1][1] and "已停止" in reader.ai.entries[-1][1]


def test_search_state_is_independent_and_does_not_move_page_on_switch(reader, pdf_path, tmp_path):
    first = reader.active_session
    reader.show_search()
    reader.search_input.setText("reading")
    reader.search()
    reader.view.goto(1)
    reader.open_file(another_pdf(pdf_path, tmp_path))
    assert reader.search_bar.isHidden() and not reader.search_results
    reader.show_search()
    reader.search_input.setText("comments")
    reader.search()
    reader.activate_session(first)
    assert not reader.search_bar.isHidden() and reader.search_input.text() == "reading"
    assert reader.search_query == "reading" and len(reader.search_results) == 1
    assert reader.view.current_page == 1


def test_switch_cancels_old_ocr_and_only_schedules_active_document(reader, pdf_path, tmp_path, monkeypatch):
    class Worker:
        killed = False
        def kill(self):
            self.killed = True
    worker = Worker()
    first = reader.active_session
    old_epoch = reader.document_epoch
    reader.auto_ocr.blockSignals(True)
    reader.auto_ocr.setChecked(True)
    reader.auto_ocr.blockSignals(False)
    reader.ocr_process = worker
    reader.ocr_state = (old_epoch, first.document.state)
    reader.open_file(another_pdf(pdf_path, tmp_path))
    assert worker.killed and reader.ocr_cancelled and reader.document_epoch != old_epoch
    calls = []
    monkeypatch.setattr(reader, "start_ocr", lambda *a, **k: calls.append(reader.active_session))
    reader.activate_session(first)
    QTest.qWait(490)
    assert calls == [first]


def test_toolbar_is_centered_without_brand_and_tabs_stay_usable_when_full(reader, pdf_path, tmp_path, qapp):
    reader.resize(1000, 700)
    for n in range(7):
        reader.open_file(another_pdf(pdf_path, tmp_path, f"{n} - a very long document filename for tab overflow.pdf"))
    qapp.processEvents()
    assert not any(label.text() == "PDF Reader" for label in reader.top.findChildren(QLabel))
    assert reader.top.widgetForAction(reader.mode_actions["hand"]) is None
    assert reader.mode_actions["hand"] in reader.reading_tools_menu.actions()
    assert reader.view.mode == "select"
    reader.mode_actions["hand"].trigger()
    assert reader.view.mode == "hand" and reader.mode_actions["hand"].isChecked()
    reader.mode_actions["select"].trigger()
    assert reader.view.mode == "select" and not reader.mode_actions["hand"].isChecked()
    assert abs(reader.toolbar_left_space.width() - reader.toolbar_right_space.width()) <= 1
    for button in reader.top.findChildren(QToolButton):
        if button.objectName() == "qt_toolbar_ext_button":
            assert not button.isVisible()
        else:
            assert button.isVisible() and reader.top.rect().contains(button.geometry())
    assert reader.document_tabs.currentIndex() == 7
    assert reader.document_tabs.tabRect(7).intersects(reader.document_tabs.rect())
    reader.document_tabs.setCurrentIndex(0)
    assert reader.document.path == pdf_path
    assert reader.document_tabs.tabRect(0).intersects(reader.document_tabs.rect())
    # Worst-case footer: OCR controls, unsaved state, many long filenames and
    # a long operation notice must leave tabs and navigation usable.
    reader.modified_label.setText("未保存")
    reader.ocr_progress_action.setVisible(True)
    reader.ocr_stop_action.setVisible(True)
    reader.status_label.timer.setInterval(20)
    reader.status_label.setText("已保存并验证：" + str(pdf_path) * 8)
    qapp.processEvents()
    assert reader.status_label.isVisible()
    assert reader.document_tabs.isVisible()
    tabs_rect = reader.document_tabs.rect().translated(reader.document_tabs.mapTo(reader.footer, QPoint(0, 0)))
    assert reader.footer.rect().contains(tabs_rect)
    assert tabs_rect.width() >= 120 and tabs_rect.right() < reader.ocr_progress.x()
    assert not any(label.text() == pdf_path.name for label in reader.footer.findChildren(QLabel))
    assert reader.stack.rect().contains(reader.status_label.geometry())
    for button in reader.footer.findChildren(QToolButton):
        if button.objectName() == "qt_toolbar_ext_button":
            assert not button.isVisible()
    # Windows coarse timers may be delivered after 30 ms while the full suite
    # is rendering multiple windows. Wait for the observable hide event.
    for _ in range(50):
        if reader.status_label.isHidden():
            break
        QTest.qWait(10)
    assert reader.status_label.isHidden() and reader.document_tabs.isVisible()


def test_hidden_view_signals_and_fit_page_action_target_active_document(reader, pdf_path, tmp_path):
    first = reader.active_session
    reader.open_file(another_pdf(pdf_path, tmp_path))
    reader.view.set_zoom(2)
    reader.view.goto(1)
    first.view.pageChanged.emit(0)
    first.view.zoomChanged.emit(.5)
    first.view.selectionChanged.emit("from the background")
    first.view.inkChosen.emit(0, [[(20, 20), (40, 40)]])
    assert reader.page_spin.value() == 2 and reader.zoom_combo.currentText() == "200%"
    assert reader.selection_text == "" and not reader.document.dirty
    first_zoom = first.view.zoom
    fit_action = next(a for a in reader.footer.actions() if a.text() == "适合整页")
    fit_action.trigger()
    assert reader.view.zoom < 2 and first.view.zoom == first_zoom


def test_stale_ocr_completion_never_changes_new_tab(reader, pdf_path, tmp_path, monkeypatch):
    old_state = (reader.document_epoch, reader.document.state)
    reader.open_file(another_pdf(pdf_path, tmp_path))
    class FinishedProcess:
        def deleteLater(self):
            pass
    reader.ocr_process = FinishedProcess()
    reader.ocr_state = old_state
    reader.ocr_cancelled = False
    applied = []
    monkeypatch.setattr(reader.document, "apply_ocr", lambda results: applied.append(results))
    reader.ocr_finished(0)
    assert not applied and not reader.document.dirty and reader.ocr_process is None
