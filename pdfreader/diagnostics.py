"""Explicit developer smoke test, also available in the frozen executable.

No network requests. Writes results only to the directory passed on the CLI.
"""
from pathlib import Path
import json
import os
import time
import traceback


def smoke(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["PDFREADER_DATA_DIR"] = str(root / "settings")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer, QPointF, Qt
    from PySide6.QtTest import QTest
    import pymupdf as fitz
    from .app import MainWindow
    from .style import STYLE, prepare_fonts
    from .paths import resource_root
    from .text_edit import normalized_font
    app = QApplication.instance() or QApplication([])
    prepare_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    errors = []
    window.error = lambda exc: errors.append(str(exc))
    assert window.auto_ocr.isChecked()
    window.show()
    output = {"checks": [], "errors": errors, "display_scale": window.devicePixelRatioF()}
    started = time.monotonic()
    terminal_sent = False
    stage = 0
    busy = False
    first_session = None
    first_zoom = None

    def select_phrase(phrase, number=0):
        view = window.view
        view.goto(number)
        r = window.document.doc[number].search_for(phrase)[0]
        item = view.items_by_page[number]
        y = (r.y0 + r.y1) / 2
        start = view.mapFromScene(item.mapToScene(QPointF(r.x0 + .2, y)))
        end = view.mapFromScene(item.mapToScene(QPointF(r.x1 - .2, y)))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert view.selected_text.casefold() == phrase.casefold(), repr(view.selected_text)
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert app.clipboard().text() == view.selected_text

    def finish():
        output["errors"] = errors
        output["ok"] = not errors
        (root / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        for session in window.sessions:
            session.document.saved_state = session.document.state
        window.close()
        app.quit()

    def tick():
        nonlocal stage, terminal_sent, busy, first_session, first_zoom
        if busy:
            return
        busy = True
        try:
            if time.monotonic() - started > 40:
                raise RuntimeError("Smoke test exceeded 40 seconds")
            if stage == 0:
                stage = 1
                window.open_file(str(resource_root() / "samples" / "welcome.pdf"))
                assert window.document.count == 3
                assert window.document.scan_candidates() == [1]
                output["checks"].append("open_sample")
                assert window.top.widgetForAction(window.ocr_action) is None
                assert not window.mode_actions["highlight"].isEnabled()
                output["checks"].append("selection_only_highlight_and_hidden_ocr_button")
                phrase = "让批注成为线索"
                r = window.document.doc[0].search_for(phrase)[0]
                box, _ = window.document.text_block_at(0, (r.tl + r.br) / 2)
                region = window.document.text_region(0, box)
                original_style = region.primary_style
                window.document.replace_text(0, region, "让批注留下线索")
                changed = window.document.text_region(0, box)
                assert normalized_font(changed.primary_style.font) == normalized_font(original_style.font)
                assert abs(changed.primary_style.size - original_style.size) < .01
                assert changed.primary_style.color == original_style.color
                output["checks"].append("chinese_edit_preserves_font_size_and_color")
                window.after_change()
                window.view.fit_page()
                select_phrase("把信息变成自己的语言")
                output["checks"].append("horizontal_chinese_selection_and_clipboard")
                window.mode_actions["highlight"].trigger()
                assert window.document.annotations()[0]["type"] == "Highlight"
                window.undo()
                assert not window.document.annotations()
                window.redo()
                assert len(window.document.annotations()) == 1
                output["checks"].append("selection_highlight_undo_redo")
                r = window.document.doc[0].search_for("把信息变成自己的语言")[0]
                window.document.add_markup_ranges({0: [tuple(r)]}, content="把这句话，用自己的语言再解释一遍。")
                entry = window.document.annotations()[-1]
                assert entry["quote"] == "把信息变成自己的语言"
                output["checks"].append("text_comment_with_persistent_quote")
                window.after_change()
                # open_file schedules OCR automatically; do not invoke it here.
            elif stage == 1 and window.ocr_process is None and not window.document.scan_candidates():
                if errors:
                    raise RuntimeError("OCR failed: " + str(errors))
                text = window.document.page_text(1)
                assert "searchable" in text.lower(), text
                assert "识别" in text, text
                output["checks"].append("auto_ocr_english_and_chinese")
                select_phrase("searchable", 1)
                output["checks"].append("ocr_text_mouse_selection")
                window.document.save(root / "smoke-result.pdf")
                window.update_state()
                window.status_label.setText("")
                window.view.goto(0)
                window.view.fit_page()
                window.show_panel("comment")
                if not window.left.isVisible():
                    window.toggle_left()
                stage = 2
            elif stage == 2:
                stage = 3
                window.grab().save(str(root / "app.png"))
                output["checks"].append("screenshot")
                window.show_panel("terminal")
                window.terminal.command.setCurrentIndex(1)
                window.terminal.start()
                output["terminal_start"] = {"phase": window.terminal.start_phase, "error": window.terminal.last_error,
                    "pid": window.terminal.process.pid if window.terminal.process else None,
                    "cwd": window.terminal.cwd}
            elif stage == 3:
                if window.terminal.process is None:
                    raise RuntimeError("Terminal failed: " + window.terminal.start_phase + " / " + window.terminal.last_error + " / " + window.terminal.description.text() + "\n" + window.terminal.display.toPlainText())
                if not terminal_sent:
                    window.terminal.write("echo PDFREADER_TERMINAL_OK\r")
                    terminal_sent = True
                text = window.terminal.display.toPlainText()
                if text.count("PDFREADER_TERMINAL_OK") >= 2:
                    output["checks"].append("conpty_command_output")
                    window.terminal.write("cd\r")
                    stage = 4
            elif stage == 4:
                window.grab().save(str(root / "terminal.png"))
                window.terminal.stop()
                window.show_panel("ai")
                window.view.goto(0)
                stage = 5
            elif stage == 5:
                select_phrase("划词，而后思考")
                window.ask_selection()
                window.grab().save(str(root / "ai.png"))
                window.toggle_panel(None)
                if window.left.isVisible():
                    window.toggle_left()
                window.view.clear_selection()
                stage = 6
            elif stage == 6:
                # Let the hidden sidebars settle before fitting and capturing.
                window.view.fit_page()
                stage = 7
            elif stage == 7:
                window.grab().save(str(root / "reader.png"))
                window.top.grab().save(str(root / "toolbar.png"))
                window.footer.grab().save(str(root / "footer.png"))
                window.document.open(root / "smoke-result.pdf")
                assert len(window.document.annotations()) == 2
                assert "searchable" in window.document.page_text(1).lower()
                output["checks"].append("reopen_saved_document")
                first_session = window.active_session
                first_zoom = window.view.zoom
                other = root / "second-document.pdf"
                other.write_bytes((root / "smoke-result.pdf").read_bytes())
                window.open_file(other)
                assert len(window.sessions) == 2 and window.document_tabs.isVisible()
                window.view.set_zoom(1.25)
                window.view.goto(1)
                window.mutate(lambda: window.document.add_comment(0, (100, 100), "Only this tab"))
                window.document_tabs.setCurrentIndex(0)
                assert window.active_session is first_session
                assert abs(window.view.zoom - first_zoom) < .001
                assert len(window.document.annotations()) == 2 and not window.document.dirty
                window.document_tabs.setCurrentIndex(1)
                assert window.view.zoom == 1.25 and window.document.dirty
                window.undo()
                assert not window.document.dirty
                assert window.open_file(other) and len(window.sessions) == 2
                output["checks"].append("multi_document_tabs_state_undo_and_duplicate_open")
                window.document_tabs.setCurrentIndex(0)
                stage = 8
            elif stage == 8:
                window.view.fit_page()
                stage = 9
            elif stage == 9:
                assert abs(window.toolbar_left_space.width() - window.toolbar_right_space.width()) <= 1
                assert window.footer.isAncestorOf(window.document_tabs)
                assert window.top.widgetForAction(window.mode_actions["hand"]) is None
                assert window.mode_actions["hand"] in window.reading_tools_menu.actions()
                window.mode_actions["hand"].trigger()
                assert window.view.mode == "hand"
                window.mode_actions["select"].trigger()
                assert window.view.mode == "select"
                window.status_label.setText("")
                output["checks"].append("footer_tabs_and_hand_tool_in_menu")
                window.grab().save(str(root / "tabs.png"))
                assert window.close_document(1)
                assert window.active_session is first_session and window.document_tabs.isHidden()
                from .diagnostics_chat import exercise_chat
                exercise_chat(window, root, output)
                assert window.close_document()
                assert window.stack.currentWidget() is window.welcome and not window.sessions
                output["checks"].append("centered_toolbar_and_close_tabs_to_welcome")
                output["checks"].append("close_and_cleanup")
                finish()
        except Exception:
            errors.append(traceback.format_exc())
            window.grab().save(str(root / "failure.png"))
            finish()
        finally:
            busy = False

    timer = QTimer()
    timer.setInterval(150)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    return 0 if output.get("ok") else 1
