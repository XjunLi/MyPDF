import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from test_selection import drag, endpoints, visible_point, view


def install_links(view, rotation=0, named=False):
    doc = view.document.doc
    doc.new_page(width=420, height=580)
    page = doc[0]
    internal = page.search_for("comments")[0]
    web = page.search_for("PDF reading")[0]
    page.insert_link({"kind": fitz.LINK_GOTO, "from": internal, "page": 1, "to": fitz.Point(70, 130)})
    page.insert_link({"kind": fitz.LINK_URI, "from": web, "uri": "https://example.org/paper?q=a#sec"})
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(60, 180, 150, 210), "uri": "file:///example.txt"})
    page = doc.reload_page(page)
    if named:
        xref = next(link["xref"] for link in page.get_links() if link["kind"] == fitz.LINK_GOTO)
        doc.xref_set_key(doc.pdf_catalog(), "Names",
                         f"<< /Dests << /Names [(section.1) [{doc[1].xref} 0 R /XYZ 70 450 0]] >> >>")
        doc.xref_set_key(xref, "A", "<< /S /GoTo /D (section.1) >>")
    page.set_rotation(rotation)
    doc[1].set_rotation((rotation + 90) % 360)
    doc.reload_page(page)
    view.rebuild(False)
    view.fit_page()
    return internal, web


@pytest.mark.parametrize("rotation,named", [(0, False), (90, True), (180, True), (270, False)])
def test_click_internal_and_named_pdf_destinations(view, qapp, rotation, named):
    internal, _ = install_links(view, rotation, named)
    qapp.processEvents()
    point = visible_point(view, 0, (internal.tl + internal.br) / 2)
    assert view.viewport().rect().contains(point)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert view.current_page == 1
    target = visible_point(view, 1, (70, 130))
    assert 0 <= target.y() <= 27


def test_external_link_click_drag_and_unsupported_file_uri(view, qapp, monkeypatch):
    _, web = install_links(view)
    qapp.processEvents()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda u: opened.append(u.toString()) or True)
    start, end = endpoints(view, 0, "PDF reading")
    drag(view, start, end)
    assert view.selected_text == "PDF reading" and not opened
    spot = visible_point(view, 0, (web.tl + web.br) / 2)
    QTest.mouseMove(view.viewport(), spot)
    assert view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=spot)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=visible_point(view, 0, (90, 190)))
    assert opened == ["https://example.org/paper?q=a#sec"]
    assert view.current_page == 0
