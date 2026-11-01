from pathlib import Path
import json
import os
from .paths import data_root, legacy_data_root
from .privacy import protect


class Settings:
    def __init__(self):
        self.path = data_root() / "settings.json"
        self.values = {"auto_ocr": True, "automatic_ocr_v3": True, "language": "eng+chi_sim", "ai": {}}
        source = self.path
        legacy = legacy_data_root()
        if not source.exists() and legacy is not None:
            source = legacy / "settings.json"
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return
            self.values.update(loaded)
            if not loaded.get("automatic_ocr_v3"):
                self.values["auto_ocr"] = True
                self.values["automatic_ocr_v3"] = True
            if not isinstance(self.values.get("ai"), dict):
                self.values["ai"] = {}
            if self.values.get("language") not in ("eng+chi_sim", "eng", "chi_sim"):
                self.values["language"] = "eng+chi_sim"
            ai = self.values.setdefault("ai", {})
            if ai.get("protected_key"):
                ai["key"] = protect(ai["protected_key"], decrypt=True)
        except (OSError, ValueError, RuntimeError):
            pass

    def save(self):
        values = dict(self.values)
        ai = dict(values.get("ai", {}))
        key = ai.pop("key", "")
        ai.pop("protected_key", None)
        if ai.get("remember_key") and key:
            ai["protected_key"] = protect(key)
        values["ai"] = ai
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)


def validate_plugin(data):
    if not isinstance(data, dict) or data.get("kind") != "ai":
        raise ValueError("当前仅支持 kind 为 ai 的服务预设；终端插件已暂时停用。")
    if not isinstance(data.get("name"), str) or not 1 <= len(data["name"]) <= 60:
        raise ValueError("插件名称需要 1–60 个字符。")
    from .ai import validate_endpoint
    validate_endpoint(data.get("endpoint", ""))
    if not isinstance(data.get("model"), str):
        raise ValueError("AI 插件需要 model 字段。")
    return data


def plugins():
    directory = data_root() / "plugins"
    directory.mkdir(exist_ok=True)
    result, names = [], set()
    paths = sorted(directory.glob("*.json"))
    legacy = legacy_data_root()
    if legacy is not None:
        paths += sorted((legacy / "plugins").glob("*.json"))
    for path in paths:
        try:
            entry = validate_plugin(json.loads(path.read_text(encoding="utf-8")))
            identity = entry["kind"], entry["name"]
            if identity not in names:
                result.append(entry)
                names.add(identity)
        except (ValueError, OSError):
            continue
    return result
