import json
import pytest
from pdfreader.config import Settings, plugins, protect


def test_upgrade_keeps_settings_key_and_plugins_without_changing_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("PDFREADER_DATA_DIR", raising=False)
    monkeypatch.delenv("SLATEPDF_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    old = tmp_path / "SlatePDF"
    (old / "plugins").mkdir(parents=True)
    original = {"auto_ocr": False, "language": "eng", "ai": {"enabled": False, "model": "model-test", "remember_key": True, "protected_key": protect("local-test-key")}}
    saved = json.dumps(original)
    (old / "settings.json").write_text(saved, encoding="utf-8")
    (old / "plugins" / "test.json").write_text(json.dumps({"name": "CLI", "kind": "terminal", "command": ["cmd.exe"]}), encoding="utf-8")
    settings = Settings()
    assert settings.values["ai"]["key"] == "local-test-key"
    assert settings.values["auto_ocr"] is True
    assert settings.values["automatic_ocr_v3"] is True
    assert plugins()[0]["name"] == "CLI"
    settings.save()
    assert settings.path.parent.name == "PDFReader"
    assert "local-test-key" not in settings.path.read_text()
    assert (old / "settings.json").read_text() == saved


def test_explicit_ocr_preference_after_upgrade_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    settings = Settings()
    settings.values["auto_ocr"] = False
    settings.save()
    assert Settings().values["auto_ocr"] is False


@pytest.mark.parametrize("content", ['[]', '{"ai":null}', '{"language":"missing"}', 'invalid json'])
def test_malformed_settings_do_not_crash_startup(tmp_path, monkeypatch, content):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(content, encoding="utf-8")
    settings = Settings()
    assert isinstance(settings.values["ai"], dict)
    assert settings.values["language"] in ("eng", "chi_sim", "eng+chi_sim")
