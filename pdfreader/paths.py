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


def canonical_document_path(value, *, case_probe=None):
    path = Path(value).resolve()
    if sys.platform == "darwin":
        # A staged sibling can prove the destination directory's case behavior
        # before the target exists. This also keeps its lock key stable on rename.
        probe = case_probe or path
        alternate = probe.with_name(probe.name.swapcase())
        try:
            if alternate.name != probe.name and probe.stat().st_ino and probe.samefile(alternate):
                import unicodedata
                return unicodedata.normalize("NFC", str(path)).casefold()
        except OSError:
            pass
    return os.path.normcase(str(path))


def same_document_path(first, second):
    if first is None or second is None:
        return False
    if canonical_document_path(first) == canonical_document_path(second):
        return True
    try:
        identity = Path(first).stat()
        # Some virtual drives report zero for every file ID. Their different
        # filenames must remain independent documents.
        return bool(identity.st_ino and os.path.samestat(identity, Path(second).stat()))
    except OSError:
        return False
