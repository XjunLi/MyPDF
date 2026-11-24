import json
import pytest
from pdfreader.config import Settings, plugins, protect


def test_unrelated_setting_does_not_erase_a_temporarily_unreadable_saved_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ai":{"remember_key":True, "protected_key":"opaque-test-value"}}), encoding="utf-8")
    def unavailable(*args, **kwargs):
        raise RuntimeError("temporary key store outage")
    monkeypatch.setattr("pdfreader.config.protect", unavailable)
    settings = Settings()
    settings.values["ink_width"] = 3
    settings.save()
    assert json.loads(path.read_text(encoding="utf-8"))["ai"]["protected_key"] == "opaque-test-value"
    settings.values["ai"]["remember_key"] = False
    settings.save()
    assert "protected_key" not in json.loads(path.read_text(encoding="utf-8"))["ai"]


def test_stale_settings_merge_distinct_changes_and_preserve_conflicting_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path))
    Settings().save()
    first, second = Settings(), Settings()
    first.values["ai"] = {"model": "first-model", "remember_key": True, "key": "synthetic-test-key"}
    first.save()
    second.values["ink_width"] = 5
    second.save()
    saved = Settings()
    assert saved.values["ai"]["key"] == "synthetic-test-key" and saved.values["ink_width"] == 5
    second.values["ai"] = {"model": "second-model"}
    with pytest.raises(ValueError, match="另一窗口"):
        second.save()
    assert Settings().values["ai"]["model"] == "first-model"
    assert "synthetic-test-key" not in second.path.read_text(encoding="utf-8")


def test_upgrade_keeps_settings_key_and_plugins_without_changing_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("PDFREADER_DATA_DIR", raising=False)
    monkeypatch.delenv("SLATEPDF_DATA_DIR", raising=False)
    monkeypatch.setattr("pdfreader.paths.app_data_parent", lambda: tmp_path)
    old = tmp_path / "SlatePDF"
    (old / "plugins").mkdir(parents=True)
    original = {"auto_ocr": False, "language": "eng", "ai": {"enabled": False, "model": "model-test", "remember_key": True, "protected_key": protect("local-test-key")}}
    saved = json.dumps(original)
    (old / "settings.json").write_text(saved, encoding="utf-8")
    (old / "plugins" / "test.json").write_text(json.dumps({"name": "Local AI", "kind": "ai", "endpoint": "http://127.0.0.1:11434/v1", "model": "local-model"}), encoding="utf-8")
    settings = Settings()
    assert settings.values["ai"]["key"] == "local-test-key"
    assert settings.values["auto_ocr"] is True
    assert settings.values["automatic_ocr_v3"] is True
    assert plugins()[0]["name"] == "Local AI"
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
