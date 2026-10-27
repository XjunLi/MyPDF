from pathlib import Path
import os
import sys


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def data_root() -> Path:
    override = os.environ.get("PDFREADER_DATA_DIR") or os.environ.get("SLATEPDF_DATA_DIR")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFReader"
    root.mkdir(parents=True, exist_ok=True)
    return root


def legacy_data_root():
    if os.environ.get("PDFREADER_DATA_DIR") or os.environ.get("SLATEPDF_DATA_DIR"):
        return None
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SlatePDF"


def tessdata_path() -> Path:
    return resource_root() / "assets" / "tessdata"
