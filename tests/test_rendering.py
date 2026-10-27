import math
import pytest
import pymupdf as fitz
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from pdfreader.engine import PdfDocument
from pdfreader.rendering import PageRenderer, TILE_PIXELS


@pytest.fixture
def math_document(tmp_path):
    source = fitz.open()
    page = source.new_page(width=595, height=842)
    font = fitz.Font("symbol")
    writer = fitz.TextWriter(page.rect)
    for y in range(45, 800, 40):
        writer.append((28, y), "∇ ∑ ∫ ∂ √ ≠ ≤ ≥ ∞", font=font, fontsize=12)
        page.draw_line((24, y + 5), (580, y + 5), color=(0, .1, .2), width=.25)
    writer.write_text(page)
    source.subset_fonts()
    path = tmp_path / "embedded-math.pdf"
    source.save(path)
    source.close()
    document = PdfDocument()
    document.open(path)
    yield document
    document.close()


def image_from_pixmap(pix):
    return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


@pytest.mark.parametrize("scale", [1, 1.25, 1.875, 3, 6, 7.5])
@pytest.mark.parametrize("rotation", [0, 90])
def test_visible_math_tiles_equal_native_raster_without_downsampling(qapp, math_document, scale, rotation):
    page = math_document.doc[0]
    page.set_rotation(rotation)
    renderer = PageRenderer(math_document)
    phase = (.375, .625)
    matrix = fitz.Matrix(scale, 0, 0, scale, *phase)
    # Cross physical tile boundaries, with fractional zoom and screen origin.
    exposed = fitz.Rect(20, 20, 260, 220)
    expected_pix = page.get_pixmap(matrix=matrix, clip=exposed, alpha=False)
    expected = image_from_pixmap(expected_pix)
    actual = QImage(expected.size(), QImage.Format.Format_RGB888)
    actual.fill(0xffffff)
    painter = QPainter(actual)
    tiles = list(renderer.visible_tiles(0, exposed, scale, phase))
    for image, target, source in tiles:
        rect = fitz.Rect(target.left(), target.top(), target.right(), target.bottom()) * matrix
        painter.drawImage(QRectF(round(rect.x0 - expected_pix.x), round(rect.y0 - expected_pix.y),
                                 round(rect.width), round(rect.height)), image, source)
        assert image.width() <= TILE_PIXELS + 6
        assert image.height() <= TILE_PIXELS + 6
    painter.end()
    assert actual == expected
    if scale >= 6:
        assert expected.width() > 1400  # The old 8M-page cap produced fewer pixels.


def test_cache_budget_and_revision_invalidation(qapp, math_document):
    renderer = PageRenderer(math_document, budget=2 * 1024 * 1024)
    for y in range(0, 800, 100):
        list(renderer.visible_tiles(0, (20, y, 180, y + 40), 7.5))
        assert renderer.bytes <= renderer.budget
    old_keys = list(renderer.tiles)
    assert old_keys
    math_document.add_comment(0, (40, 60), "new annotation")
    list(renderer.visible_tiles(0, (20, 20, 180, 100), 2))
    assert renderer.revision == math_document.revision
    assert not set(old_keys).intersection(renderer.tiles)
    renderer.clear()
    assert not renderer.tiles and not renderer.lists and renderer.bytes == 0


def test_paint_maps_exactly_to_physical_pixels(qapp, math_document):
    ratio, zoom = 1.5, 1.25
    actual = QImage(900, 1280, QImage.Format.Format_RGB888)
    actual.fill(0xffffff)
    actual.setDevicePixelRatio(ratio)
    painter = QPainter(actual)
    painter.translate(1.25, 2.125)
    painter.scale(zoom, zoom)
    transform = painter.deviceTransform()
    renderer = PageRenderer(math_document)
    renderer.paint(painter, 0, (0, 0, 595, 842))
    painter.end()
    matrix = fitz.Matrix(transform.m11(), 0, 0, transform.m22(), transform.dx(), transform.dy())
    expected = image_from_pixmap(math_document.doc[0].get_pixmap(matrix=matrix, alpha=False))
    # Compare the entire content area; raster boundaries remain white.
    rect = (20, 20, 850, 1200)
    assert actual.copy(*rect) == expected.copy(rect[0] - math.floor(transform.dx()), rect[1] - math.floor(transform.dy()), rect[2], rect[3])


def test_zoom_percentage_uses_screen_logical_dpi(qapp, math_document):
    from pdfreader.viewer import PdfView
    view = PdfView(math_document)
    view.resize(900, 750)
    view.show()
    view.rebuild()
    view.set_zoom(1)
    assert view.transform().m11() == pytest.approx(view.logicalDpiX() / 72)
    view.fit_width()
    assert view.sceneRect().width() * view.transform().m11() == pytest.approx(view.viewport().width() - 4)
    view.close()
