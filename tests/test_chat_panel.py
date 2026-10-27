import base64
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
import pymupdf as fitz
from PySide6.QtCore import QObject, Signal, QUrl, QTimer, Qt
from PySide6.QtGui import QImage, QTextTable
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QComboBox, QListWidget, QDialog, QTabWidget

from pdfreader.app import MainWindow
from pdfreader.ai import AIJob, history_for_request
from pdfreader.chat_markdown import PrivateDocument, markdown_html
from pdfreader.chat_store import ChatStore, document_key
from pdfreader.style import STYLE


class FakeJob(QObject):
    chunk, finished, failed = Signal(str), Signal(str), Signal(str)
    def __init__(self, config, messages):
        super().__init__()
        self.messages, self.thread, self.cancelled = deepcopy(messages), None, False
    def start(self):
        pass
    def cancel(self):
        self.cancelled = True


@pytest.fixture
def reader(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr("pdfreader.panels.AIJob", FakeJob)
    window = MainWindow()
    window.setStyleSheet(STYLE)
    window.auto_ocr.setChecked(False)
    window.open_file(pdf_path)
    window.settings.values["ai"] = {"enabled": True, "model": "test", "key": "private-test-key", "endpoint": "https://example.com/v1"}
    window.show()
    window.show_panel("ai")
    qapp.processEvents()
    yield window
    for session in window.sessions:
        session.document.saved_state = session.document.state
    window.close()
    qapp.processEvents()


def test_current_page_image_request_only_and_no_repeat_upload(reader, qapp, monkeypatch):
    ai = reader.ai
    reader.view.goto(1)
    qapp.processEvents()
    ai.set_attachment(2)
    ai.input.setPlainText("Explain this page")
    ai.send()
    job = ai.job
    parts = job.messages[-1]["content"]
    assert parts[0]["type"] == "text" and "第 2 页" in parts[0]["text"]
    image = QImage.fromData(base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1]))
    assert not image.isNull() and max(image.width(), image.height()) <= 1601
    expected = QImage.fromData(reader.ai_page_image()["png"])
    assert image == expected
    job.chunk.emit("## Summary\n\n**Bold** and `code`.")
    job.finished.emit("## Summary\n\n**Bold** and `code`.")
    ai.set_attachment(0)
    monkeypatch.setattr(ai, "context_provider", lambda kind: pytest.fail("No document context requested"))
    monkeypatch.setattr(ai, "image_provider", lambda: pytest.fail("No image requested"))
    ai.input.setPlainText("Follow up without attachment")
    ai.send()
    assert all(isinstance(m["content"], str) for m in ai.job.messages)
    assert ai.job.messages[-1]["content"] == "Follow up without attachment"
    assert "data:image" not in json.dumps(ai.job.messages)
    # The original screenshot remains encrypted locally for the record.
    assert isinstance(ai.store.load(ai.conversation_id)["messages"][0]["content"], list)
    previews = []
    monkeypatch.setattr(ai, "preview_attachment", previews.append)
    ai.link_clicked(QUrl("attachment:0"))
    assert QImage.fromData(previews[0]["png"]) == expected


def test_screenshot_includes_current_rotation_and_annotations(reader):
    first = QImage.fromData(reader.ai_page_image()["png"])
    reader.document.add_comment(0, (40, 180), "Visible note")
    second = QImage.fromData(reader.ai_page_image()["png"])
    assert first != second
    reader.document.doc[0].set_rotation(90)
    third = QImage.fromData(reader.ai_page_image()["png"])
    assert third.width() == first.height() and third.height() == first.width()


def test_selection_attachment_and_missing_selection_are_explicit(reader, monkeypatch):
    ai = reader.ai
    ai.set_attachment(1)
    ai.input.setPlainText("Explain")
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: notices.append(a))
    ai.send()
    assert ai.job is None and notices and ai.input.toPlainText() == "Explain"
    reader.selection_changed("The selected phrase")
    ai.send()
    assert "The selected phrase" in ai.job.messages[-1]["content"]
    assert "Second page" not in ai.job.messages[-1]["content"]
    assert ai.attachments["0"]["text"] == "The selected phrase"


