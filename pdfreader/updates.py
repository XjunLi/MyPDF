"""Update check against the project's GitHub repository.

Only commit or release version information is read; no document, chat, path
or key is sent. A source checkout updates with git, packaged apps open the
release page.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from PySide6.QtCore import QObject, Signal
from . import __version__

ROOT = Path(__file__).resolve().parent.parent
RELEASES_URL = "https://github.com/XjunLi/MyPDF/releases/latest"
RELEASE_API = "https://api.github.com/repos/XjunLi/MyPDF/releases/latest"


def source_checkout(root=ROOT):
    return not getattr(sys, "frozen", False) and (Path(root) / ".git").exists()


def run(args, root, timeout=60):
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=timeout,
                            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"{args[0]} 执行失败。")
    return result.stdout.strip()


def is_newer(tag, current=__version__):
    try:
        return tuple(map(int, tag.lstrip("v").split("."))) > tuple(map(int, current.split(".")))
    except ValueError:
        return False


def check(root=ROOT):
    """Return a short description of the available update, or None."""
    if source_checkout(root):
        # ponytail: follows origin/main only; other branches or remotes need a setting.
        run(["git", "fetch", "--quiet", "origin", "main"], root)
        count = int(run(["git", "rev-list", "--count", "HEAD..origin/main"], root))
        return f"GitHub 有 {count} 个新提交" if count else None
    request = Request(RELEASE_API, headers={"User-Agent": "PDF Reader", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=15) as response:
        tag = json.loads(response.read(1024 * 1024)).get("tag_name", "")
    return f"新版本 {tag} 可下载" if is_newer(tag) else None


def apply(root=ROOT):
    """Fast-forward a source checkout to origin/main and sync changed dependencies."""
    before = run(["git", "rev-parse", "HEAD"], root)
    run(["git", "merge", "--ff-only", "origin/main"], root)
    lock = "dependency-lock-macos.txt" if sys.platform == "darwin" else "dependency-lock.txt"
    if run(["git", "diff", "--name-only", before, "HEAD", "--", lock], root):
        command = [sys.executable, "-m", "pip", "install", "-q", "-r", lock]
        if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode and shutil.which("uv"):
            command = ["uv", "pip", "install", "-q", "--python", sys.executable, "-r", lock]
        run(command, root, timeout=900)


class Background(QObject):
    """Run one blocking call off the GUI thread and report (result, error)."""
    finished = Signal(object, str)

    def __init__(self, function, callback, parent=None):
        super().__init__(parent)
        self.finished.connect(callback)
        threading.Thread(target=self._run, args=(function,), daemon=True, name="PDF Reader-update").start()

    def _run(self, function):
        try:
            result, error = function(), ""
        except Exception as exc:
            result, error = None, str(exc) or type(exc).__name__
        try:
            self.finished.emit(result, error)
        except RuntimeError:
            pass  # The window closed first.
