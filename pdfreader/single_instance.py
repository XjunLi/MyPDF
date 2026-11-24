"""Forward desktop launches to the owning GUI over local OS IPC."""
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QLockFile, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class ReaderInstance(QObject):
    openRequested = Signal(list)
    MAX_REQUEST = 4 * 1024 * 1024

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        key = os.fsencode(os.path.normcase(str(profile.resolve())))
        self.name = "mypdf-" + hashlib.sha256(key).hexdigest()[:24]
        self.lock = QLockFile(str(profile / "reader-instance.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.accept_connections)

    def start_or_forward(self, paths):
        paths = [str(Path(path).absolute()) for path in paths]
        payload = json.dumps(paths, ensure_ascii=True).encode("ascii") + b"\n"
        if len(payload) > self.MAX_REQUEST:
            raise RuntimeError("一次打开的文件过多，请分批打开。")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.lock.tryLock(0):
                # Only the elected process may remove a socket left by a crash.
                QLocalServer.removeServer(self.name)
                if self.server.listen(self.name):
                    return True
                self.lock.unlock()
                raise RuntimeError("无法启动本机窗口通信：" + self.server.errorString())
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise RuntimeError("无法锁定阅读器配置目录，请检查写入权限。")
            socket = QLocalSocket()
            try:
                socket.connectToServer(self.name)
                if socket.waitForConnected(200):
                    if sys.platform == "win32":
                        # Explorer allowed this new process to take focus. Pass
                        # that permission to the existing window before waking it.
                        import ctypes
                        owner_pid, _, _ = self.lock.getLockInfo()
                        if owner_pid > 0:
                            ctypes.windll.user32.AllowSetForegroundWindow(owner_pid)
                    if socket.write(payload) != len(payload):
                        raise RuntimeError("打开请求未能完整发送给已有窗口。")
                    socket.flush()
                    while not socket.canReadLine():
                        remaining = int(1000 * (deadline - time.monotonic()))
                        if remaining <= 0 or not socket.waitForReadyRead(remaining):
                            break
                    if bytes(socket.readLine()) == b"OK\n":
                        return False
            finally:
                socket.abort()
            time.sleep(.04)  # The owner may still be starting, or exiting.
        raise RuntimeError("已有 PDF Reader 窗口暂未确认打开请求，请稍后在原窗口重试。")

    def accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(self.MAX_REQUEST + 1)
            socket.disconnected.connect(socket.deleteLater)
            timeout = QTimer(socket)
            timeout.setSingleShot(True)
            timeout.timeout.connect(socket.abort)
            timeout.start(10000)
            socket.readyRead.connect(partial(self.read_request, socket))
            self.read_request(socket)

    def read_request(self, socket):
        if socket.bytesAvailable() > self.MAX_REQUEST:
            socket.abort()
            return
        if not socket.canReadLine():
            return
        try:
            paths = json.loads(bytes(socket.readLine()))
            if not isinstance(paths, list) or not all(isinstance(path, str) and "\0" not in path
                    and Path(path).is_absolute() and Path(path).suffix.lower() == ".pdf" for path in paths):
                raise ValueError("Invalid file-open request")
        except (ValueError, UnicodeError, RecursionError):
            socket.abort()
            return
        self.openRequested.emit(paths)
        socket.write(b"OK\n")
        socket.disconnectFromServer()

    def close(self):
        if self.lock.isLocked():
            self.server.close()
            self.lock.unlock()
