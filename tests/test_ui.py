import pytest
import pymupdf as fitz
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from pdfreader.viewer import PdfView
from pdfreader.config import validate_plugin, protect
from pdfreader.ai import validate_endpoint


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_view_coordinates_after_rotation(qapp, document, rotation):
    document.doc[0].set_rotation(rotation)
    view = PdfView(document)
    view.resize(850, 620)
    view.show()
    view.rebuild(False)
    qapp.processEvents()
    point = fitz.Point(100, 150)
    local = point * document.doc[0].rotation_matrix
    item = view.items_by_page[0]
    from PySide6.QtCore import QPointF
    screen = view.mapFromScene(item.mapToScene(QPointF(local.x, local.y)))
    hit = view.locate(screen, 0)
    assert hit[0] == 0
    assert abs(hit[1].x - point.x) < 2
    assert abs(hit[1].y - point.y) < 2
    view.close()


def test_ui_zoom_search_selection_and_reopen(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    from pdfreader.app import MainWindow
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.show()
    window.open_file(str(pdf_path))
    qapp.processEvents()
    assert window.document.count == 2
    window.view.set_zoom(1.5)
    assert window.zoom_combo.currentText() == "150%"
    window.search_input.setText("reading")
    window.search()
    assert len(window.search_results) == 1
    assert window.view.search_boxes[0]
    window.view.goto(1)
    assert window.page_spin.value() == 2
    window.mutate(lambda: window.document.add_comment(0, (50, 200), "UI note"))
    assert "未保存" in window.modified_label.text()
    assert window.comment_list.count() == 1
    window.undo()
    assert not window.document.dirty
    window.close()


def test_ai_url_and_plugin_validation():
    assert validate_endpoint("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/chat/completions"
    assert validate_endpoint("https://example.com/v1/chat/completions").endswith("/chat/completions")
    for url in ["http://example.com/v1", "file:///tmp/secret", "https://key:pass@example.com", "https://example.com/?key=x"]:
        with pytest.raises(ValueError):
            validate_endpoint(url)
    assert validate_plugin({"name": "Local AI", "kind": "ai", "endpoint": "http://127.0.0.1:11434/v1", "model": "local-model"})
    with pytest.raises(ValueError):
        validate_plugin({"name": "Unsafe shape", "kind": "terminal", "command": "shell text"})


def test_dpapi_key_roundtrip():
    import os
    if os.name != "nt":
        pytest.skip("Windows only")
    secret = "test-local-key-123"
    encrypted = protect(secret)
    assert secret not in encrypted
    assert protect(encrypted, decrypt=True) == secret


def test_search_cancels_old_batches_when_query_or_document_changes(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    from pdfreader.app import MainWindow
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.show()
    window.open_file(str(pdf_path))
    for n in range(30):
        window.document.doc.new_page().insert_text((50, 50), "old-query new-query")
    window.after_change(False)
    window.search_input.setText("old-query")
    window.search()
    assert len(window.search_results) < 30
    window.search_input.setText("new-query")
    window.search()
    QTest.qWait(50)
    assert len(window.search_results) == 30
    assert window.search_query == "new-query"
    window.search_input.setText("old-query")
    window.search()
    # Opening the same path now focuses its existing tab. A different file
    # must still cancel the outstanding batches and isolate search results.
    other_path = tmp_path / "another.pdf"
    other_path.write_bytes(pdf_path.read_bytes())
    window.open_file(str(other_path))
    QTest.qWait(50)
    assert not window.search_results
    assert not window.view.search_boxes
    window.close()


def test_footer_controls_hidden_ocr_and_small_window_layout(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    from pdfreader.app import MainWindow
    from pdfreader.style import STYLE
    window = MainWindow()
    window.setStyleSheet(STYLE)
    window.auto_ocr.setChecked(False)
    window.resize(1000, 700)
    window.show()
    window.open_file(str(pdf_path))
    qapp.processEvents()
    assert not window.ocr_progress.isVisible()
    assert not window.ocr_stop.isVisible()
    assert window.footer.y() > window.view.y()
    for action in window.panel_actions.values():
        button = window.top.widgetForAction(action)
        assert button.isVisible()
        assert window.top.rect().contains(button.geometry())
    window.show_panel("ai")
    window.toggle_left()
    qapp.processEvents()
    assert window.view.viewport().width() >= 400
    window.page_spin.setFocus()
    window.page_spin.selectAll()
    QTest.keyClicks(window.page_spin, "2")
    QTest.keyClick(window.page_spin, Qt.Key.Key_Return)
    assert window.view.current_page == 1
    window.previous_action.trigger()
    assert window.page_spin.value() == 1
    assert window.view.current_page == 0
    window.show_search()
    assert window.search_input.isVisible()
    window.hide_search()
    assert not window.search_input.isVisible()
    window.close()


@pytest.mark.parametrize("primary, alternate", [("text", "image")])
def test_split_tool_icon_and_arrow_have_separate_click_targets(qapp, pdf_path, tmp_path, monkeypatch, primary, alternate):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    from pdfreader.app import MainWindow
    from pdfreader.style import STYLE
    window = MainWindow()
    window.setStyleSheet(STYLE)
    window.auto_ocr.setChecked(False)
    window.show()
    window.open_file(str(pdf_path))
    qapp.processEvents()
    button = window.markup_button if primary == "highlight" else window.insert_button
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(16, 17))
    assert window.view.mode == primary
    opened = []
    menu = button.menu()
    menu.aboutToShow.connect(lambda: opened.append(True))
    # The real arrow click enters Qt's menu event loop. Dismiss it on that loop.
    QTimer.singleShot(50, menu.close)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(button.width() - 6, 17))
    assert opened == [True]
    assert window.view.mode == primary
    window.mode_actions[alternate].trigger()
    assert button.defaultAction() is window.mode_actions[alternate]
    window.mode_actions["select"].trigger()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(16, 17))
    assert window.view.mode == alternate
    window.close()
