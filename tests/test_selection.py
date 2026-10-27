"""Regressions for real mouse selection, rather than artificial rectangles."""
import pymupdf as fitz
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pdfreader.viewer import PdfView
from pdfreader.text_selection import TextPage


def visible_point(view, number, point):
    point = fitz.Point(point) * view.document.doc[number].rotation_matrix
    return view.mapFromScene(view.items_by_page[number].mapToScene(QPointF(point.x, point.y)))


def endpoints(view, number, text):
    box = view.document.doc[number].search_for(text)[0]
    y = (box.y0 + box.y1) / 2
    return (visible_point(view, number, (box.x0 + .2, y)),
            visible_point(view, number, (box.x1 - .2, y)))


def drag(view, start, end):
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end, 15)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)


@pytest.fixture
def view(qapp, document):
    result = PdfView(document)
    result.resize(820, 720)
    result.show()
    result.rebuild(False)
    result.set_zoom(1)
    result.goto(0)
    qapp.processEvents()
    yield result
    result.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("reverse", [False, True])
def test_horizontal_drag_selects_exact_text_at_any_rotation(view, rotation, reverse, qapp):
    view.document.doc[0].set_rotation(rotation)
    view.rebuild(False)
    qapp.processEvents()
    start, end = endpoints(view, 0, "PDF reading")
    drag(view, end, start) if reverse else drag(view, start, end)
    assert view.selected_text == "PDF reading"
    assert len(view.text_boxes[0]) == 1
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "PDF reading"


def test_multiline_selection_follows_reading_order(view):
    start, _ = endpoints(view, 0, "reading")
    _, end = endpoints(view, 0, "this original")
    drag(view, start, end)
    assert view.selected_text == "reading and editing\nReplace this original"
    assert len(view.text_boxes[0]) == 2


def test_double_click_selects_word_and_never_opens_edit(view):
    edited = []
    view.editRequested.connect(lambda *args: edited.append(args))
    start, end = endpoints(view, 0, "reading")
    center = QPoint((start.x()+end.x())//2, (start.y()+end.y())//2)
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    assert view.selected_text == "reading"
    assert not edited


def test_cross_page_selection_and_atomic_markup_undo(view, qapp):
    view.set_zoom(.5)
    view.goto(0)
    qapp.processEvents()
    start, _ = endpoints(view, 0, "comments.")
    _, end = endpoints(view, 1, "Second page")
    assert view.viewport().rect().contains(end)
    drag(view, start, end)
    assert view.selected_text == "comments.\n\nSecond page"
    assert set(view.text_boxes) == {0, 1}
    view.document.add_markup_ranges(view.text_boxes)
    assert len(view.document.annotations()) == 2
    assert len(view.document.undo_stack) == 1
    view.document.undo()
    assert not view.document.annotations()
    view.document.redo()
    assert len(view.document.annotations()) == 2


def test_drag_past_bottom_auto_scrolls_without_losing_anchor(view, qapp):
    view.set_zoom(1.4)
    view.goto(0)
    start, _ = endpoints(view, 0, "reading")
    edge = QPoint(start.x(), view.viewport().height()-2)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), edge)
    before = view.verticalScrollBar().value()
    for _ in range(7):
        view._auto_scroll()
    assert view.verticalScrollBar().value() > before
    assert view.text_anchor[0] == 0
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=edge)
    assert not view.scroll_timer.isActive()


def test_selection_then_toolbar_highlight(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    from pdfreader.app import MainWindow
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.show()
    window.open_file(str(pdf_path))
    window.view.set_zoom(1)
    window.view.goto(0)
    qapp.processEvents()
    drag(window.view, *endpoints(window.view, 0, "PDF reading"))
    assert window.selection_text == "PDF reading"
    window.mode_actions["highlight"].trigger()
    assert window.document.annotations()[0]["type"] == "Highlight"
    assert window.view.mode == "select"
    assert window.mode_actions["select"].isChecked()
    window.undo()
    assert not window.document.annotations()
    window.close()


def test_chinese_partial_selection(view, qapp):
    view.document.doc[0].insert_text((40, 210), "理解是把信息变成自己的语言", fontname="china-s", fontsize=18)
    view.rebuild(False)
    qapp.processEvents()
    drag(view, *endpoints(view, 0, "把信息变成自己"))
    assert view.selected_text == "把信息变成自己"


def test_ctrl_zoom_keeps_pointer_position_and_manual_zoom_cancels_pending_fit(view, qapp):
    view.set_zoom(1.5)
    view.goto(0, (100, 180, 200, 250))
    anchor = QPoint(view.viewport().width()//2, view.viewport().height()//3)
    before = view.mapToScene(anchor)
    view.resize_timer.start(20)
    view.set_zoom(1.8, anchor=anchor)
    QTest.qWait(90)
    after = view.mapToScene(anchor)
    assert abs(before.y()-after.y()) < 2
    assert view.zoom == 1.8


def test_selection_range_clamps_reversed_indices(document):
    page = TextPage(document.doc[0])
    assert page.range(9999, -1) == page.range(0, len(page.glyphs))


def test_escape_clears_selection(view):
    drag(view, *endpoints(view, 0, "reading"))
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.selected_text == ""
    assert not view.text_boxes


def test_legacy_highlighter_mode_only_selects_text(view):
    groups = []
    view.markupChosen.connect(lambda kind, boxes: groups.append((kind, boxes)))
    view.set_mode("highlight")
    drag(view, *endpoints(view, 0, "reading"))
    assert view.selected_text == "reading"
    assert view.mode == "select"
    assert not groups