def test_persistent_draft_after_tab_close_and_restart(reader, pdf_path, qapp):
    ai = reader.ai
    ai.input.setPlainText("Remember this draft")
    QTest.qWait(740)
    identity = ai.conversation_id
    assert identity and ai.store.load(identity)["input"] == "Remember this draft"
    assert reader.close_document()
    assert reader.open_file(pdf_path)
    assert ai.input.toPlainText() == "Remember this draft" and ai.conversation_id == identity
    # One record, not a new optimistic-concurrency branch on every tab switch.
    assert len(ai.store.list(ai.document_key)) == 1
    assert reader.close()
    reopened = MainWindow()
    reopened.auto_ocr.setChecked(False)
    reopened.open_file(pdf_path)
    assert reopened.ai.input.toPlainText() == "Remember this draft"
    assert reopened.ai.conversation_id == identity
    reopened.close()


def test_new_chat_keeps_history_export_and_delete(reader, monkeypatch, tmp_path):
    ai = reader.ai
    ai.input.setPlainText("First question")
    ai.send()
    ai.job.finished.emit("## Heading\n\n- one\n- **two**")
    first_id = ai.conversation_id
    ai.new_chat()
    assert ai.conversation_id != first_id and not ai.entries
    assert len(ai.store.list(ai.document_key)) == 2
    ai.restore_state(ai.store.load(first_id))
    exported = tmp_path / "conversation.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(exported), "Markdown"))
    assert ai.export_chat()
    assert "## Heading" in exported.read_text(encoding="utf-8")
    assert "private-test-key" not in exported.read_text(encoding="utf-8")
    ai.copy_answer()
    assert QApplication.clipboard().text() == "## Heading\n\n- one\n- **two**"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    ai.delete_chat()
    assert ai.store.load(first_id) is None and not ai.entries


def test_save_as_clones_chat_without_losing_original_history(reader, pdf_path, tmp_path, monkeypatch):
    reader.ai.input.setPlainText("Related to this document")
    assert reader.ai.flush()
    old_key, old_id = reader.ai.document_key, reader.ai.conversation_id
    destination = tmp_path / "new-copy.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(destination), "PDF"))
    assert reader.save(True)
    assert reader.ai.document_key == document_key(destination)
    assert reader.ai.conversation_id != old_id
    assert reader.ai.store.latest(old_key)["input"] == "Related to this document"
    assert reader.close_document()
    reader.open_file(destination)
    assert reader.ai.input.toPlainText() == "Related to this document"


def test_database_failure_visible_and_draft_kept(reader, monkeypatch):
    ai = reader.ai
    ai.input.setPlainText("Do not lose my draft")
    original = ai.store.save
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(ai.store, "save", fail)
    assert not ai.flush()
    assert "保存失败" in ai.save_status.text() and "disk full" in ai.save_status.toolTip()
    monkeypatch.setattr(ai, "confirm_history_loss", lambda: False)
    assert not reader.close()
    assert ai.input.toPlainText() == "Do not lose my draft"
    monkeypatch.setattr(ai.store, "save", original)
    assert ai.flush() and "保存失败" not in ai.save_status.text()


def test_markdown_formats_and_blocks_embedded_resources(qapp, tmp_path):
    secret = tmp_path / "private.txt"
    secret.write_text("MUST NOT LOAD")
    source = "## Heading\n\n**bold** and `code`\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n![x](https://example.com/track.png)\n\n![local](" + secret.as_uri() + ")\n\n<script>evil()</script>"
    rendered = markdown_html(source)
    assert "<img" not in rendered and "<script" not in rendered
    document = PrivateDocument()
    document.setHtml(rendered)
    cursor = document.find("bold")
    assert cursor.charFormat().fontWeight() >= 600
    assert any(isinstance(frame, QTextTable) for frame in document.rootFrame().childFrames())
    assert not document.loadResource(2, QUrl(secret.as_uri()))
    assert "MUST NOT LOAD" not in document.toPlainText()


