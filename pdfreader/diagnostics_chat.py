"""Frozen-app chat smoke checks using in-process HTTP responses, never a service."""
from copy import deepcopy
import base64
import json
import pymupdf as fitz
from PySide6.QtGui import QImage


def exercise_chat(window, root, output):
    from . import ai as client, panels
    from .chat_store import ChatStore
    original_settings = deepcopy(window.settings.values.get("ai", {}))
    original_opener, original_factory = client.build_opener, panels.AIJob
    captured = []
    reply = "### 本机测试\n\n**文字已加粗**，`代码` 保留格式。\n\n- 当前页截图\n- 自动保存草稿\n\n| 功能 | 状态 |\n| --- | --- |\n| SQLite | 已保存 |"

    class Response:
        headers = {"Content-Type": "text/event-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            for fragment in (reply[:20], reply[20:]):
                yield ("data: " + json.dumps({"choices": [{"delta": {"content": fragment}}]}) + "\n").encode()
            yield b"data: [DONE]\n"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://example.invalid/v1/chat/completions"
            captured.append(json.loads(request.data))
            return Response()

    def synchronous_job(config, messages):
        job = client.AIJob(config, messages)
        job.start = job._run
        return job

    try:
        client.build_opener = lambda *args: Opener()
        panels.AIJob = synchronous_job
        window.settings.values["ai"] = {"enabled": True, "endpoint": "https://example.invalid/v1",
                                         "model": "offline-smoke", "key": "smoke-key-not-a-secret"}
        window.ai.update_connection()
        window.ai.rebind_document(window.document.path, window.active_session.name)
        window.ai.new_chat()
        window.ai.set_attachment(2)
        window.ai.input.setPlainText("SMOKE_PRIVATE_QUESTION")
        window.ai.send()
        assert window.ai.entries[-1] == ("assistant", reply)
        content = captured[0]["messages"][-1]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert not QImage.fromData(window.ai_page_image()["png"]).isNull()
        assert window.ai.chat.document().find("文字已加粗").charFormat().fontWeight() >= 600
        output["checks"].append("ai_native_markdown_and_image_payload")
        window.ai.set_attachment(0)
        window.ai.input.setPlainText("SMOKE_FOLLOW_UP_NO_ATTACHMENT")
        window.ai.send()
        assert captured[1]["messages"][-1]["content"] == "SMOKE_FOLLOW_UP_NO_ATTACHMENT"
        assert "data:image" not in json.dumps(captured[1])
        output["checks"].append("ai_no_attachment_no_old_image_upload")
        window.ai.input.setPlainText("SMOKE_PRIVATE_DRAFT")
        assert window.ai.flush()
        identity, db_path = window.ai.conversation_id, window.ai.store.path
        with_store = ChatStore(db_path)
        state = with_store.load(identity)
        assert state["input"] == "SMOKE_PRIVATE_DRAFT" and len(state["entries"]) == 4
        with_store.close()
        for file in db_path.parent.glob(db_path.name + "*"):
            raw = file.read_bytes()
            assert b"SMOKE_PRIVATE" not in raw and b"smoke-key-not-a-secret" not in raw
        output["checks"].append("sqlite_encrypted_draft_and_chat")
        path = window.document.path
        assert window.close_document()
        assert window.open_file(path)
        assert window.ai.conversation_id == identity
        assert window.ai.input.toPlainText() == "SMOKE_PRIVATE_DRAFT"
        assert window.ai.entries[-1] == ("assistant", reply)
        output["checks"].append("sqlite_reopen_document_restore")
        window.ai.new_chat()
        assert window.ai.attachment_kind == 3
        window.selection_changed("SMOKE_SELECTED_CONTEXT")
        window.ai.input.setPlainText("SMOKE_FULL_DOCUMENT_QUESTION")
        window.ai.send()
        messages = captured[-1]["messages"]
        file = messages[1]["content"][1]["file"]
        with fitz.open(stream=base64.b64decode(file["file_data"].split(",", 1)[1]), filetype="pdf") as pdf:
            assert len(pdf) == window.document.count
        parts = messages[-1]["content"]
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "SMOKE_SELECTED_CONTEXT" in parts[2]["text"]
        output["checks"].append("ai_full_pdf_current_page_and_selection")
        saved = window.ai.store.load(window.ai.conversation_id)
        assert saved["document_context"]["data"] == window.ai.document_context["data"]
        ciphertext = window.ai.store.db.execute("SELECT payload FROM context_blobs").fetchone()[0]
        window.ai.input.setPlainText("SMOKE_CONTEXT_DRAFT")
        assert window.ai.flush()
        assert window.ai.store.db.execute("SELECT payload FROM context_blobs").fetchone()[0] == ciphertext
        output["checks"].append("sqlite_full_document_encrypted_and_deduplicated")
        window.settings.values["ai"]["document_mode"] = "text"
        window.selection_changed("")
        window.ai.input.setPlainText("SMOKE_TEXT_COMPATIBILITY")
        window.ai.send()
        messages = captured[-1]["messages"]
        assert isinstance(messages[1]["content"], str)
        assert f"[第 {window.document.count} 页]" in messages[1]["content"]
        assert len(messages[-1]["content"]) == 2
        assert "file_data" not in json.dumps(messages)
        output["checks"].append("ai_full_text_compatibility_with_current_page")
        window.show_panel("ai")
        window.ai.chat.verticalScrollBar().setValue(0)
        window.grab().save(str(root / "ai.png"))
    finally:
        client.build_opener, panels.AIJob = original_opener, original_factory
        window.settings.values["ai"] = original_settings
        window.ai.update_connection()
