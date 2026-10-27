"""Local SQLite conversations. Encrypt user content *before* it reaches SQLite."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .config import protect
from .paths import data_root


def document_key(path=None, session_key=None):
    identity = os.path.normcase(str(Path(path).resolve())) if path else "untitled:" + (session_key or "general")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class ChatStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_root() / "chat-history.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=2)
        try:
            if self.db.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise ValueError("聊天数据库来自更新版本，请使用新版 PDF Reader 打开。")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.execute("PRAGMA foreign_keys=ON")
            with self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, document_key TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL,
                    revision INTEGER NOT NULL, summary BLOB NOT NULL, payload BLOB NOT NULL)""")
                self.db.execute("CREATE INDEX IF NOT EXISTS conversations_document ON conversations(document_key, updated DESC)")
                self.db.execute("CREATE TABLE IF NOT EXISTS context_blobs (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
                self.db.execute("""CREATE TABLE IF NOT EXISTS conversation_contexts (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                    blob_id TEXT NOT NULL REFERENCES context_blobs(id))""")
                self.db.execute("CREATE INDEX IF NOT EXISTS context_references ON conversation_contexts(blob_id)")
                self.db.execute("PRAGMA user_version=2")
        except Exception:
            self.db.close()
            raise

    @staticmethod
    def _encode(value):
        # No settings or API credentials are accepted here. There is no
        # plaintext fallback if Windows encryption is unavailable.
        return protect(json.dumps(value, ensure_ascii=False)).encode("ascii")

    @staticmethod
    def _decode(value):
        return json.loads(protect(bytes(value).decode("ascii"), decrypt=True))

    def save(self, key, name, state):
        allowed = ("messages", "entries", "attachments", "input", "context", "context_version", "scroll", "pending", "document_path", "document_context", "continue_from")
        payload = {field: deepcopy(state[field]) for field in allowed if field in state}
        context = payload.get("document_context")
        blob_id, encrypted_blob = None, None
        if context and context.get("data") is not None:
            data = context.pop("data")
            blob_id = hashlib.sha256(data.encode("utf-8")).hexdigest()
            context["blob_id"] = blob_id
            if not self.db.execute("SELECT 1 FROM context_blobs WHERE id=?", (blob_id,)).fetchone():
                encrypted_blob = self._encode({"data": data})
        title = next((text.strip().splitlines()[0][:70] for role, text in payload.get("entries", [])
                      if role == "user" and text.strip()), "未命名对话")
        if title == "未命名对话" and payload.get("input", "").strip():
            title = payload["input"].strip().splitlines()[0][:70]
        summary = self._encode({"title": title, "document": name, "path": state.get("document_path", "")})
        encrypted = self._encode(payload)
        identity = state.get("conversation_id") or uuid.uuid4().hex
        expected = state.get("revision", 0)
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        # Serialize the check and write. Another app window must never silently
        # overwrite a newer version: retain both as separate conversations.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if blob_id and encrypted_blob is not None:
                self.db.execute("INSERT OR IGNORE INTO context_blobs VALUES (?,?)", (blob_id, encrypted_blob))
            elif blob_id and not self.db.execute("SELECT 1 FROM context_blobs WHERE id=?", (blob_id,)).fetchone():
                # Another connection may have removed the last reference since
                # the optimistic read. Recreate the encrypted blob atomically.
                self.db.execute("INSERT INTO context_blobs VALUES (?,?)", (blob_id, self._encode({"data": data})))
            row = self.db.execute("SELECT revision, document_key FROM conversations WHERE id=?", (identity,)).fetchone()
            if row and (row[0] != expected or row[1] != key):
                identity, row = uuid.uuid4().hex, None
            revision = row[0] + 1 if row else 1
            if row:
                self.db.execute("UPDATE conversations SET updated=?,revision=?,summary=?,payload=? WHERE id=?",
                                (now, revision, summary, encrypted, identity))
            else:
                self.db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?)",
                                (identity, key, now, now, revision, summary, encrypted))
            if blob_id:
                self.db.execute("INSERT OR REPLACE INTO conversation_contexts VALUES (?,?)", (identity, blob_id))
            else:
                self.db.execute("DELETE FROM conversation_contexts WHERE conversation_id=?", (identity,))
            self.db.execute("DELETE FROM context_blobs WHERE id NOT IN (SELECT blob_id FROM conversation_contexts)")
        return {"conversation_id": identity, "revision": revision, "document_key": key}

    def load(self, identity):
        row = self.db.execute("SELECT document_key, revision, payload FROM conversations WHERE id=?", (identity,)).fetchone()
        if not row:
            return None
        state = self._decode(row[2])
        context = state.get("document_context")
        if context and context.get("blob_id"):
            blob = self.db.execute("SELECT payload FROM context_blobs WHERE id=?", (context["blob_id"],)).fetchone()
            if not blob:
                raise ValueError("此对话的文档背景数据缺失。")
            context.update(self._decode(blob[0]))
        state.update(conversation_id=identity, document_key=row[0], revision=row[1])
        return state

    def latest(self, key):
        row = self.db.execute("SELECT id FROM conversations WHERE document_key=? ORDER BY updated DESC LIMIT 1", (key,)).fetchone()
        return self.load(row[0]) if row else None

    def list(self, key=None):
        query = "SELECT id, updated, summary FROM conversations"
        rows = self.db.execute(query + (" WHERE document_key=?" if key else "") + " ORDER BY updated DESC", (key,) if key else ()).fetchall()
        result = []
        for identity, updated, encrypted in rows:
            summary = self._decode(encrypted)
            result.append(dict(summary, id=identity, updated=updated))
        return result

    def delete(self, identity):
        with self.db:
            self.db.execute("DELETE FROM conversations WHERE id=?", (identity,))
            self.db.execute("DELETE FROM context_blobs WHERE id NOT IN (SELECT blob_id FROM conversation_contexts)")

    def close(self):
        self.db.close()