def test_streaming_preserves_history_scroll_position(reader, qapp):
    ai = reader.ai
    ai.entries = [("user", "Question"), ("assistant", "\n\n".join(f"Paragraph {n}" for n in range(100)))]
    ai.render(force_bottom=True)
    qapp.processEvents()
    bar = ai.chat.verticalScrollBar()
    assert bar.maximum() > 200
    bar.setValue(50)
    ai.entries[-1] = ("assistant", ai.entries[-1][1] + "\n\nMore text")
    ai.render()
    assert bar.value() == 50


def test_partial_crash_restore_does_not_send_unfinished_turn(reader):
    ai = reader.ai
    ai.input.setPlainText("Interrupted question")
    ai.send()
    ai.job.chunk.emit("Partial response")
    ai.flush()
    recovered = ai.store.load(ai.conversation_id)
    ai.cancel()
    ai.restore_state(recovered)
    assert "Partial response" in ai.entries[-1][1] and "已中断" in ai.entries[-1][1]
    assert not history_for_request(ai.messages)


def test_history_budget_and_failed_turns():
    messages = [{"role": "user", "content": "failed question"}, {"role": "user", "content": "success question"},
                {"role": "assistant", "content": "answer"}]
    assert len(history_for_request(messages)) == 2
    assert "failed" not in json.dumps(history_for_request(messages))
    assert history_for_request(messages, max_characters=3) == []


def test_global_history_opens_the_correct_pdf_and_keeps_current_draft(reader, pdf_path, tmp_path):
    ai = reader.ai
    first = reader.active_session
    ai.input.setPlainText("First saved question")
    ai.flush()
    first_id = ai.conversation_id
    second_path = tmp_path / "different.pdf"
    second_path.write_bytes(pdf_path.read_bytes())
    reader.open_file(second_path)
    ai.input.setPlainText("Second unsent draft")
    def choose_first():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QComboBox).setCurrentIndex(1)
        listing = dialog.findChild(QListWidget)
        for index in range(listing.count()):
            if listing.item(index).data(Qt.ItemDataRole.UserRole)["id"] == first_id:
                listing.setCurrentRow(index)
                dialog.accept()
                return
        dialog.reject()
    QTimer.singleShot(0, choose_first)
    ai.show_history()
    assert reader.active_session is first
    assert ai.input.toPlainText() == "First saved question"
    reader.activate_session(reader.sessions[1])
    assert ai.input.toPlainText() == "Second unsent draft"


def test_unsaved_document_history_remains_accessible_as_archive(reader, monkeypatch):
    reader.new_document()
    ai = reader.ai
    ai.input.setPlainText("Draft belonging to an unsaved PDF")
    ai.flush()
    identity = ai.conversation_id
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    assert reader.close_document()
    archived = []
    monkeypatch.setattr(ai, "view_archive", lambda state, record: archived.append((state, record)))
    def choose_archive():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QComboBox).setCurrentIndex(1)
        listing = dialog.findChild(QListWidget)
        for index in range(listing.count()):
            if listing.item(index).data(Qt.ItemDataRole.UserRole)["id"] == identity:
                listing.setCurrentRow(index)
                dialog.accept()
                return
        dialog.reject()
    QTimer.singleShot(0, choose_archive)
    ai.show_history()
    assert archived[0][0]["input"] == "Draft belonging to an unsaved PDF"
    assert "未发送草稿" in ai.export_markdown(archived[0][0], archived[0][1]["document"])


def test_normal_pdf_save_keeps_active_ai_stream(reader):
    ai = reader.ai
    ai.input.setPlainText("Still working")
    ai.send()
    job = ai.job
    assert reader.save()
    assert ai.job is job and not job.cancelled


