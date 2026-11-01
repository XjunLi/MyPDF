"""PDF-point text layout shared by the inline editor and PDF writer."""
from dataclasses import dataclass
import math
import pymupdf as fitz
from .fonts import library_face,subset_font

PADDING=3.0
DEFAULT_WIDTH=144.0


@dataclass
class TextLayout:
    width: float
    height: float
    line_height: float
    ascender: float
    lines: list
    fonts: dict


def layout_text(text,size=12,fontname="library:sans-sc",max_width=500,min_width=DEFAULT_WIDTH):
    if not math.isfinite(size) or not 1<=size<=500: raise ValueError("字号需要在 1–500 pt 之间。")
    if len(text)>25000: raise ValueError("单个文字框最多 25,000 字符，请拆分文字框。")
    identity=fontname.removeprefix("library:")
    primary=library_face(identity)
    fonts={identity:primary}; glyphs={}
    for char in set(text.replace("\t","    "))-{"\n","\r"}:
        chosen=identity
        if not char.isspace() and not primary.has_glyph(ord(char),fallback=False):
            for fallback in ("sans-sc","sans","math","symbols"):
                candidate=fonts.get(fallback)
                if candidate is None:
                    candidate=library_face(fallback); fonts[fallback]=candidate
                if candidate.has_glyph(ord(char),fallback=False): chosen=fallback; break
            else: raise ValueError(f"字体库没有字符 U+{ord(char):04X}（{char}），请导入包含该字形的字体。")
        glyphs[char]=(chosen,fonts[chosen].glyph_advance(ord(char))*size)
    paragraphs=text.replace("\r\n","\n").replace("\r","\n").replace("\t","    ").split("\n")
    natural=max((sum(glyphs[c][1] for c in line) for line in paragraphs),default=0)
    max_width=max(0,float(max_width))
    width=min(max_width,max(min_width,natural+PADDING*2))
    usable=width-PADDING*2
    if usable<=0: raise ValueError("此处没有足够空间放置文字，请换一个位置。")
    lines=[]
    for paragraph in paragraphs:
        current=[]; used=0
        for char in paragraph:
            face,advance=glyphs[char]
            if advance>usable+.01: raise ValueError("此处放不下当前字号，请换一个位置或调整字号。")
            if current and used+advance>usable+.01:
                # Break Latin text at whitespace when possible; CJK can wrap anywhere.
                split=next((n+1 for n in range(len(current)-1,-1,-1) if current[n][0].isspace()),0)
                if split:
                    lines.append(current[:split]); current=current[split:]
                    used=sum(x[2] for x in current)
                if current and used+advance>usable+.01:
                    lines.append(current); current=[]; used=0
            current.append((char,face,advance)); used+=advance
        lines.append(current)
    ascender=max([primary.ascender]+[font.ascender for font in fonts.values()])*size
    descender=min([primary.descender]+[font.descender for font in fonts.values()])*size
    line_height=max(size*1.4,ascender-descender)
    height=PADDING*2+ascender-descender+max(0,len(lines)-1)*line_height
    return TextLayout(width,height,line_height,ascender,lines,fonts)


def write_layout(page,point,layout,size,color,display_coordinates=False):
    # Subset only the newly added fonts in an isolated document; original PDF
    # fonts, graphics and annotations are never resubsetted or substituted.
    dimensions=page.rect if display_coordinates else page.cropbox
    with fitz.open() as overlay:
        target=overlay.new_page(width=dimensions.width,height=dimensions.height)
        names={}
        for identity,font in layout.fonts.items():
            used="".join(char for line in layout.lines for char,face,_ in line if face==identity)
            if not used: continue
            name="FR"+str(len(names)); names[identity]=name
            target.insert_font(fontname=name,fontbuffer=subset_font(font,used))
        shape=target.new_shape()
        for line_number,line in enumerate(layout.lines):
            x=point[0]+PADDING
            y=point[1]+PADDING+layout.ascender+line_number*layout.line_height
            start=0
            while start<len(line):
                end=start+1
                while end<len(line) and line[end][1]==line[start][1]: end+=1
                text="".join(x[0] for x in line[start:end])
                shape.insert_text((x,y),text,fontname=names[line[start][1]],fontsize=size,color=color)
                x+=sum(x[2] for x in line[start:end]); start=end
        shape.commit()
        bounds=page.rect*page.derotation_matrix if display_coordinates else fitz.Rect(0,0,dimensions.width,dimensions.height)
        page.show_pdf_page(bounds,overlay,0,rotate=page.rotation if display_coordinates else 0)
