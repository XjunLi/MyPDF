"""Text-caret selection in PDF reading order, independent of the Qt viewport."""
from dataclasses import dataclass
import re
import pymupdf as fitz


@dataclass
class Glyph:
    text: str
    rect: fitz.Rect
    line: int


@dataclass
class Line:
    start: int
    end: int
    rect: fitz.Rect
    direction: tuple


def distance_to_rect(point, rect):
    dx = max(rect.x0 - point.x, 0, point.x - rect.x1)
    dy = max(rect.y0 - point.y, 0, point.y - rect.y1)
    return dx * dx + dy * dy


class TextPage:
    def __init__(self, page):
        self.glyphs = []
        self.lines = []
        for block in page.get_text("rawdict", sort=True, flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
            for line in block.get("lines", []):
                start = len(self.glyphs)
                number = len(self.lines)
                for span in line["spans"]:
                    for char in span["chars"]:
                        self.glyphs.append(Glyph(char["c"], fitz.Rect(char["bbox"]), number))
                if len(self.glyphs) > start:
                    self.lines.append(Line(start, len(self.glyphs), fitz.Rect(line["bbox"]), tuple(line.get("dir", (1, 0)))))

    def caret(self, point, near=False):
        if not self.lines:
            return None
        point = fitz.Point(point)
        line = min(self.lines, key=lambda candidate: distance_to_rect(point, candidate.rect))
        if near and distance_to_rect(point, line.rect) > 12 ** 2:
            return None
        direction = fitz.Point(line.direction)
        along = point.x * direction.x + point.y * direction.y
        for index in range(line.start, line.end):
            center = (self.glyphs[index].rect.tl + self.glyphs[index].rect.br) / 2
            if along < center.x * direction.x + center.y * direction.y:
                return index
        return line.end

    def word(self, point):
        if not self.glyphs:
            return None
        point = fitz.Point(point)
        candidates = [(distance_to_rect(point, glyph.rect), index) for index, glyph in enumerate(self.glyphs)]
        distance, index = min(candidates)
        if distance > 12 ** 2:
            return None
        glyph = self.glyphs[index]
        # Latin words include digits and apostrophes; Chinese remains precise
        # at character granularity without guessing linguistic segmentation.
        is_word = lambda s: bool(re.fullmatch(r"[A-Za-z0-9_'’\-]+", s))
        start, end = index, index + 1
        if is_word(glyph.text):
            line = self.lines[glyph.line]
            while start > line.start and is_word(self.glyphs[start - 1].text):
                start -= 1
            while end < line.end and is_word(self.glyphs[end].text):
                end += 1
        return start, end

    def range(self, start, end):
        start, end = sorted((max(0, min(len(self.glyphs), start)), max(0, min(len(self.glyphs), end))))
        text, boxes = [], []
        previous_line = None
        box = None
        for glyph in self.glyphs[start:end]:
            if glyph.line != previous_line:
                if previous_line is not None:
                    text.append("\n")
                    boxes.append(tuple(box))
                box = fitz.Rect(glyph.rect)
                previous_line = glyph.line
            else:
                box |= glyph.rect
            text.append(glyph.text)
        if box is not None:
            boxes.append(tuple(box))
        return "".join(text), boxes
