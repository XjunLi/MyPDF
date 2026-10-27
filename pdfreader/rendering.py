"""Render visible PDF tiles at physical display resolution, without page downsampling.

MuPDF stays on the GUI thread. Display lists avoid reinterpreting fonts and
paths for each tile; a byte budget bounds raster memory independently of zoom.
"""
from collections import OrderedDict
import math
import pymupdf as fitz
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

TILE_PIXELS = 512
CACHE_BYTES = 64 * 1024 * 1024


def qt_rect(rect):
    return QRectF(rect.x0, rect.y0, rect.width, rect.height)


class PageRenderer:
    def __init__(self, document, budget=CACHE_BYTES):
        self.document, self.budget = document, budget
        self.revision = None
        self.tiles, self.lists = OrderedDict(), OrderedDict()
        self.bytes = 0

    def clear(self):
        self.tiles.clear()
        self.lists.clear()
        self.bytes = 0
        self.revision = self.document.revision

    def display_list(self, number):
        if self.revision != self.document.revision:
            self.clear()
        if number not in self.lists:
            self.lists[number] = self.document.doc[number].get_displaylist(annots=True)
            while len(self.lists) > 8:
                self.lists.popitem(last=False)
        self.lists.move_to_end(number)
        return self.lists[number]

    def visible_tiles(self, number, exposed, scale, phase=(0.0, 0.0)):
        display = self.display_list(number)
        # Subpixel translation is part of rasterization, not a second Qt filter.
        # Thus thin mathematical strokes are sampled exactly once on HiDPI screens.
        matrix = fitz.Matrix(scale, 0, 0, scale, *phase)
        inverse = ~matrix
        bounds = (display.rect * matrix).irect
        visible = ((fitz.Rect(exposed) & display.rect) * matrix).irect & bounds
        if visible.is_empty:
            return
        for y in range(math.floor(visible.y0 / TILE_PIXELS), math.ceil(visible.y1 / TILE_PIXELS)):
            for x in range(math.floor(visible.x0 / TILE_PIXELS), math.ceil(visible.x1 / TILE_PIXELS)):
                core = fitz.IRect(x * TILE_PIXELS, y * TILE_PIXELS, (x + 1) * TILE_PIXELS, (y + 1) * TILE_PIXELS) & bounds
                key = (number, scale, phase, x, y)
                if key not in self.tiles:
                    # Two physical pixels of bleed avoid antialias seams at edges.
                    clip = fitz.Rect(core.x0 - 2, core.y0 - 2, core.x1 + 2, core.y1 + 2) * inverse
                    pix = display.get_pixmap(matrix=matrix, clip=clip, colorspace=fitz.csRGB, alpha=False)
                    image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                    self.tiles[key] = (image, pix.x, pix.y)
                    self.bytes += image.sizeInBytes()
                    while self.bytes > self.budget and len(self.tiles) > 1:
                        _, old = self.tiles.popitem(last=False)
                        self.bytes -= old[0].sizeInBytes()
                self.tiles.move_to_end(key)
                image, left, top = self.tiles[key]
                yield image, qt_rect(fitz.Rect(core) * inverse), QRectF(core.x0 - left, core.y0 - top, core.width, core.height)

    def paint(self, painter, number, exposed):
        transform = painter.deviceTransform()
        scale = math.hypot(transform.m11(), transform.m12())
        if not math.isfinite(scale) or scale <= 0:
            return
        phase = (transform.dx() % 1, transform.dy() % 1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setClipRect(qt_rect(self.document.doc[number].rect))
        for image, target, source in self.visible_tiles(number, exposed, scale, phase):
            painter.drawImage(target, image, source)
        painter.restore()
