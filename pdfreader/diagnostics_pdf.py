"""Opt-in local PDF regression check, available in source and packaged builds.

Read-only input; isolated settings; simulated AI transport with no HTTP access.
"""
from pathlib import Path
import hashlib
import json
import os
import time


def inspect_pdf(filename, directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["PDFREADER_DATA_DIR"] = str(root / "settings")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from .app import MainWindow
    from .style import STYLE, prepare_fonts
    from . import ai as client
    source = Path(filename)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    app = QApplication.instance() or QApplication([])
    prepare_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.resize(1380, 900)
    window.show()
    captured = []
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    result = {"ok": False, "network": "disabled: simulated response only"}
    original_opener = client.build_opener

    class Response:
        headers = {"Content-Type": "text/event-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            for text in ("Local test: ", "∇ PDF processed"):
                time.sleep(.05)
                yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.deepseek.com/chat/completions"
            payload = json.loads(request.data)
            background = payload["messages"][1]["content"]
            captured.append({"background_images": sum(part["type"] == "image_url" for part in background),
                             "file_parts": sum(part["type"] == "file" for part in background),
                             "request_bytes": len(request.data), "max_tokens_sent": "max_tokens" in payload})
            return Response()

    try:
        client.build_opener = lambda *args: Opener()
        assert window.open_file(source)
        window.settings.values["ai"] = {"enabled": True, "endpoint": "https://api.deepseek.com", "model": "deepseek-flash"}
        window.ai.update_connection()
        window.show_panel("ai")
        app.processEvents()
        math_pages, gradients = [], 0
        for page in window.document.doc:
            count = page.get_text().count("∇")
            if count:
                math_pages.append(page.number)
                gradients += count
        number = math_pages[0] if math_pages else 0
        rectangles = window.document.doc[number].search_for("∇")
        focus = tuple(rectangles[0]) if rectangles else None
        for zoom in (1, 1.25, 4, 5):
            window.view.set_zoom(zoom)
            window.view.goto(number, focus)
            app.processEvents()
            window.view.viewport().grab().save(str(root / f"page-{number + 1}-zoom-{zoom}.png"))
            assert window.view.renderer.bytes <= window.view.renderer.budget
        window.ai.input.setPlainText("Local pipeline verification; no external AI request.")
        timer.start()
        started = time.monotonic()
        window.ai.send()
        while (window.ai.preparation or window.ai.job) and time.monotonic() - started < 120:
            app.processEvents()
            time.sleep(.005)
        assert not window.ai.preparation and not window.ai.job, "attachment preparation timed out"
        assert window.ai.entries[-1] == ("assistant", "Local test: ∇ PDF processed"), window.ai.entries[-1]
        assert captured[0]["background_images"] == window.document.count
        assert captured[0]["file_parts"] == 0 and not captured[0]["max_tokens_sent"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert not list((root / "settings").glob("attachment-*"))
        result.update(ok=True, pages=window.document.count, gradient_symbols=gradients,
                      math_pages=[p + 1 for p in math_pages], original_unchanged=True,
                      display_scale=window.devicePixelRatioF(), request=captured[0],
                      gui_ticks=len(ticks), max_gui_gap_ms=round(max((b - a for a, b in zip(ticks, ticks[1:])), default=0) * 1000),
                      duration_seconds=round(time.monotonic() - started, 3))
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        timer.stop()
        window.close()
        client.build_opener = original_opener
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
