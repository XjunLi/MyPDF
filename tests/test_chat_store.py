import sqlite3
import pytest
from pdfreader.chat_store import ChatStore, document_key


def test_database_encrypts_content_drafts_titles_and_excludes_credentials(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ChatStore(path)
    state = {"entries": [("user", "private question 你好")], "input": "private draft",
             "messages": [{"role": "user", "content": "secret PDF excerpt"}],
             "api_key": "never-store-this-key", "settings": {"key": "never-store-this-key"}}
    key = document_key(tmp_path / "confidential-document.pdf")
    saved = store.save(key, "confidential-document.pdf", state)
    assert store.list()[0]["id"] == saved["conversation_id"]
    restored = store.load(saved["conversation_id"])
    assert restored["input"] == state["input"]
    assert restored["entries"][0][1] == "private question 你好"
    assert "settings" not in restored and "api_key" not in restored
    assert store.list(key)[0]["title"] == "private question 你好"
    # Check every SQLite file, including the live WAL, before checkpointing.
    for file in tmp_path.glob("history.sqlite3*"):
        raw = file.read_bytes()
        for secret in ("private question", "private draft", "secret PDF excerpt", "never-store-this-key", "confidential-document.pdf"):
            assert secret.encode() not in raw
    store.close()
    reopened = ChatStore(path)
    assert reopened.latest(key)["messages"] == state["messages"]
    reopened.close()


def test_multiple_windows_preserve_both_edits_instead_of_overwriting(tmp_path):
    path = tmp_path / "chat.sqlite3"
    first, second = ChatStore(path), ChatStore(path)
    key = document_key(tmp_path / "book.pdf")
    identity = first.save(key, "book.pdf", {"input": "original"})["conversation_id"]
    state_a, state_b = first.load(identity), second.load(identity)
    state_a["input"], state_b["input"] = "window A", "window B"
    first.save(key, "book.pdf", state_a)
    branch = second.save(key, "book.pdf", state_b)
    assert branch["conversation_id"] != identity
    assert first.load(identity)["input"] == "window A"
    assert second.load(branch["conversation_id"])["input"] == "window B"
    assert len(first.list(key)) == 2
    first.close()
    second.close()


def test_failed_encryption_never_falls_back_to_plaintext(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "chat.sqlite3")
    def broken(*args, **kwargs):
        raise OSError("DPAPI unavailable")
    monkeypatch.setattr("pdfreader.chat_store.protect", broken)
    with pytest.raises(OSError):
        store.save("key", "document", {"input": "private text"})
    assert store.db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    store.close()


def test_delete_and_document_isolation(tmp_path):
    store = ChatStore(tmp_path / "chat.sqlite3")
    first = store.save("first", "A", {"input": "A"})["conversation_id"]
    second = store.save("second", "B", {"input": "B"})["conversation_id"]
    store.delete(first)
    assert store.latest("first") is None
    assert store.load(second)["input"] == "B"
    store.close()


def test_future_schema_is_not_downgraded(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="更新版本"):
        ChatStore(path)


def test_v1_database_migrates_without_losing_existing_conversations(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, document_key TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, revision INTEGER NOT NULL, summary BLOB NOT NULL, payload BLOB NOT NULL)")
        db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?)",
                   ("existing", "book", "2026", "2026", 2, ChatStore._encode({"title": "Old chat", "document": "book"}),
                    ChatStore._encode({"input": "Preserve my draft", "entries": [["user", "Old question"]], "context": 0})))
        db.execute("PRAGMA user_version=1")
    store = ChatStore(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    old = store.load("existing")
    assert old["input"] == "Preserve my draft" and old["revision"] == 2
    assert store.list()[0]["title"] == "Old chat"
    store.close()


def test_document_blob_encrypted_deduplicated_and_collected_after_last_reference(tmp_path):
    path = tmp_path / "chat.sqlite3"
    store = ChatStore(path)
    background = {"mode": "text", "name": "private-document.pdf", "data": "SENSITIVE_FULL_DOCUMENT" * 1000, "pages": 2}
    state = {"input": "Question", "document_context": background, "context": 3, "context_version": 2}
    saved = store.save("book", "Book", state)
    state.update(saved)
    assert "data" in background
    blob = store.db.execute("SELECT payload FROM context_blobs").fetchone()[0]
    for i in range(3):
        state["input"] = "Draft " + str(i)
        state.update(store.save("book", "Book", state))
        # DPAPI would produce different ciphertext if we encrypted/wrote it again.
        assert store.db.execute("SELECT payload FROM context_blobs").fetchone()[0] == blob
    other = store.save("other", "Other", {"document_context": background, "input": "Another conversation"})
    assert store.db.execute("SELECT count(*) FROM context_blobs").fetchone()[0] == 1
    encrypted_payload = store.db.execute("SELECT payload FROM conversations WHERE id=?", (saved["conversation_id"],)).fetchone()[0]
    assert "data" not in store._decode(encrypted_payload)["document_context"]
    for file in tmp_path.glob("chat.sqlite3*"):
        assert b"SENSITIVE_FULL_DOCUMENT" not in file.read_bytes()
        assert b"private-document.pdf" not in file.read_bytes()
    store.delete(saved["conversation_id"])
    assert store.load(other["conversation_id"])["document_context"]["data"] == background["data"]
    store.close()
    reopened = ChatStore(path)
    assert reopened.load(other["conversation_id"])["document_context"]["data"] == background["data"]
    reopened.delete(other["conversation_id"])
    assert reopened.db.execute("SELECT count(*) FROM context_blobs").fetchone()[0] == 0
    reopened.close()


def test_replacing_background_and_failed_save_preserve_committed_data(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "chat.sqlite3")
    state = {"input": "Question", "document_context": {"data": "ORIGINAL_CONTEXT", "mode": "text"}}
    state.update(store.save("book", "Book", state))
    state["document_context"]["data"] = "REPLACEMENT_CONTEXT"
    original = store._encode
    def fail_after_blob(value):
        if "title" in value:
            raise OSError("Encryption failed")
        return original(value)
    monkeypatch.setattr(store, "_encode", fail_after_blob)
    with pytest.raises(OSError):
        store.save("book", "Book", state)
    assert store.load(state["conversation_id"])["document_context"]["data"] == "ORIGINAL_CONTEXT"
    assert store.db.execute("SELECT count(*) FROM context_blobs").fetchone()[0] == 1
    monkeypatch.setattr(store, "_encode", original)
    state.update(store.save("book", "Book", state))
    assert store.load(state["conversation_id"])["document_context"]["data"] == "REPLACEMENT_CONTEXT"
    assert store.db.execute("SELECT count(*) FROM context_blobs").fetchone()[0] == 1
    store.close()


def test_concurrent_background_changes_keep_each_windows_snapshot(tmp_path):
    path = tmp_path / "chat.sqlite3"
    first, second = ChatStore(path), ChatStore(path)
    identity = first.save("book", "Book", {"document_context": {"data": "Original"}})["conversation_id"]
    state_a, state_b = first.load(identity), second.load(identity)
    state_a["document_context"]["data"] = "Window A updated document"
    first.save("book", "Book", state_a)
    branch = second.save("book", "Book", state_b)
    assert branch["conversation_id"] != identity
    assert first.load(identity)["document_context"]["data"] == "Window A updated document"
    assert second.load(branch["conversation_id"])["document_context"]["data"] == "Original"
    assert first.db.execute("PRAGMA foreign_key_check").fetchall() == []
    first.close()
    second.close()
