"""Finder file-open events and native shortcut labels."""
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QKeySequence


def shortcut_text(shortcut):
    return QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)


class FileOpenRouter(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.window = None
        self.pending = []
        app.installEventFilter(self)

    def set_window(self, window):
        self.window = window
        QTimer.singleShot(0, self.drain)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() or event.url().toLocalFile()
            if path and Path(path).suffix.lower() == ".pdf":
                self.pending.append(path)
                QTimer.singleShot(0, self.drain)
                return True
        return False

    def drain(self):
        if self.window is not None and self.pending:
            paths, self.pending = self.pending, []
            self.window.open_files(paths)
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
