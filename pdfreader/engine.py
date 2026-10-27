"""PDF operations. All access in the GUI process stays on the GUI thread.

OCR uses a separate process: MuPDF must not be used concurrently in threads.
Coordinates exposed here are unrotated PDF points, relative to the page crop.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import tempfile
import uuid

import pymupdf as fitz
from .text_edit import capture_region, replacement_plan, write_replacement, TextEditError


class PdfError(Exception):
    pass


class PasswordRequired(PdfError):
    pass


class PdfDocument:
    HISTORY_LIMIT = 20
    HISTORY_BYTES = 192 * 1024 * 1024

    def __init__(self):
        self.doc = None
        self.path = None
        self.password = ""
        self.state = ""
        self.saved_state = ""
        self.revision = 0
        self.undo_stack = []
        self.redo_stack = []

    @property
    def dirty(self):
        return self.doc is not None and self.state != self.saved_state

    @property
    def count(self):
        return len(self.doc) if self.doc is not None else 0

    def _load(self, data, password=None):
        result = fitz.open(stream=data, filetype="pdf")
        if result.needs_pass and not result.authenticate(self.password if password is None else password):
            result.close()
            raise PasswordRequired("此 PDF 需要正确的密码。")
        if not len(result):
            result.close()
            raise PdfError("PDF 中没有可显示的页面。")
        return result

    def open(self, path, password=""):
        path = Path(path).resolve()
        document = self._load(path.read_bytes(), password)
        if self.doc is not None:
            self.doc.close()
        self.doc = document
        self.path = path
        self.password = password
        self.state = uuid.uuid4().hex
        self.saved_state = self.state
        self.revision += 1
        self.undo_stack.clear()
        self.redo_stack.clear()

    def new(self):
        if self.doc is not None:
            self.doc.close()
        self.doc = fitz.open()
        self.doc.new_page()
        self.path = None
        self.password = ""
        self.state = uuid.uuid4().hex
        self.saved_state = ""
        self.revision += 1
        self.undo_stack.clear()
        self.redo_stack.clear()

    def snapshot(self):
        return self.doc.tobytes(encryption=fitz.PDF_ENCRYPT_KEEP)

    def _restore(self, data, state):
        loaded = self._load(data)
        self.doc.close()
        self.doc = loaded
        self.state = state
        self.revision += 1

    def require_permission(self, annotation=False):
        flag = fitz.PDF_PERM_ANNOTATE if annotation else fitz.PDF_PERM_MODIFY
        if not (self.doc.permissions & flag):
            raise PdfError("此 PDF 的权限限制了这项操作，请使用有编辑权限的副本。")

    @contextmanager
    def change(self, label, annotation=False):
        self.require_permission(annotation)
        before = (self.snapshot(), self.state, label)
        try:
            yield
        except Exception:
            self._restore(before[0], before[1])
            raise
        self.undo_stack.append(before)
        self.redo_stack.clear()
        while len(self.undo_stack) > self.HISTORY_LIMIT or (
            sum(len(x[0]) for x in self.undo_stack) > self.HISTORY_BYTES
        ):
            self.undo_stack.pop(0)
        self.state = uuid.uuid4().hex
        self.revision += 1

    def undo(self):
        if not self.undo_stack:
            return False
        data, state, label = self.undo_stack.pop()
        self.redo_stack.append((self.snapshot(), self.state, label))
        self._restore(data, state)
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        data, state, label = self.redo_stack.pop()
        self.undo_stack.append((self.snapshot(), self.state, label))
        self._restore(data, state)
        return True

    def save(self, destination=None):
        target = Path(destination or self.path or "").resolve()
        if target.is_dir():
            raise PdfError("请选择 PDF 文件名。")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write and verify a sibling temporary file before atomically replacing.
        fd, temp = tempfile.mkstemp(prefix=".pdfreader-", suffix=".pdf", dir=target.parent)
        os.close(fd)
        try:
            self.doc.save(temp, garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            check = self._load(Path(temp).read_bytes())
            if len(check) != self.count:
                raise PdfError("保存验证失败，原文件未被替换。")
            check.close()
            with open(temp, "r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.path = target
        self.saved_state = self.state

    def page_text(self, page):
        if not (self.doc.permissions & fitz.PDF_PERM_COPY):
            raise PdfError("此 PDF 禁止提取文字。")
        return self.doc[page].get_text(sort=True)

    def words(self, page, rect=None):
        words = self.doc[page].get_text("words", sort=True)
        if rect is not None:
            rect = fitz.Rect(rect)
            words = [w for w in words if rect.contains(fitz.Rect(w[:4]).tl + (fitz.Rect(w[:4]).br - fitz.Rect(w[:4]).tl) / 2)]
        return words

    def selection_text(self, page, rect):
        if not (self.doc.permissions & fitz.PDF_PERM_COPY):
            return ""
        return "\n".join(text for _, text in self.selection_parts(page, rect))

    def selection_parts(self, page, rect):
        # Glyph selection supports Chinese text without spaces and partial words.
        region = fitz.Rect(rect)
        parts = []
        for block in self.doc[page].get_text("rawdict", sort=True)["blocks"]:
            for line in block.get("lines", []):
                chars = []
                bounds = None
                for span in line["spans"]:
                    for char in span["chars"]:
                        box = fitz.Rect(char["bbox"])
                        center = (box.tl + box.br) / 2
                        if region.contains(center):
                            chars.append(char["c"])
                            bounds = box if bounds is None else bounds | box
                if chars and "".join(chars).strip():
                    parts.append((bounds, "".join(chars)))
        return parts

    def text_block_at(self, page, point):
        p = self.doc[page]
        blocks = p.get_text("blocks")
        lines = [line for block in p.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"] for line in block.get("lines", [])]
        for line in lines:
            if fitz.Rect(line["bbox"]).contains(fitz.Point(point)):
                box = fitz.Rect(line["bbox"])
                text = "".join(s["text"] for s in line["spans"])
                if capture_region(p, box).invisible:
                    return box, text
                # A text block's bbox ends at its final glyph, not its editing
                # margin. Allow longer words in available whitespace, stopping
                # before adjacent columns, images, rules and enclosing panels.
                right = (p.rect * p.derotation_matrix).x1 - 16
                obstacles = [fitz.Rect(other["bbox"]) for other in lines if other is not line]
                obstacles.extend(fitz.Rect(b[:4]) for b in blocks if b[6] != 0)
                drawings = [fitz.Rect(d["rect"]) for d in p.get_drawings()]
                for other in obstacles + drawings:
                    if other.y0 < box.y1 and other.y1 > box.y0 and other.x0 >= box.x1 - .5:
                        right = min(right, other.x0 - 4)
                for panel in drawings:
                    if panel.contains(box) and panel.x1 > box.x1 + 5:
                        right = min(right, panel.x1 - 5)
                box.x1 = max(box.x1, right)
                return box, text
        return None

    def text_region(self, page, rect):
        return capture_region(self.doc[page], rect)

    def replace_text(self, page, region, text):
        if text == region.text:
            return
        try:
            plan, fonts = replacement_plan(self.doc[page], region, text)
            with self.change("编辑文字"):
                write_replacement(self.doc[page], region, plan, fonts)
        except TextEditError as exc:
            raise PdfError(str(exc)) from exc

    def add_markup(self, page, rect, kind="highlight", color=(1, .79, .14)):
        boxes = [tuple(box) for box, text in self.selection_parts(page, rect)]
        self.add_markup_ranges({page: boxes}, kind, color)

    def add_markup_ranges(self, groups, kind="highlight", color=(1, .79, .14), content=""):
        if not any(groups.values()):
            raise PdfError("选区中没有文字。扫描页请先完成 OCR。")
        with self.change("文字标记", annotation=True):
            for number, boxes in groups.items():
                if not boxes:
                    continue
                p = self.doc[int(number)]
                create = {"highlight": p.add_highlight_annot, "underline": p.add_underline_annot,
                          "strike": p.add_strikeout_annot}[kind]
                annot = create([fitz.Rect(box).quad for box in boxes])
                annot.set_colors(stroke=color)
                if kind == "highlight":
                    annot.set_opacity(.45)
                quote = "\n".join(self.selection_text(int(number), box) for box in boxes)
                annot.set_info(title="我" if content else "PDF Reader", content=content,
                               subject="文字注释" if content else {"highlight": "高亮", "underline": "下划线", "strike": "删除线"}[kind])
                self.doc.xref_set_key(annot.xref, "PRQuote", fitz.get_pdf_str(quote))
                annot.update()

    def add_comment(self, page, point, text):
        with self.change("添加评论", annotation=True):
            p = self.doc[page]
            a = p.add_text_annot(point, text, icon="Comment")
            a.set_info(title="我", subject="评论")
            a.update()

    def annotations(self):
        result = []
        for p in self.doc:
            for a in p.annots() or []:
                kind, quote = self.doc.xref_get_key(a.xref, "PRQuote")
                vertices = a.vertices or []
                marked_boxes = [tuple(fitz.Quad(vertices[i:i + 4]).rect) for i in range(0, len(vertices), 4)] if a.type[1] in ("Highlight", "Underline", "StrikeOut") else []
                result.append({"page": p.number, "xref": a.xref, "type": a.type[1],
                               "rect": tuple(a.rect), "text": a.info.get("content", ""),
                               "marked_boxes": marked_boxes,
                               "quote": quote if kind == "string" else "",
                               "author": a.info.get("title", ""), "subject": a.info.get("subject", "")})
        return result

    def edit_comment(self, page, xref, text):
        with self.change("修改评论", annotation=True):
            p = self.doc[page]
            a = p.load_annot(xref)
            a.set_info(content=text)
            a.update()

    def delete_annotation(self, page, xref):
        with self.change("删除批注", annotation=True):
            p = self.doc[page]
            p.delete_annot(p.load_annot(xref))

    @staticmethod
    def _font(text):
        return "china-s" if any(ord(c) > 255 for c in text) else "helv"

    def add_text(self, page, rect, text, size=12, replace=False, raster_replace=False, color=(.08, .12, .18), fontname=None):
        if not text.strip() and not replace:
            return
        with self.change("编辑文字" if replace else "添加文字"):
            p = self.doc[page]
            box = fitz.Rect(rect)
            font = fontname or self._font(text)
            face = fitz.Font(font)
            if any(not c.isspace() and not face.has_glyph(ord(c), fallback=False) for c in text):
                raise PdfError("所选字体不包含部分字符。中文可选择“自动（中英文）”。")
            # Validate fitting before touching the actual page.
            trial = fitz.open()
            test = trial.new_page(width=p.cropbox.width, height=p.cropbox.height)
            remaining = test.insert_textbox(box, text, fontsize=size, fontname=font)
            trial.close()
            if remaining < 0:
                raise PdfError("文字超出选框，请缩小字号、减少文字或重新绘制更大的框。")
            if replace:
                if any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in (p.annots() or [])):
                    raise PdfError("本页含有尚未应用的遮盖标记。请先处理这些标记，再替换文字。")
                p.add_redact_annot(box, fill=(1, 1, 1) if raster_replace else False)
                p.apply_redactions(images=2 if raster_replace else 0, graphics=0, text=0)
            p.insert_textbox(box, text, fontsize=size, fontname=font, color=color)

    def add_ink(self, page, strokes, signature=False):
        valid = [[tuple(fitz.Point(p)) for p in stroke] for stroke in strokes if len(stroke) > 1]
        if not valid:
            raise PdfError("请先绘制笔迹。")
        with self.change("手写签名" if signature else "画笔", annotation=True):
            p = self.doc[page]
            a = p.add_ink_annot(valid)
            a.set_colors(stroke=(.08, .16, .3))
            a.set_border(width=1.5 if signature else 1.1)
            a.set_info(title="我", subject="手写签名" if signature else "自由笔迹")
            a.update()

    def add_image(self, page, rect, filename):
        with self.change("插入图片"):
            self.doc[page].insert_image(fitz.Rect(rect), filename=str(filename), keep_proportion=True)

    def rotate(self, page, delta=90):
        with self.change("旋转页面"):
            p = self.doc[page]
            p.set_rotation((p.rotation + delta) % 360)

    def delete_page(self, page):
        if self.count == 1:
            raise PdfError("至少需要保留一页。")
        with self.change("删除页面"):
            self.doc.delete_page(page)

    def insert_blank(self, after):
        with self.change("插入空白页"):
            self.doc.new_page(pno=after + 1)

    def move_page(self, source, destination):
        if source == destination:
            return
        with self.change("移动页面"):
            self.doc.move_page(source, to=destination if destination < source else destination + 1)

    def merge(self, filename, after, password=""):
        other = self._load(Path(filename).read_bytes(), password)
        try:
            with self.change("合并 PDF"):
                self.doc.insert_pdf(other, start_at=after + 1)
        finally:
            other.close()

    def export_pages(self, pages, destination):
        if self.path and Path(destination).resolve() == self.path:
            raise PdfError("导出页面请使用新文件名，不能覆盖当前文档。")
        result = fitz.open()
        try:
            for p in pages:
                result.insert_pdf(self.doc, from_page=p, to_page=p)
            result.save(destination, garbage=3, deflate=True)
        finally:
            result.close()

    def scan_candidates(self):
        # Do not run OCR over pages that already have a useful text layer.
        return [p.number for p in self.doc if len(p.get_text().strip()) < 4 and p.get_images()]

    def apply_ocr(self, overlays):
        with self.change("OCR 文字层"):
            for number, filename in overlays:
                p = self.doc[number]
                rotation = p.rotation
                p.set_rotation(0)
                try:
                    with fitz.open(filename) as layer:
                        p.show_pdf_page(p.rect, layer, 0, overlay=True)
                finally:
                    p.set_rotation(rotation)

    def update_widget(self, page, xref, value):
        with self.change("填写表单", annotation=True):
            p = self.doc[page]
            widget = p.load_widget(xref)
            widget.field_value = value
            widget.update()

    def close(self):
        if self.doc is not None:
            self.doc.close()
            self.doc = None


def parse_page_range(value, total):
    result = []
    for part in value.replace("，", ",").split(","):
        part = part.strip()
        if "-" in part:
            a, b = (int(n.strip()) for n in part.split("-"))
            if a > b:
                raise PdfError("页码范围应由小到大。")
            values = range(a, b + 1)
        else:
            values = [int(part)]
        for p in values:
            if not 1 <= p <= total:
                raise PdfError(f"页码必须位于 1 到 {total} 之间。")
            if p - 1 not in result:
                result.append(p - 1)
    return result
