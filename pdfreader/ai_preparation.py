"""Local, cancellable attachment preparation in an isolated MuPDF process."""
import base64
import json
from pathlib import Path
import sys
from PySide6.QtCore import QObject, Signal, QProcess, QTemporaryDir, QTimer
from .paths import data_root, resource_root


class AttachmentJob(QObject):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, snapshot, metadata, parent=None):
        super().__init__(parent)
        self.snapshot, self.metadata = snapshot, metadata
        self.cancelled, self.done = False, False
        self.directory = None
        self.buffer = ""
        self.last_progress = None
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.finished.connect(self.completed)
        self.process.errorOccurred.connect(self.process_error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120_000)
        self.timer.timeout.connect(self.timed_out)
        self.poller = QTimer(self)
        self.poller.setInterval(150)
        self.poller.timeout.connect(self.poll_progress)

    def start(self):
        try:
            self.directory = QTemporaryDir(str(data_root() / "attachment-XXXXXX"))
            if not self.directory.isValid():
                raise OSError("无法创建本机附件临时目录。")
            path = Path(self.directory.path())
            (path / "input.pdf").write_bytes(self.snapshot)
            self.snapshot = None
            (path / "input.json").write_text(json.dumps(self.metadata, ensure_ascii=False), encoding="utf-8")
            args = ["--attachment-worker", str(path)]
            if not getattr(sys, "frozen", False):
                args.insert(0, str(resource_root() / "main.py"))
            self.process.setProgram(sys.executable)
            self.process.setArguments(args)
            self.process.start()
            self.timer.start()
            self.poller.start()
        except Exception as exc:
            self.cleanup()
            self.failed.emit(str(exc))

    def read_progress(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            try:
                value = json.loads(line)
                self.report_progress(value)
            except (ValueError, KeyError, TypeError):
                continue

    def report_progress(self, value):
        current = value["done"], value["total"]
        if not self.cancelled and current != self.last_progress:
            self.last_progress = current
            self.progress.emit(f"正在准备文档 · {current[0]} / {current[1]} 页")

    def poll_progress(self):
        # PyInstaller's windowed executable may have no Python stdout stream.
        if self.directory and not self.done:
            try:
                value = json.loads((Path(self.directory.path()) / "progress.json").read_text(encoding="utf-8"))
                self.report_progress(value)
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def completed(self, code, *_):
        if self.done:
            return
        try:
            if self.cancelled:
                return
            path = Path(self.directory.path())
            if code:
                error = path / "error.txt"
                raise ValueError(error.read_text(encoding="utf-8") if error.exists() else "附件处理进程异常结束。")
            result = json.loads((path / "result.json").read_text(encoding="utf-8"))
            result["page"]["png"] = base64.b64decode(result["page"]["png"])
            self.ready.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.cleanup()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self.cancelled:
            self.cleanup()
            self.failed.emit("无法启动本机附件处理进程。")

    def timed_out(self):
        self.cancel()
        self.failed.emit("准备附件超时，已停止。可以通过 ＋ 仅发送当前页或选区。")

    def cancel(self):
        self.cancelled = True
        self.timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        self.cleanup()

    def cleanup(self):
        self.done = True
        self.timer.stop()
        self.poller.stop()
        self.snapshot = None
        if self.directory:
            self.directory.remove()
            self.directory = None
        self.deleteLater()


def prepare(directory):
    # Invoked by main.py before importing any GUI window or HTTP client.
    from .engine import PdfDocument
    from .ai_context import build_document_context, page_image
    path = Path(directory)
    doc = PdfDocument()
    try:
        info = json.loads((path / "input.json").read_text(encoding="utf-8"))
        doc.open(path / "input.pdf")
        doc.state = info["source_state"]
        context = None
        if info.get("mode"):
            def progress(done, total):
                value = json.dumps({"done": done, "total": total})
                (path / "progress.json").write_text(value, encoding="utf-8")
                if sys.stdout is not None:
                    print(value, flush=True)
            context = build_document_context(doc, info["name"], info["source_session"], info["mode"], progress)
        number = info["page"]
        if context and context["mode"] == "pages":
            png = json.loads(context["data"])[number]["png"]
        else:
            png = base64.b64encode(page_image(doc.doc[number])).decode("ascii")
        result = {"context": context, "page": {"png": png, "label": f"第 {info['label_page'] + 1} 页 · 图片"}}
        (path / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:
        (path / "error.txt").write_text(str(exc), encoding="utf-8")
        return 1
    finally:
        doc.close()
