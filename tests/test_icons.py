"""Regression coverage for cropped icons on Windows scaled displays."""
import pytest
from PySide6.QtCore import QSize, QRect, Qt
from PySide6.QtGui import QIcon, QImage, QPainter
from pdfreader.icons import icon, PATHS


def ink_bounds(image):
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).alpha() > 64]
    assert points, "The icon rendered blank"
    return (min(x for x, y in points), min(y for x, y in points),
            max(x for x, y in points) + 1, max(y for x, y in points) + 1)


@pytest.mark.parametrize("dpr", [1.25, 1.5, 1.75, 2.0, 2.5, 3.0])
def test_icons_keep_their_full_shape_at_display_scales(qapp, dpr):
    for name in PATHS:
        reference = icon(name).pixmap(QSize(20, 20), 1.0).toImage()
        pixmap = icon(name).pixmap(QSize(20, 20), dpr)
        assert pixmap.size() == QSize(round(20 * dpr), round(20 * dpr)), name
        assert pixmap.devicePixelRatio() == pytest.approx(dpr), name
        expected = ink_bounds(reference)
        actual = tuple(value / dpr for value in ink_bounds(pixmap.toImage()))
        assert max(abs(a - b) for a, b in zip(actual, expected)) <= 1.1, name


def test_welcome_icon_renders_at_large_sizes_instead_of_upscaling_a_crop(qapp):
    for size in (54, 96):
        pixmap = icon("document").pixmap(QSize(size, size), 2.0)
        assert pixmap.size() == QSize(size * 2, size * 2)
        left, top, right, bottom = ink_bounds(pixmap.toImage())
        assert right - left > pixmap.width() * .5
        assert bottom - top > pixmap.height() * .75
        assert left > 0 and top > 0
        assert right < pixmap.width() and bottom < pixmap.height()


def test_direct_icon_paint_respects_offset_and_display_scale(qapp):
    image = QImage(120, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    icon("save").paint(painter, QRect(13, 11, 20, 20))
    painter.end()
    left, top, right, bottom = ink_bounds(image)
    assert 26 <= left < right <= 66
    assert 22 <= top < bottom <= 62
    assert right - left >= 28 and bottom - top >= 28


def test_selected_and_disabled_icons_preserve_shape(qapp):
    images = [icon("highlight").pixmap(QSize(20, 20), 1.5, mode, state).toImage()
              for mode, state in ((QIcon.Mode.Normal, QIcon.State.Off),
                                  (QIcon.Mode.Normal, QIcon.State.On),
                                  (QIcon.Mode.Disabled, QIcon.State.On))]
    assert len({ink_bounds(image) for image in images}) == 1
    colors = [next(image.pixelColor(x, y).rgb() for y in range(image.height())
                   for x in range(image.width()) if image.pixelColor(x, y).alpha() > 250)
              for image in images]
    assert len(set(colors)) == 3