def test_stream_error_preserves_partial_answer(reader):
    ai = reader.ai
    ai.input.setPlainText("Question")
    ai.send()
    ai.job.chunk.emit("Useful partial reply")
    ai.job.failed.emit("Connection interrupted")
    assert ai.entries[-2] == ("assistant", "Useful partial reply")
    assert ai.entries[-1] == ("info", "Connection interrupted")
    assert not history_for_request(ai.messages)


def test_real_http_client_serializes_image_payload_without_uploading_file(qapp):
    config = {"endpoint": "https://example.com/v1", "model": "vision", "enabled": True, "key": "test-key"}
    content = [{"type": "text", "text": "Only this page"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}}]
    class Response:
        headers = {"Content-Type": "text/event-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"A page"}}]}\n'
            yield b'data: [DONE]\n'
    job = AIJob(config, [{"role": "user", "content": content}])
    answers = []
    job.finished.connect(answers.append)
    with patch("pdfreader.ai.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        job._run()
        request = opener.return_value.open.call_args.args[0]
        assert json.loads(request.data)["messages"][0]["content"] == content
        assert request.headers["Authorization"] == "Bearer test-key"
        assert "test-key" not in request.data.decode()
    assert answers == ["A page"]


def file_parts(messages):
    return [part["file"] for m in messages if isinstance(m.get("content"), list)
            for part in m["content"] if part.get("type") == "file"]


def test_default_context_combines_full_pdf_latest_page_and_optional_selection(reader, qapp):
    ai = reader.ai
    assert ai.attachment_kind == 3
    assert ai.job is None and ai.document_context is None
    reader.view.goto(1)
    reader.selection_changed("Focus on these words")
    qapp.processEvents()
    assert ai.job is None and ai.document_context is None  # Navigation never starts work or a request.
    ai.input.setPlainText("Explain this in context")
    ai.send()
    messages = ai.job.messages
    files = file_parts(messages)
    assert len(files) == 1 and files[0]["filename"] == "source.pdf"
    with fitz.open(stream=base64.b64decode(files[0]["file_data"].split(",", 1)[1]), filetype="pdf") as pdf:
        assert len(pdf) == 2 and "Second page" in pdf[1].get_text()
    parts = messages[-1]["content"]
    assert "第 2 页" in parts[0]["text"] and "Focus on these words" in parts[2]["text"]
    assert QImage.fromData(base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1])) == QImage.fromData(reader.ai_page_image()["png"])
    assert not file_parts(ai.messages)  # Single local background, independent of rolling turns.
    ai.job.finished.emit("Answer")
    reader.selection_changed("")
    reader.view.goto(0)
    qapp.processEvents()
    ai.input.setPlainText("And this page?")
    ai.send()
    assert len(ai.job.messages[-1]["content"]) == 2
    assert "第 1 页" in ai.job.messages[-1]["content"][0]["text"]


def test_full_background_survives_history_window_without_old_page_images(reader):
    ai = reader.ai
    for turn in range(6):
        ai.input.setPlainText(f"question {turn}")
        ai.send()
        serialized = json.dumps(ai.job.messages)
        assert len(file_parts(ai.job.messages)) == 1
        assert serialized.count("data:image/png;base64,") == 1
        if turn == 5:
            assert "question 0" not in serialized
            assert "question 1" in serialized
            assert len(ai.job.messages) == 11  # system + document + four pairs + current question
        ai.job.finished.emit(f"answer {turn}")
    assert ai.store.db.execute("SELECT count(*) FROM context_blobs").fetchone()[0] == 1


def test_background_cache_refreshes_after_edit_undo_save_as_and_file_switch(reader, pdf_path, tmp_path):
    ai = reader.ai
    first = ai.get_document_context()
    assert ai.get_document_context() is first
    reader.document.add_comment(0, (40, 200), "New note")
    edited = ai.get_document_context()
    assert edited["digest"] != first["digest"]
    assert reader.document.undo()
    assert ai.get_document_context()["source_state"] == first["source_state"]
    first_session = reader.active_session
    renamed = tmp_path / "renamed.pdf"
    reader.document.save(renamed)
    ai.rebind_document(renamed, "renamed.pdf")
    assert ai.get_document_context()["name"] == "renamed.pdf"
    other = tmp_path / "different.pdf"
    other.write_bytes(pdf_path.read_bytes())
    reader.open_file(other)
    assert ai.document_context is None
    assert ai.get_document_context()["source_session"] != first["source_session"]
    reader.activate_session(first_session)
    assert ai.get_document_context()["source_session"] == first["source_session"]
    assert ai.get_document_context()["name"] == "renamed.pdf"


