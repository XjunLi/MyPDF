from pathlib import Path
from copy import deepcopy
import json
from PySide6.QtCore import QIODevice, QLockFile, QSaveFile
from .paths import data_root, legacy_data_root
from .privacy import protect


class Settings:
    def __init__(self):
        self.path = data_root() / "settings.json"
        self._on_disk = {}
        self.values = {"auto_ocr": True, "automatic_ocr_v3": True, "language": "eng+chi_sim", "ai": {}}
        source = self.path
        legacy = legacy_data_root()
        if not source.exists() and legacy is not None:
            source = legacy / "settings.json"
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return
            if source == self.path:
                self._on_disk = deepcopy(loaded)
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
        finally:
            self._previous = deepcopy(self.values)

    @staticmethod
    def _stored_ai(config):
        ai = dict(config)
        key = ai.pop("key", None)
        if not ai.get("remember_key") or key == "":
            ai.pop("protected_key", None)
        elif key:
            ai["protected_key"] = protect(key)
        return ai

    def save(self):
        lock = QLockFile(str(self.path.with_suffix(".lock")))
        if not lock.tryLock(0):
            raise OSError("无法锁定设置文件，请稍后重试。")
        try:
            try:
                latest = json.loads(self.path.read_bytes())
            except FileNotFoundError:
                if self._on_disk:
                    raise ValueError("设置文件已被移动或删除，请重新打开阅读器。") from None
                latest = {}
            if not isinstance(latest, dict):
                raise ValueError("设置文件格式无效，原文件已保留。")
            missing = object()
            fields = {k for k, value in self.values.items()
                      if value != self._previous.get(k, missing) or (k not in latest and k not in self._on_disk)}
            if not latest.get("automatic_ocr_v3") and self.values.get("automatic_ocr_v3"):
                fields.update(("automatic_ocr_v3", "auto_ocr"))
            if isinstance(latest.get("ai"), dict) and "key" in latest["ai"]:
                fields.add("ai")
            changes = {}
            for field in fields:
                value = self._stored_ai(self.values[field]) if field == "ai" else self.values[field]
                base = self._on_disk.get(field, self._previous.get(field, missing))
                disk = latest.get(field, missing)
                if (disk is not missing or field in self._on_disk) and disk != base and disk != value:
                    raise ValueError("另一窗口已修改同一项设置，已保留磁盘版本。请重新打开阅读器后修改。")
                changes[field] = value
            if changes:
                latest.update(changes)
                data = json.dumps(latest, indent=2, ensure_ascii=False).encode("utf-8")
                output = QSaveFile(str(self.path))
                if not output.open(QIODevice.OpenModeFlag.WriteOnly) or output.write(data) != len(data) or not output.commit():
                    raise OSError("无法保存设置：" + output.errorString())
                self._on_disk.update(deepcopy(changes))
                self._previous = deepcopy(self.values)
        finally:
            lock.unlock()


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
