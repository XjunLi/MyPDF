"""Coordinate local PDF ownership and verified, atomic file replacements."""
from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unicodedata

import pymupdf as fitz
from PySide6.QtCore import QLockFile

from .paths import canonical_document_path


class PdfError(Exception):
    pass


class FileConflict(PdfError):
    pass


def file_digest(path):
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return None
    with stream:
        return hashlib.file_digest(stream, "sha256").digest()


def acquire_lock(path, busy_message):
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)  # A live editor may keep a PDF open for days.
    if not lock.tryLock(0):
        if lock.error() == QLockFile.LockError.LockFailedError:
            raise FileConflict(busy_message)
        raise PdfError("无法创建 PDF 文件锁，请检查目录的写入权限。")
    return lock


class OpenFileLease:
    """Keep the pathname across replacements and the file ID across aliases."""
    def __init__(self):
        self.locks = {}

    def owns_identity(self, stat):
        return bool(stat.st_ino and f"file:{stat.st_dev}:{stat.st_ino}" in self.locks)

    @classmethod
    def acquire(cls, path, stat, *owners, case_probe=None):
        keys = ["path:" + canonical_document_path(path, case_probe=case_probe)]
        if stat is not None and stat.st_ino:
            keys.append(f"file:{stat.st_dev}:{stat.st_ino}")
        # Windows and macOS provide a per-user temp directory. Reading from
        # read-only media never requires creating a file beside the PDF.
        root = Path(tempfile.gettempdir()) / "PDFReader-open-files"
        root.mkdir(mode=0o700, exist_ok=True)
        result = cls()
        try:
            for key in keys:
                shared = next((owner.locks[key] for owner in owners if owner and key in owner.locks), None)
                if shared is None:
                    name = hashlib.sha256(os.fsencode(key)).hexdigest() + ".lock"
                    shared = acquire_lock(root / name, f"“{path.name}”已在另一个 PDF Reader 窗口打开。")
                result.locks[key] = shared
            return result
        except BaseException:
            result.release(*owners)
            raise

    def release(self, *keepers):
        for key, lock in self.locks.items():
            if not any(keeper and keeper.locks.get(key) is lock for keeper in keepers):
                lock.unlock()
        self.locks.clear()


def verified_file(target, expected, *owners, case_probe=None):
    """Return an owned snapshot identity only if the on-disk revision still matches."""
    existing = None
    try:
        try:
            stream = target.open("rb")
        except FileNotFoundError:
            existing = OpenFileLease.acquire(target, None, *owners, case_probe=case_probe)
            actual = None
        else:
            with stream:
                before = os.fstat(stream.fileno())
                existing = OpenFileLease.acquire(target, before, *owners)
                actual = hashlib.file_digest(stream, "sha256").digest()
                try:
                    after = target.stat()
                except FileNotFoundError:
                    raise FileConflict(f"“{target.name}”在保存期间已被移动或删除。") from None
                if (not os.path.samestat(before, after) or before.st_size != after.st_size
                        or before.st_mtime_ns != after.st_mtime_ns):
                    raise FileConflict(f"“{target.name}”在保存期间发生了变化。")
        if actual != expected:
            raise FileConflict(f"“{target.name}”在磁盘上已被修改、移动或替换。")
        return existing
    except BaseException:
        if existing is not None:
            existing.release(*owners)
        raise


@contextmanager
def checked_destination(target, expected, *owners, case_probe=None):
    # Serialize the last check and rename by directory entry, including on
    # shared volumes. The short-lived sibling lock also covers a new filename.
    name = unicodedata.normalize("NFC", target.name).casefold()
    token = hashlib.sha256(os.fsencode(name)).hexdigest()[:32]
    lock = acquire_lock(target.with_name(f".pdfreader-{token}.lock"), f"另一个保存操作正在写入“{target.name}”。")
    existing = None
    try:
        existing = verified_file(target, expected, *owners, case_probe=case_probe)
        yield
    finally:
        if existing is not None:
            existing.release(*owners)
        lock.unlock()


