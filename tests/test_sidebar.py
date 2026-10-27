import json
import sys
from pdfreader.app import MainWindow
from pdfreader.config import plugins


def test_sidebar_contains_only_ai_and_comments_and_does_not_load_terminal(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.show()
    try:
        assert window.right.count() == 2
        assert set(window.panel_actions) == {"ai", "comment"}
        assert not hasattr(window, "terminal")
        assert "pdfreader.terminal" not in sys.modules and "winpty" not in sys.modules
        for name, widget in (("ai", window.ai), ("comment", window.comments)):
            window.panel_actions[name].trigger()
            assert window.right.currentWidget() is widget and window.right.isVisible()
            assert window.panel_actions[name].isChecked()
            window.toggle_panel(None)
            assert not window.right.isVisible()
    finally:
        window.close()


def test_legacy_terminal_preset_is_ignored_without_deleting_user_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    folder = tmp_path / "plugins"
    folder.mkdir()
    disabled = folder / "old-terminal.json"
    old = json.dumps({"name": "CLI", "kind": "terminal", "command": ["cmd.exe"]})
    disabled.write_text(old, encoding="utf-8")
    (folder / "ai.json").write_text(json.dumps({"name": "Local model", "kind": "ai", "endpoint": "http://127.0.0.1:11434/v1", "model": "test"}), encoding="utf-8")
    assert [entry["name"] for entry in plugins()] == ["Local model"]
    assert disabled.read_text(encoding="utf-8") == old
