import os
import shutil
import threading
import subprocess
import time
import ctypes
import pyte

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QLabel, QComboBox, QApplication)
from .icons import icon_button


def prepare_hidden_console():
    """Create only our own hidden console before the ConPTY wrapper initializes.

    winpty-rs 1.0.x unwraps ShowWindow's false return, although false also means
    the console was already hidden. This affects windowed EXEs launched hidden.
    Allocating here lets the wrapper reuse the console without that code path.
    Never hide a console inherited by a source/CLI launch.
    """
    if os.name != "nt":
        return
    kernel = ctypes.windll.kernel32
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    if kernel.AllocConsole():
        handle = kernel.GetConsoleWindow()
        ctypes.windll.user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        ctypes.windll.user32.ShowWindow(handle, 0)


class TerminalEvents(QObject):
    data = Signal(int, str)
    ended = Signal(int, str)


class TerminalDisplay(QPlainTextEdit):
    input = Signal(str)
    resized = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont("Consolas", 10))
        self.setStyleSheet("QPlainTextEdit { font-family:'Consolas'; font-size:12px; background:#20252e; color:#d8dfe8; border:0; border-radius:5px; padding:8px; selection-background-color:#3c567e; }")
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setPlaceholderText("点击上方运行按钮启动终端。")

    def keyPressEvent(self, event):
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and event.key() == Qt.Key.Key_C and self.textCursor().hasSelection():
            self.copy()
            return
        if ctrl and event.key() == Qt.Key.Key_V:
            self.input.emit(QApplication.clipboard().text().replace("\n", "\r"))
            return
        keys = {Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r", Qt.Key.Key_Backspace: "\x7f",
                Qt.Key.Key_Tab: "\t", Qt.Key.Key_Escape: "\x1b", Qt.Key.Key_Up: "\x1b[A",
                Qt.Key.Key_Down: "\x1b[B", Qt.Key.Key_Right: "\x1b[C", Qt.Key.Key_Left: "\x1b[D",
                Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F", Qt.Key.Key_Delete: "\x1b[3~",
                Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~"}
        if ctrl and Qt.Key.Key_A <= event.key() <= Qt.Key.Key_Z:
            self.input.emit(chr(event.key() - Qt.Key.Key_A + 1))
        elif event.key() in keys:
            self.input.emit(keys[event.key()])
        elif event.text():
            self.input.emit(event.text())
        else:
            super().keyPressEvent(event)

    def inputMethodEvent(self, event):
        if event.commitString():
            self.input.emit(event.commitString())
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        fm = self.fontMetrics()
        cols = max(20, int((self.viewport().width() - 18) / fm.horizontalAdvance("M")))
        rows = max(8, int((self.viewport().height() - 18) / fm.lineSpacing()))
        self.resized.emit(rows, cols)


class TerminalPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.last_error = ""
        self.start_phase = "idle"
        self.generation = 0
        self.rows, self.cols = 24, 80
        self.screen = pyte.HistoryScreen(self.cols, self.rows, history=1000)
        self.stream = pyte.Stream(self.screen)
        self.events = TerminalEvents()
        self.events.data.connect(self.receive)
        self.events.ended.connect(self.ended)
        self.cwd = os.getcwd()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 10)
        self.command = QComboBox()
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        self.command.addItem("PowerShell", [shell, "-NoLogo", "-NoProfile"])
        self.command.addItem("命令提示符", [os.environ.get("COMSPEC", "cmd.exe")])
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        self.description = QLabel("启动后运行本机命令。终端程序可以访问文件和网络。")
        self.description.setWordWrap(True)
        self.description.setObjectName("muted")
        self.description.hide()
        self.start_button = icon_button("play", "启动终端")
        self.stop_button = icon_button("stop", "结束进程")
        self.interrupt_button = icon_button("close", "中断 · Ctrl+C")
        row.addWidget(self.start_button)
        row.addWidget(self.interrupt_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.display = TerminalDisplay()
        layout.addWidget(self.display, 1)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.interrupt_button.clicked.connect(lambda: self.write("\x03"))
        self.display.input.connect(self.write)
        self.display.resized.connect(self.resize_terminal)
        self.command.currentIndexChanged.connect(self.show_command)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.redraw)
        self.show_command()

    def show_command(self):
        argv = self.command.currentData() or []
        self.description.setText("命令：" + subprocess.list2cmdline(argv))
        self.command.setToolTip(subprocess.list2cmdline(argv))

    def add_plugins(self, entries):
        while self.command.count() > 2:
            self.command.removeItem(2)
        for entry in entries:
            if entry["kind"] == "terminal":
                self.command.addItem(entry["name"], entry["command"])

    def start(self):
        if self.process is not None:
            return
        try:
            self.last_error = ""
            self.start_phase = "import"
            from winpty import PTY
            self.screen.reset()
            argv = self.command.currentData()
            command = shutil.which(argv[0]) or argv[0]
            self.start_phase = "create"
            prepare_hidden_console()
            self.process = PTY(self.cols, self.rows)
            self.start_phase = "spawn"
            self.process.spawn(subprocess.list2cmdline([command] + argv[1:]), cwd=self.cwd)
            self.start_phase = "spawned"
            process = self.process
            generation = self.generation
            self.start_button.setEnabled(False)
            self.command.setEnabled(False)
            self.display.setFocus()

            def read():
                message = "进程已退出。"
                try:
                    while process.isalive():
                        data = process.read(blocking=False)
                        if generation != self.generation:
                            return
                        if data:
                            self.events.data.emit(generation, data)
                        else:
                            time.sleep(.015)
                    code = process.get_exitstatus()
                    if code is not None:
                        message = f"进程已退出，代码 {code}。"
                except Exception as exc:
                    message = f"终端读取失败：{exc}"
                finally:
                    if generation == self.generation:
                        try:
                            self.events.ended.emit(generation, message)
                        except RuntimeError:
                            pass
            threading.Thread(target=read, daemon=True, name="PDF Reader-Terminal").start()
            self.start_phase = "reading"
        except BaseException as exc:
            # PyO3 native initialization errors may use PanicException, which
            # does not inherit Exception. Do not let Qt silently swallow it.
            if not isinstance(exc, Exception) and type(exc).__name__ != "PanicException":
                raise
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.display.setPlainText(f"无法启动终端：{exc}")
            self.process = None

    def receive(self, generation, text):
        if generation != self.generation:
            return
        # Respond to terminal cursor-position/device queries used by shells.
        if "\x1b[6n" in text:
            self.write(f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R")
        self.stream.feed(text)
        if not self.timer.isActive():
            self.timer.start(35)

    def redraw(self):
        scroll = self.display.verticalScrollBar()
        at_bottom = scroll.maximum() - scroll.value() < 12
        old = scroll.value()
        history = ["".join(line[x].data for x in range(self.cols)) for line in self.screen.history.top]
        text = "\n".join(history + self.screen.display)
        self.display.setPlainText(text)
        if at_bottom:
            scroll.setValue(scroll.maximum())
        else:
            scroll.setValue(old)

    def write(self, text):
        try:
            if self.process and self.process.isalive():
                self.process.write(text)
        except (OSError, EOFError):
            self.ended(self.generation, "进程已退出。")

    def resize_terminal(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.screen.resize(lines=rows, columns=cols)
        if self.process:
            try:
                self.process.set_size(cols, rows)
            except Exception:
                pass

    def ended(self, generation, message):
        if generation != self.generation:
            return
        self.process = None
        self.start_phase = "exited"
        self.start_button.setEnabled(True)
        self.command.setEnabled(True)
        self.description.setText(message)
        self.display.appendPlainText("\n[" + message + "]")

    def stop(self):
        self.generation += 1
        process, self.process = self.process, None
        if process and process.isalive():
            try:
                # Kill only this pane's process tree, including CLI children.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                process.cancel_io()
            except Exception:
                pass
        self.start_button.setEnabled(True)
        self.command.setEnabled(True)
