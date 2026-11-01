"""Authenticated local encryption: Windows DPAPI or macOS Keychain + AES-GCM.

Only an opaque key identifier is stored beside the database on macOS. Losing
Keychain access is an error, never permission to persist plaintext or reset keys.
"""
import base64
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
import sys
import uuid
from .paths import data_root

MAC_PREFIX = "macos-aesgcm-v1:"
KEYCHAIN_SERVICE = "com.xjunli.pdfreader.local-data"


def _keychain():
    # Select the native backend explicitly: never use a fallback file backend.
    from keyring.backends.macOS import Keyring
    return Keyring()


@lru_cache(maxsize=32)
def _read_mac_key(identity):
    try:
        encoded = _keychain().get_password(KEYCHAIN_SERVICE, identity)
        if not encoded:
            raise RuntimeError("本机钥匙串中找不到此记录的加密密钥。")
        key = base64.b64decode(encoded, validate=True)
        if len(key) != 32:
            raise ValueError("Invalid key length")
        return key
    except Exception as exc:
        raise RuntimeError("无法读取 macOS 钥匙串，记录仍保持加密。请解锁钥匙串或恢复原账户中的密钥。") from exc


def _mac_identity():
    from PySide6.QtCore import QLockFile
    root = data_root()
    lock = QLockFile(str(root / "keychain-init.lock"))
    if not lock.tryLock(5000):
        raise RuntimeError("另一个 PDF Reader 正在初始化本机加密，请稍后重试。")
    try:
        record = root / "keychain-id"
        if record.exists():
            return str(uuid.UUID(record.read_text(encoding="ascii").strip()))
        identity = str(uuid.uuid4())
        encoded = base64.b64encode(os.urandom(32)).decode("ascii")
        backend = _keychain()
        backend.set_password(KEYCHAIN_SERVICE, identity, encoded)
        try:
            temp = record.with_suffix(".tmp")
            temp.write_text(identity + "\n", encoding="ascii")
            os.replace(temp, record)
        except Exception:
            backend.delete_password(KEYCHAIN_SERVICE, identity)
            raise
        return identity
    except Exception as exc:
        raise RuntimeError("无法初始化 macOS 钥匙串加密；没有写入明文记录。") from exc
    finally:
        lock.unlock()


def _protect_mac(value, decrypt=False):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    if decrypt:
        if not value.startswith(MAC_PREFIX):
            raise RuntimeError("这份加密记录来自其他系统，需在原系统导出后使用。")
        identity, encoded = value[len(MAC_PREFIX):].split(":", 1)
        identity = str(uuid.UUID(identity))
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) < 28:
            raise ValueError("加密记录不完整。")
        key = _read_mac_key(identity)
        try:
            return AESGCM(key).decrypt(raw[:12], raw[12:], (MAC_PREFIX + identity).encode()).decode("utf-8")
        except InvalidTag as exc:
            raise ValueError("加密记录校验失败，无法解密。") from exc
    identity = _mac_identity()
    key = _read_mac_key(identity)
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, value.encode("utf-8"), (MAC_PREFIX + identity).encode())
    return MAC_PREFIX + identity + ":" + base64.b64encode(nonce + encrypted).decode("ascii")


def protect(value, decrypt=False):
    if sys.platform == "win32":
        if decrypt and value.startswith(MAC_PREFIX):
            raise RuntimeError("这份记录使用 macOS 钥匙串加密，需在原 Mac 导出。")
        return _protect_windows(value, decrypt)
    if sys.platform == "darwin":
        return _protect_mac(value, decrypt)
    raise RuntimeError("本机加密目前支持 Windows 和 macOS。")


class Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect_windows(value, decrypt=False):
    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        success = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        success = crypt.CryptProtectData(ctypes.byref(source), "PDF Reader private data", None, None, None, 1, ctypes.byref(target))
    if not success:
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(target.data, target.length)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)
