from pathlib import Path
import os
import sys


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def app_data_parent() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def data_root() -> Path:
    override = os.environ.get("PDFREADER_DATA_DIR") or os.environ.get("SLATEPDF_DATA_DIR")
    root = Path(override) if override else app_data_parent() / "PDFReader"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def legacy_data_root():
    if os.environ.get("PDFREADER_DATA_DIR") or os.environ.get("SLATEPDF_DATA_DIR"):
        return None
    return app_data_parent() / "SlatePDF"


def tessdata_path() -> Path:
    return resource_root() / "assets" / "tessdata"
