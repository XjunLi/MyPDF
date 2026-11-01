"""Capture native PDF text styles and lay out replacements without shrinking.

Unchanged glyphs keep their spacing; inserted glyphs inherit the nearby style.
Fonts must be available and contain every requested glyph. No silent fallback.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from collections import Counter
import hashlib
import math
import os
import re
import pymupdf as fitz

FILLED = fitz.mupdf.FZ_STEXT_FILLED
STROKED = fitz.mupdf.FZ_STEXT_STROKED


class TextEditError(ValueError):
    pass


@dataclass(frozen=True)
class TextStyle:
    font: str
    size: float
    color: int
    alpha: int = 255
    char_flags: int = FILLED
    ascender: float = 1.1
    descender: float = -.3

    @property
    def rgb(self):
        return tuple(((self.color >> shift) & 255) / 255 for shift in (16, 8, 0))


@dataclass
class EditGlyph:
    text: str
    style: TextStyle
    origin: tuple
    rect: tuple
    line: int
    advance: float = 0


@dataclass
class EditLine:
    origin: tuple
    direction: tuple
    glyphs: list


@dataclass
class TextRegion:
    rect: tuple
    lines: list
    glyphs: list
    text: str

    @property
    def styles(self):
        return set(g.style for g in self.glyphs)

    @property
    def primary_style(self):
        return Counter(g.style for g in self.glyphs).most_common(1)[0][0] if self.glyphs else None

    @property
    def invisible(self):
        return bool(self.glyphs) and all(g.style.alpha == 0 or not (g.style.char_flags & (FILLED | STROKED))
                                        or g.style.font == "GlyphLessFont" for g in self.glyphs)


def capture_region(page, rect):
    region = fitz.Rect(rect)
    lines, glyphs, texts = [], [], []
    for block in page.get_text("rawdict", sort=True, flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines", []):
            chars = []
            for span in line["spans"]:
                style = TextStyle(span["font"], span["size"], span["color"], span.get("alpha", 255),
                                  span.get("char_flags", FILLED), span.get("ascender", 1.1), span.get("descender", -.3))
                for char in span["chars"]:
                    box = fitz.Rect(char["bbox"])
                    if region.contains((box.tl + box.br) / 2):
                        chars.append(EditGlyph(char["c"], style, tuple(char["origin"]), tuple(box), len(lines)))
            if not chars:
                continue
            direction = tuple(line.get("dir", (1, 0)))
            for a, b in zip(chars, chars[1:]):
                a.advance = ((b.origin[0] - a.origin[0]) * direction[0] +
                             (b.origin[1] - a.origin[1]) * direction[1])
            lines.append(EditLine(chars[0].origin, direction, chars))
            glyphs.extend(chars)
            texts.append("".join(g.text for g in chars))
    return TextRegion(tuple(region), lines, glyphs, "\n".join(texts))


def normalized_font(name):
    name = re.sub(r"-\d+$", "", re.sub(r"^[A-Z]{6}\+", "", name))
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return key.removesuffix("regular").removesuffix("psmt").removesuffix("mt")


# Exact local faces help PDFs whose embedded subsets lack newly typed glyphs.
# These files are read from Windows; they are never distributed with the app.
SYSTEM_FACES = {
    "microsoftyahei": "msyh.ttc", "microsoftyaheibold": "msyhbd.ttc",
    "microsoftyaheilight": "msyhl.ttc", "simsun": "simsun.ttc", "simhei": "simhei.ttf",
    "arialmt": "arial.ttf", "arialboldmt": "arialbd.ttf", "arialitalicmt": "ariali.ttf",
    "arialbolditalicmt": "arialbi.ttf", "timesnewromanpsmt": "times.ttf",
    "timesnewromanpsboldmt": "timesbd.ttf", "timesnewromanpsitalicmt": "timesi.ttf",
    "timesnewromanpsbolditalicmt": "timesbi.ttf", "segoeui": "segoeui.ttf",
    "segoeuibold": "segoeuib.ttf", "segoeuiitalic": "segoeuii.ttf",
    "calibri": "calibri.ttf", "calibribold": "calibrib.ttf", "calibriitalic": "calibrii.ttf",
    "couriernewpsmt": "cour.ttf", "couriernewpsboldmt": "courbd.ttf",
}


@dataclass
class WritableFont:
    font: object
    name: str
    buffer: bytes = b""

    def install(self, page):
        page.insert_font(fontname=self.name, fontbuffer=self.buffer or None)


def resolve_font(page, name, text):
    key = normalized_font(name)
    if name.startswith("library:"):
        from .fonts import library_face
        font=library_face(name[8:])
        if not all(c.isspace() or font.has_glyph(ord(c),fallback=False) for c in set(text)):
            raise TextEditError("所选字体缺少部分字形，请选择包含这些字符的字体。")
        data=font.buffer
        return WritableFont(font,"PREdit"+hashlib.sha1(data).hexdigest()[:12],data)
    supports = lambda font: all(c.isspace() or font.has_glyph(ord(c), fallback=False) for c in set(text))
    for code, fullname in fitz.Base14_fontdict.items():
        if key == normalized_font(fullname):
            font = fitz.Font(code)
            if supports(font):
                return WritableFont(font, code)
    for fullname, code in {"Heiti": "china-s", "Song": "china-ss", "Fangti": "china-t",
                           "Ming": "china-ts", "Gothic": "japan", "Mincho": "japan-s"}.items():
        if key == normalized_font(fullname):
            font = fitz.Font(code)
            if supports(font):
                return WritableFont(font, code)
    for item in page.get_fonts(full=True):
        if normalized_font(item[3]) != key or not item[0]:
            continue
        try:
            data = page.parent.extract_font(item[0])[3]
            if data:
                font = fitz.Font(fontbuffer=data)
                if supports(font):
                    return WritableFont(font, "PREdit" + hashlib.sha1(data).hexdigest()[:12], data)
        except (RuntimeError, ValueError):
            continue
    filename = next((filename for face, filename in SYSTEM_FACES.items() if normalized_font(face) == key), None)
    if filename:
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if path.exists():
            font = fitz.Font(fontfile=str(path))
            if normalized_font(font.name) == key and supports(font):
                data = font.buffer
                return WritableFont(font, "PREdit" + hashlib.sha1(data).hexdigest()[:12], data)
    from .fonts import exact_local_face
    font=exact_local_face(name,text)
    if font is not None:
        data=font.buffer
        return WritableFont(font,"PREdit"+hashlib.sha1(data).hexdigest()[:12],data)
    raise TextEditError(f"原字体「{name}」不可用，或没有新增文字所需的字形。请获取原字体、导入本机字体，或明确选择替代字体。")


def mapped_glyphs(region,replacement):
    original=[]
    for line in region.lines:
        if original: original.append(None)
        original.extend(line.glyphs)
    before=region.text
    prefix=0; limit=min(len(before),len(replacement))
    while prefix<limit and before[prefix]==replacement[prefix]: prefix+=1
    suffix=0
    while suffix<limit-prefix and before[-suffix-1]==replacement[-suffix-1]: suffix+=1
    result=[None]*len(replacement)
    result[:prefix]=original[:prefix]
    if suffix: result[-suffix:]=original[-suffix:]
    old_end=len(before)-suffix; new_end=len(replacement)-suffix
    left=before[prefix:old_end]; right=replacement[prefix:new_end]
    matcher=SequenceMatcher(None,left,right,autojunk=max(len(left),len(right))>2000)
    for tag,i,j,a,b in matcher.get_opcodes():
        i+=prefix; j+=prefix; a+=prefix; b+=prefix
        if tag=="equal": result[a:b]=original[i:j]
        elif tag in ("replace","insert"):
            candidates=[g for g in original[i:j] if g is not None]
            if not candidates:
                candidates=[next((g for g in reversed(original[:i]) if g),None) or next((g for g in original[j:] if g),region.glyphs[0])]
            for index in range(a,b): result[index]=candidates[min(index-a,len(candidates)-1)]
    return result


def replacement_plan(page, region, text, mapped_override=None):
    if not region.glyphs or region.invisible:
        raise TextEditError("选区没有可编辑的可见文字。扫描页请使用扫描件覆写，并选择替代字体。")
    mapped = mapped_override if mapped_override is not None else mapped_glyphs(region,text)
    by_font = {}
    for char, glyph in zip(text, mapped):
        if char != "\n" and glyph:
            by_font[glyph.style.font] = by_font.get(glyph.style.font, "") + char
    fonts = {name: resolve_font(page, name, chars) for name, chars in by_font.items()}
    box = fitz.Rect(region.rect) + (-.8, -.8, .8, .8)
    plan, cursor = [], 0
    for n, line_text in enumerate(text.split("\n")):
        template = region.lines[min(n, len(region.lines) - 1)]
        dx, dy = template.direction
        angle = (-math.degrees(math.atan2(dy, dx))) % 360
        rotation = round(angle / 90) * 90 % 360
        if min(abs(angle - rotation), abs(angle - rotation - 360)) > .01:
            raise TextEditError("这段文字带有倾斜变换，暂不能原样编辑；原文未改动。")
        origin = fitz.Point(template.origin)
        if n >= len(region.lines):
            extra = (n - len(region.lines) + 1) * max(g.style.size for g in template.glyphs) * 1.4
            origin += fitz.Point(-dy * extra, dx * extra)
        along = 0
        for char in line_text:
            old = mapped[cursor]
            cursor += 1
            style = old.style
            if not (style.char_flags & FILLED) or style.char_flags & STROKED:
                raise TextEditError("这段文字使用了特殊描边或不可见样式，暂不能原样编辑；原文未改动。")
            font = fonts[style.font]
            advance = old.advance if old.text == char and old.advance > 0 else font.font.glyph_advance(ord(char)) * style.size
            # Keep baseline offsets such as superscripts within the original line.
            old_line = region.lines[old.line]
            normal = ((old.origin[0] - old_line.origin[0]) * -dy + (old.origin[1] - old_line.origin[1]) * dx)
            point = origin + fitz.Point(along * dx - normal * dy, along * dy + normal * dx)
            asc, desc = style.ascender * style.size, style.descender * style.size
            corners = [point + fitz.Point(dx * x - dy * y, dy * x + dx * y)
                       for x in (0, advance) for y in (-asc, -desc)]
            bounds = fitz.Rect(min(p.x for p in corners), min(p.y for p in corners),
                               max(p.x for p in corners), max(p.y for p in corners))
            if not char.isspace() and not box.contains(bounds):
                raise TextEditError("按原字号排版后文字超出选框。请减少文字、调整换行，或重新框选更大的区域；不会自动缩小字号。")
            plan.append((point, char, style, font.name, rotation))
            along += advance
        cursor += 1  # explicit line break
    return plan, fonts


def write_replacement(page, region, plan, fonts):
    if any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in (page.annots() or [])):
        raise TextEditError("本页含有尚未应用的遮盖标记。请先处理这些标记，再替换文字。")
    # Small interior rectangles remove only the intended glyphs, not neighbours
    # whose italic glyph boxes overlap the outside of the selected region.
    for glyph in region.glyphs:
        r = fitz.Rect(glyph.rect)
        if not r.is_empty:
            page.add_redact_annot(r + (r.width * .35, r.height * .35, -r.width * .35, -r.height * .35), fill=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    if not plan: return
    bounds=page.rect*page.derotation_matrix
    with fitz.open() as overlay:
        target=overlay.new_page(width=bounds.width,height=bounds.height)
        from .fonts import subset_font
        for font in fonts.values():
            if font.buffer:
                used="".join(char for _,char,_,name,_ in plan if name==font.name)
                if used: target.insert_font(fontname=font.name,fontbuffer=subset_font(font.font,used))
            else: font.install(target)
        shape=target.new_shape()
        for point,char,style,fontname,rotation in plan:
            shape.insert_text(point,char,fontsize=style.size,fontname=fontname,color=style.rgb,
                              rotate=rotation,fill_opacity=style.alpha/255)
        shape.commit()
        page.show_pdf_page(bounds,overlay,0)


def editing_bounds(page,region):
    box=fitz.Rect(region.rect)
    bounds=page.rect*page.derotation_matrix
    bottom=bounds.y1-6
    originals={tuple(line.origin) for line in region.lines}
    obstacles=[]
    for block in page.get_text("dict",flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines",[]):
            if not line["spans"]: continue
            if tuple(line["spans"][0]["origin"]) not in originals: obstacles.append(fitz.Rect(line["bbox"]))
    obstacles += [fitz.Rect(b[:4]) for b in page.get_text("blocks") if b[6]!=0]
    for other in obstacles:
        if other.x0<box.x1 and other.x1>box.x0 and other.y0>=box.y1-.1: bottom=min(bottom,other.y0-2)
    for drawing in page.get_drawings():
        panel=fitz.Rect(drawing["rect"])
        if panel.contains(box) and panel.y1>box.y1+4: bottom=min(bottom,panel.y1-4)
    box.y1=max(box.y1,bottom)
    return box


def reflow_replacement(page,region,text,size=None,color=None,styles=None,bounds_override=None):
    from dataclasses import replace
    original=region
    if size is not None or color is not None:
        glyphs=[]; lines=[]
        for line in region.lines:
            changed=[]
            for glyph in line.glyphs:
                newsize=size if size is not None else glyph.style.size
                style=replace(glyph.style,size=newsize,color=color if color is not None else glyph.style.color)
                changed.append(replace(glyph,style=style,advance=glyph.advance*newsize/glyph.style.size))
            glyphs.extend(changed); lines.append(replace(line,glyphs=changed))
        region=replace(region,glyphs=glyphs,lines=lines)
    bounds=fitz.Rect(bounds_override) if bounds_override is not None else editing_bounds(page,original)
    # Orthogonal vertical text retains the established replacement path.
    if any(abs(line.direction[1])>.01 for line in region.lines):
        return replacement_plan(page,region,text)
    mapped=mapped_glyphs(region,text)
    if styles is not None:
        if len(styles)!=len(text): raise TextEditError("文字样式数量与内容不符。")
        from dataclasses import replace
        for index,(char,style) in enumerate(zip(text,styles)):
            if char=="\n" or not mapped[index]: continue
            old=mapped[index]
            newstyle=replace(old.style,font=style["font"],size=style["size"],color=style["color"])
            keep=old.style.font==newstyle.font and abs(old.style.size-newstyle.size)<.01
            mapped[index]=replace(old,style=newstyle,advance=old.advance if keep else 0)
    fonts={}
    for char,glyph in zip(text,mapped):
        if glyph and char!="\n":
            fonts[glyph.style.font]=fonts.get(glyph.style.font,"")+char
    resolved={name:resolve_font(page,name,chars) for name,chars in fonts.items()}
    lines=[]; current=[]; advances=[]; mapped_lines=[]; current_maps=[]
    for char,glyph in zip(text,mapped):
        if char=="\n":
            lines.append("".join(current)); mapped_lines.append(current_maps)
            current=[]; advances=[]; current_maps=[]; continue
        if glyph is None: continue
        face=resolved[glyph.style.font].font
        advance=glyph.advance if glyph.text==char and glyph.advance>0 else face.glyph_advance(ord(char))*glyph.style.size
        if advance>bounds.width+.01: raise TextEditError("当前区域放不下此字号。")
        if current and sum(advances)+advance>bounds.width:
            split=next((i+1 for i in range(len(current)-1,-1,-1) if current[i].isspace()),0)
            if split:
                lines.append("".join(current[:split])); mapped_lines.append(current_maps[:split])
                current=current[split:]; advances=advances[split:]; current_maps=current_maps[split:]
            if current and sum(advances)+advance>bounds.width:
                lines.append("".join(current)); mapped_lines.append(current_maps)
                current=[]; advances=[]; current_maps=[]
        current.append(char); advances.append(advance); current_maps.append(glyph)
    lines.append("".join(current)); mapped_lines.append(current_maps)
    from dataclasses import replace
    templates=list(region.lines)
    step=max(g.style.size for g in mapped if g)*1.4 if any(mapped) else 16
    if len(templates)>1:
        step=templates[-1].origin[1]-templates[-2].origin[1]
    while len(templates)<len(lines):
        last=templates[-1]
        templates.append(replace(last,origin=(last.origin[0],last.origin[1]+step)))
    # A changed size may need more ascent while keeping the existing baseline.
    if any(mapped):
        top=min(region.lines[g.line].origin[1]-g.style.ascender*g.style.size for g in mapped if g)
        bounds.y0=max(0,min(bounds.y0,top))
    expanded=replace(region,rect=tuple(bounds),lines=templates)
    wrapped=[]
    for number,line in enumerate(mapped_lines):
        if number: wrapped.append(None)
        wrapped.extend(line)
    return replacement_plan(page,expanded,"\n".join(lines),mapped_override=wrapped)
