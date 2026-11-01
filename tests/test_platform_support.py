import base64
import sys
from pathlib import Path
import pytest
from PySide6.QtCore import QEvent, QUrl
from pdfreader import privacy, paths
from pdfreader.application import FileOpenRouter, shortcut_text


@pytest.fixture
def keychain(tmp_path, monkeypatch):
    class MemoryKeychain:
        def __init__(self): self.values = {}
        def get_password(self, service, identity): return self.values.get((service, identity))
        def set_password(self, service, identity, value): self.values[service, identity] = value
        def delete_password(self, service, identity): del self.values[service, identity]
    backend = MemoryKeychain()
    monkeypatch.setattr(privacy, "_keychain", lambda: backend)
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    privacy._read_mac_key.cache_clear()
    yield backend
    privacy._read_mac_key.cache_clear()


def test_mac_encryption_randomized_authenticated_and_reopens(keychain, tmp_path):
    text = "private PDF / 你好 / ∇" * 1000
    first, second = privacy._protect_mac(text), privacy._protect_mac(text)
    assert first != second and text not in first
    assert len(keychain.values) == 1
    privacy._read_mac_key.cache_clear()
    assert privacy._protect_mac(first, True) == text
    assert privacy._protect_mac(second, True) == text
    assert not any(text.encode() in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    prefix, data = first.rsplit(":", 1)
    raw = bytearray(base64.b64decode(data)); raw[-1] ^= 1
    with pytest.raises(ValueError, match="校验"):
        privacy._protect_mac(prefix + ":" + base64.b64encode(raw).decode(), True)


def test_missing_keychain_does_not_replace_key_or_write_plaintext(keychain, tmp_path):
    cipher = privacy._protect_mac("sensitive text")
    keychain.values.clear()
    privacy._read_mac_key.cache_clear()
    before = (tmp_path / "keychain-id").read_bytes()
    with pytest.raises(RuntimeError, match="钥匙串"):
        privacy._protect_mac(cipher, True)
    with pytest.raises(RuntimeError, match="钥匙串"):
        privacy._protect_mac("new secret")
    assert (tmp_path / "keychain-id").read_bytes() == before
    assert not keychain.values


def test_denied_keychain_fails_without_local_secret(keychain, tmp_path, monkeypatch):
    def denied(*args): raise OSError("Keychain locked")
    monkeypatch.setattr(keychain, "set_password", denied)
    with pytest.raises(RuntimeError, match="没有写入明文"):
        privacy._protect_mac("never write this")
    assert not (tmp_path / "keychain-id").exists()
    assert not any(b"never write this" in p.read_bytes() for p in tmp_path.iterdir() if p.is_file())


def test_mac_rejects_other_platform_ciphertext(keychain):
    with pytest.raises(RuntimeError, match="其他系统"):
        privacy._protect_mac("legacy-windows-dpapi", True)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_platform_data_directory(tmp_path, monkeypatch, platform):
    monkeypatch.delenv("PDFREADER_DATA_DIR", raising=False)
    monkeypatch.delenv("SLATEPDF_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "windows"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(paths.sys, "platform", platform)
    parent = {"win32": tmp_path / "windows", "darwin": tmp_path / "Library/Application Support", "linux": tmp_path / "xdg"}[platform]
    assert paths.data_root() == parent / "PDFReader"
    assert paths.legacy_data_root() == parent / "SlatePDF"
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "isolated"))
    assert paths.data_root() == tmp_path / "isolated"
    assert paths.legacy_data_root() is None


def test_finder_open_queues_before_window_and_opens_later(qapp, pdf_path):
    router = FileOpenRouter(qapp)
    class OpenEvent(QEvent):
        def __init__(self, path): super().__init__(QEvent.Type.FileOpen); self.path = path
        def file(self): return self.path
        def url(self): return QUrl.fromLocalFile(self.path)
    class Window:
        def __init__(self): self.opened = []
        def open_files(self, files): self.opened.extend(files)
        def showNormal(self): pass
        def raise_(self): pass
        def activateWindow(self): pass
    assert qapp.sendEvent(qapp, OpenEvent(str(pdf_path)))
    qapp.processEvents()
    window = Window()
    router.set_window(window)
    qapp.processEvents()
    assert window.opened == [str(pdf_path)]
    qapp.sendEvent(qapp, OpenEvent(str(pdf_path.with_suffix(".PDF"))))
    qapp.processEvents()
    assert len(window.opened) == 2
    router.deleteLater()
    qapp.processEvents()


def test_native_shortcut_is_command_on_mac(qapp):
    if sys.platform == "darwin":
        assert "⌘" in shortcut_text("Ctrl+O")
    else:
        assert "Ctrl" in shortcut_text("Ctrl+O")


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires the real macOS Keychain")
def test_native_keychain_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    privacy._read_mac_key.cache_clear()
    identity = None
    try:
        value = privacy.protect("local native keychain test / 你好")
        identity = (tmp_path / "keychain-id").read_text().strip()
        privacy._read_mac_key.cache_clear()
        assert privacy.protect(value, True) == "local native keychain test / 你好"
        assert privacy._keychain().get_password(privacy.KEYCHAIN_SERVICE, identity)
    finally:
        privacy._read_mac_key.cache_clear()
        if identity:
            privacy._keychain().delete_password(privacy.KEYCHAIN_SERVICE, identity)
