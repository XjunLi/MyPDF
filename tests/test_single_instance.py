import os
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget

from pdfreader.application import FileOpenRouter
from pdfreader.single_instance import ReaderInstance


def test_two_simultaneous_starts_elect_one_owner_and_recover_after_crash(qapp, tmp_path):
    # Each child uses a real event loop, process ID, and OS socket. Both wait at
    # a start gate so a pre-existing listener is not required for this scenario.
    helper = """from pathlib import Path
import sys, time
from PySide6.QtCore import QCoreApplication
from pdfreader.single_instance import ReaderInstance
root = Path(sys.argv[1])
label = sys.argv[2]
app = QCoreApplication([])
instance = ReaderInstance(root)
instance.openRequested.connect(lambda paths: (root / 'delivered').write_text(paths[0], encoding='utf-8'))
(root / ('ready-' + label)).touch()
while not (root / 'start').exists():
    time.sleep(.01)
if instance.start_or_forward([root / (label + '.pdf')]):
    (root / ('owner-' + label)).touch()
    app.exec()
"""
    children = {}
    def until(check):
        deadline = time.monotonic() + 12
        while not check() and time.monotonic() < deadline:
            time.sleep(.02)
        assert check(), {key: proc.poll() for key, proc in children.items()}
    try:
        for key in ("left", "right"):
            children[key] = subprocess.Popen([sys.executable, "-c", helper, str(tmp_path), key],
                                             cwd=Path(__file__).resolve().parents[1])
        until(lambda: len(list(tmp_path.glob("ready-*"))) == 2)
        (tmp_path / "start").touch()
        until(lambda: (tmp_path / "delivered").exists() and any(p.poll() == 0 for p in children.values()))
        owners = list(tmp_path.glob("owner-*"))
        assert len(owners) == 1
        key = owners[0].name.removeprefix("owner-")
        other = "left" if key == "right" else "right"
        assert (tmp_path / "delivered").read_text(encoding="utf-8") == str(tmp_path / (other + ".pdf"))
        children[key].kill()
        children[key].wait(timeout=5)
        next_owner = ReaderInstance(tmp_path)
        try:
            assert next_owner.start_or_forward([])
        finally:
            next_owner.close()
    finally:
        for process in children.values():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_secondary_launch_waits_for_modal_then_restores_maximized_window(qapp, qt_wait, tmp_path):
    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.opened = []
            self.activations = 0
        def open_files(self, paths):
            self.opened.extend(paths)
        def activateWindow(self):
            self.activations += 1
            super().activateWindow()
    router = FileOpenRouter(qapp)
    window = Window()
    window.showMaximized()
    router.set_window(window)
    instance = ReaderInstance(tmp_path)
    instance.openRequested.connect(router.request_open)
    requests = []
    instance.openRequested.connect(requests.append)
    dialog = QDialog(window)
    try:
        assert instance.start_or_forward([])
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.show()
        qapp.processEvents()
        def launch(args):
            entry = Path(__file__).resolve().parents[1] / "main.py"
            child = subprocess.Popen([sys.executable, str(entry), *args], cwd=tmp_path,
                env=dict(os.environ, PDFREADER_DATA_DIR=str(tmp_path), QT_QPA_PLATFORM="offscreen"))
            try:
                qt_wait(lambda: child.poll() is not None, 15000)
                assert child.returncode == 0
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
        launch(["relative name.PDF"])
        assert requests == [[str(tmp_path / "relative name.PDF")]]
        assert window.opened == [] and router.activation_requested
        dialog.close()
        qt_wait(lambda: window.opened == requests[0])
        assert window.isMaximized()
        window.setWindowState(window.windowState() | Qt.WindowState.WindowMinimized)
        launch([])
        qt_wait(lambda: not window.isMinimized() and window.activations >= 2)
        assert requests[-1] == [] and window.isMaximized() and len(window.opened) == 1
    finally:
        instance.close()
        qapp.removeEventFilter(router)
        router.deleteLater()
        dialog.close()
        window.close()