def test_explicit_attachment_scope_and_legacy_migration(reader, monkeypatch):
    ai = reader.ai
    ai.restore_state({"input": "Old draft", "context": 0})
    assert ai.attachment_kind == 3 and ai.input.toPlainText() == "Old draft"
    ai.set_attachment(0)
    state = ai.snapshot()
    ai.restore_state(state)
    assert ai.attachment_kind == 0
    monkeypatch.setattr(ai, "document_provider", lambda *args: pytest.fail("Unexpected whole document access"))
    ai.send()
    assert all(isinstance(m["content"], str) for m in ai.job.messages)
    ai.job.finished.emit("Independent reply")
    reader.selection_changed("Only this excerpt")
    ai.set_attachment(1)
    ai.input.setPlainText("Selection only")
    ai.send()
    assert not file_parts(ai.job.messages) and "Only this excerpt" in ai.job.messages[-1]["content"]


def test_text_compatibility_keeps_all_pages_and_current_screenshot(reader):
    ai = reader.ai
    reader.settings.values["ai"]["document_mode"] = "text"
    reader.selection_changed("Focus")
    ai.input.setPlainText("Explain")
    ai.send()
    assert not file_parts(ai.job.messages)
    full = ai.job.messages[1]["content"]
    assert "[第 1 页]" in full and "Second page" in full
    assert ai.job.messages[-1]["content"][1]["type"] == "image_url"
    assert ai.document_context["mode"] == "text"


@pytest.mark.parametrize("kind", [1, 3])
def test_oversized_selection_preserves_draft_without_sending(reader, monkeypatch, kind):
    ai = reader.ai
    ai.set_attachment(kind)
    reader.selection_changed("X" * 12001)
    ai.input.setPlainText("Keep my question")
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    ai.send()
    assert ai.job is None and not ai.entries and ai.input.toPlainText() == "Keep my question"
    assert "未截断" in errors[0]


def test_smart_attachment_preview_export_and_saved_context(reader, monkeypatch):
    ai = reader.ai
    reader.selection_changed("Selected words for export")
    seen = []
    def inspect_dialog():
        dialog = QApplication.activeModalWidget()
        tabs = dialog.findChild(QTabWidget)
        seen.append([tabs.tabText(index) for index in range(tabs.count())])
        assert tabs.widget(2).toPlainText() == "Selected words for export"
        dialog.reject()
    QTimer.singleShot(0, inspect_dialog)
    ai.preview_attachment()
    assert seen == [["全文背景", "当前页", "选中文字"]] and ai.job is None
    ai.input.setPlainText("Discuss")
    ai.send()
    ai.job.finished.emit("## Answer")
    loaded = ai.store.load(ai.conversation_id)
    assert loaded["document_context"]["data"] == ai.document_context["data"]
    exported = ai.export_markdown()
    assert "Selected words for export" in exported and "共 2 页" in exported
    assert ai.document_context["data"] not in exported
    records = []
    monkeypatch.setattr(ai, "preview_attachment", records.append)
    ai.link_clicked(QUrl("attachment:0"))
    assert records[0]["kind"] == "smart" and records[0]["selection"] == "Selected words for export"
    assert not QImage.fromData(records[0]["png"]).isNull()


def test_settings_preserve_document_input_mode(qapp):
    from pdfreader.dialogs import AISettingsDialog
    dialog = AISettingsDialog(None, {"document_mode": "text", "enabled": True})
    assert dialog.values()["document_mode"] == "text"
    dialog.document_mode.setCurrentIndex(0)
    assert dialog.values()["document_mode"] == "pdf"
