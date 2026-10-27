"""Vector icons drawn at the requested size and display density, without files."""
from functools import lru_cache
from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton


PATHS = {
    "history": '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "open": '<path d="M3 18V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v2M3 18l3-7h15l-3 8H4a1 1 0 0 1-1-1Z"/>',
    "save": '<path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8"/>',
    "undo": '<path d="m8 4-5 5 5 5M3 9h10a7 7 0 0 1 0 14" transform="translate(0 -2)"/>',
    "redo": '<g transform="translate(24 0) scale(-1 1)"><path d="m8 4-5 5 5 5M3 9h10a7 7 0 0 1 0 14" transform="translate(0 -2)"/></g>',
    "select": '<path d="M8 4h8M12 4v16M8 20h8"/>',
    "hand": '<path d="M8 12V6a2 2 0 0 1 4 0v5M12 5a2 2 0 0 1 4 0v6M16 8a2 2 0 0 1 4 0v6c0 5-2.5 7-7 7-3 0-4.5-1.5-6-4l-3-5a1.8 1.8 0 0 1 3-2l1 2Z"/>',
    "highlight": '<path d="m9 13 7-9 5 4-7 9-5-4ZM9 13l-4 5 5 1 4-2M3 22h17"/>',
    "underline": '<path d="M6 3v8a6 6 0 0 0 12 0V3M4 21h16"/>',
    "strike": '<path d="M18 5c-2-3-10-3-11 1-1 3 2 4 5 5m-4 4c3 0 10-1 10 3 0 4-9 4-12 1M3 12h18"/>',
    "comment": '<path d="M5 3h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-9l-6 4v-4H3V5a2 2 0 0 1 2-2ZM7 8h10M7 12h6"/>',
    "text": '<path d="M3 5h12M9 5v15M6 20h6M18 12v8M14 16h8"/>',
    "edit": '<path d="M11 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-6M11 11l8-8 3 3-8 8-4 1 1-4ZM17 5l3 3"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.4"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
    "ink": '<path d="m14 4 6 6M14 4 5 8 3 21l13-2 4-9M3 21l7-7"/><circle cx="12" cy="12" r="2"/>',
    "signature": '<path d="M3 16c4-1 9-8 9-11 0-3-3-2-3 1v11l4-5 2 4 3-2 3 2M3 21h18"/>',
    "ocr": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M8 8h8M12 8v8M10 16h4"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    "sidebar": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    "pages": '<path d="M8 3h12v15H8zM4 7v15h12"/>',
    "outline": '<path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1"/>',
    "ai": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4M18 4h4"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    "prev": '<path d="m14 5-7 7 7 7"/>',
    "next": '<path d="m10 5 7 7-7 7"/>',
    "up": '<path d="m5 14 7-7 7 7"/>',
    "down": '<path d="m5 10 7 7 7-7"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "fit_width": '<path d="M3 4v16M21 4v16M4 12h16m-13-3-3 3 3 3m10-6 3 3-3 3"/>',
    "fit_page": '<rect x="8" y="6" width="8" height="12" rx="1"/><path d="M7 3H3v4m14-4h4v4M3 17v4h4m10 0h4v-4"/>',
    "fullscreen": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5"/>',
    "gear": '<path d="m10 3-.5 3-2 1.2-2.8-1-2 3.5L5 11v2l-2.3 1.3 2 3.5 2.8-1 2 1.2.5 3h4l.5-3 2-1.2 2.8 1 2-3.5L19 13v-2l2.3-1.3-2-3.5-2.8 1-2-1.2-.5-3Z"/><circle cx="12" cy="12" r="3"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
    "send": '<path d="M12 20V4m-7 7 7-7 7 7"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "play": '<path d="m7 4 13 8-13 8V4Z"/>',
    "document": '<path d="M6 2h8l5 5v15H5V2h1Zm8 0v6h5M8 12h8m-8 4h6"/>',
}


class SvgIconEngine(QIconEngine):
    """Keep vector data until Qt supplies the actual target rectangle and DPR."""
    def __init__(self, name, color=None):
        super().__init__()
        self.name = name
        self.color = color
        self.renderers = {}

    def clone(self):
        return SvgIconEngine(self.name, self.color)

    def isNull(self):
        return False

    def key(self):
        return "PDFReaderSvg"

    def actualSize(self, size, mode, state):
        side = min(size.width(), size.height())
        return QSize(side, side)

    def paint(self, painter, rect, mode, state):
        if rect.isEmpty():
            return
        stroke = ("#b8bdc5" if mode == QIcon.Mode.Disabled else
                  self.color or ("#326ad5" if state == QIcon.State.On else "#424b57"))
        renderer = self.renderers.get(stroke)
        if renderer is None:
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                   f'<g fill="none" stroke="{stroke}" stroke-width="1.8" '
                   f'stroke-linecap="round" stroke-linejoin="round">{PATHS[self.name]}</g></svg>')
            renderer = QSvgRenderer(QByteArray(svg.encode()))
            self.renderers[stroke] = renderer
        side = min(rect.width(), rect.height())
        bounds = QRectF(rect.x() + (rect.width() - side) / 2,
                        rect.y() + (rect.height() - side) / 2, side, side)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Never let QSvgRenderer infer bounds from the physical paint device:
        # on a scaled display that applies the device scale a second time.
        renderer.render(painter, bounds)
        painter.restore()

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        if size.isEmpty():
            return QPixmap()
        # Qt >= 6.8 passes a device-independent size. Draw in physical pixels,
        # then attach DPR metadata, so fractional scales are applied only once.
        pixmap = QPixmap(max(1, round(size.width() * scale)),
                         max(1, round(size.height() * scale)))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            self.paint(painter, pixmap.rect(), mode, state)
        finally:
            painter.end()
        pixmap.setDevicePixelRatio(scale)
        return pixmap


@lru_cache(maxsize=96)
def icon(name, color=None):
    if name not in PATHS:
        raise KeyError(name)
    return QIcon(SvgIconEngine(name, color))


def split_tool_button(action):
    button = QToolButton()
    button.setObjectName("splitTool")
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(48, 34)
    button.setDefaultAction(action)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    return button


def icon_button(name, label, callback=None):
    button = QToolButton()
    button.setIcon(icon(name))
    button.setIconSize(QSize(18, 18))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.setFixedSize(32, 32)
    if callback:
        button.clicked.connect(callback)
    return button