@contextmanager
def staged_file(target, suffix):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".pdfreader-", suffix=suffix, dir=target.parent)
    os.close(fd)
    temp = Path(filename)
    try:
        yield temp
    finally:
        temp.unlink(missing_ok=True)


def publish_new_file(temp, target):
    if os.name == "nt":
        os.rename(temp, target)  # MoveFileEx without REPLACE_EXISTING.
        return
    if sys.platform == "darwin":
        import ctypes
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(os.fsencode(temp), os.fsencode(target), 0x00000004) == 0:  # RENAME_EXCL
            return
        error = ctypes.get_errno()
        if error not in (errno.ENOTSUP, errno.ENOSYS, errno.EINVAL):
            raise OSError(error, os.strerror(error), str(target))
    # A same-directory hard link also publishes without replacing any entry.
    # staged_file removes the other name. Never publish an empty placeholder.
    try:
        os.link(temp, target)
    except OSError as exc:
        if exc.errno not in (errno.ENOTSUP, errno.EPERM, errno.EMLINK, errno.ENOSYS):
            raise
        raise PdfError("此位置无法安全创建新文件，请选择另一个保存位置。") from exc


def install_output(temp, target, expected):
    """Publish under the destination lock. A new name must still be free at commit."""
    if expected is not None:
        if os.name == "posix":
            os.chmod(temp, target.stat().st_mode & 0o777)
        os.replace(temp, target)
        return
    try:
        publish_new_file(temp, target)
    except FileExistsError:
        raise FileConflict(f"“{target.name}”在保存期间已被其他程序创建。") from None


@contextmanager
def export_destination(path):
    """Write image or text exports without truncating a PDF via an alias."""
    path = Path(path)
    target = path.resolve()
    if ".pdf" in (path.suffix.lower(), target.suffix.lower()):
        raise PdfError("导出文字或图片请使用其他文件名，不能覆盖 PDF。")
    expected = file_digest(target)
    with staged_file(target, path.suffix) as temp:
        yield temp
        with checked_destination(target, expected, case_probe=temp):
            install_output(temp, target, expected)


def copy_saved_revision(document, temp, source, expected):
    original = document.stream
    if original is not None and hashlib.sha256(original).digest() == expected:
        temp.write_bytes(original)
        return True
    # After an edit and save, MuPDF retains its initial input stream. Copy the
    # last committed file only if the bytes actually copied match our revision.
    try:
        stream = source.open("rb")
    except OSError:
        return False
    with stream, temp.open("wb") as output:
        shutil.copyfileobj(stream, output)
    return file_digest(temp) == expected


def write_pdf(document, target, expected, *, owner=None, keep_open=False, password="", clean_source=None):
    """Verify on disk before committing. Acquire the replacement's lease before rename."""
    with staged_file(target, ".pdf") as temp:
        if clean_source is None or not copy_saved_revision(document, temp, *clean_source):
            # Levels 2 and 3 renumber the live document's xrefs, invalidating
            # comment and form selections even if the subsequent rename fails.
            document.save(temp, garbage=1 if keep_open else 3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        with fitz.open(temp) as check:
            if ((check.needs_pass and not check.authenticate(password)) or not check.is_pdf
                    or not len(check) or len(check) != len(document)):
                raise PdfError("保存的 PDF 未通过完整性验证。")
        with temp.open("r+b") as stream:
            os.fsync(stream.fileno())
            digest = hashlib.file_digest(stream, "sha256").digest() if keep_open else None
            identity = os.fstat(stream.fileno())
        replacement = OpenFileLease.acquire(target, identity, owner, case_probe=temp) if keep_open else None
        try:
            with checked_destination(target, expected, owner, replacement, case_probe=temp):
                install_output(temp, target, expected)
        except BaseException:
            if replacement is not None:
                replacement.release(owner)
            raise
        return digest, replacement
