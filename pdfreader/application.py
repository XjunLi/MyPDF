"""Finder file-open events and native shortcut labels."""
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication


def shortcut_text(shortcut):
    return QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)


class FileOpenRouter(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.window = None
        self.pending = []
        self.activation_requested = False
        self.opening = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.drain)
        app.installEventFilter(self)

    def set_window(self, window):
        self.window = window
        if self.activation_requested:
            self.timer.start(0)

    def request_open(self, paths):
        self.pending.extend(paths)
        self.activation_requested = True
        if self.window is not None and not self.opening:
            self.timer.start(0)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() or event.url().toLocalFile()
            if path and Path(path).suffix.lower() == ".pdf":
                self.request_open([path])
                return True
        return False

    def drain(self):
        if self.window is None or self.opening or not self.activation_requested:
            return
        if QApplication.activeModalWidget() is not None:
            self.timer.start(150)
            return
        paths, self.pending = self.pending, []
        self.activation_requested = False
        self.opening = True
        try:
            if paths:
                self.window.open_files(paths)
            state = self.window.windowState()
            if state & Qt.WindowState.WindowMinimized:
                self.window.setWindowState(state & ~Qt.WindowState.WindowMinimized)
            self.window.raise_()
            self.window.activateWindow()
        finally:
            self.opening = False
            if self.activation_requested:
                self.timer.start(0)
