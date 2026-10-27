from pathlib import Path
import base64
import ctypes
import json
import os
from ctypes import wintypes
from .paths import data_root, legacy_data_root


class Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(value, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("本机加密保存目前仅支持 Windows。")
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
    if not isinstance(data, dict) or data.get("kind") not in ("terminal", "ai"):
        raise ValueError("插件必须是 kind 为 terminal 或 ai 的 JSON 清单。")
    if not isinstance(data.get("name"), str) or not 1 <= len(data["name"]) <= 60:
        raise ValueError("插件名称需要 1–60 个字符。")
    if data["kind"] == "terminal":
        command = data.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(v, str) and v and "\0" not in v for v in command):
            raise ValueError("terminal 插件的 command 应为命令和参数的数组。")
    else:
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
