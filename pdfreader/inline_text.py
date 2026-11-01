"""A native, IME-capable text editor anchored in displayed PDF coordinates."""
import math
from collections import OrderedDict
import pymupdf as fitz
from PySide6.QtCore import QObject,QEvent,QPointF,QRectF,QTimer,Qt,Slot
from PySide6.QtGui import QColor,QFont,QPen,QTextCursor,QTextCharFormat,QTextBlockFormat,QFontMetricsF
from PySide6.QtWidgets import (QApplication,QPlainTextEdit,QFrame,QHBoxLayout,QComboBox,
    QDoubleSpinBox,QColorDialog,QDialog,QGraphicsProxyWidget)
from .application import shortcut_text
from .fonts import font_choices,qt_family,library_face,qt_original_family,catalog_match
from .icons import icon_button
from .text_layout import layout_text,PADDING

FONT_PROPERTY=0x100001


class TextInput(QPlainTextEdit):
    def insertFromMimeData(self,source):
        size=len(self.toPlainText())-len(self.textCursor().selectedText())+len(source.text())
        if size>25000:
            self.owner.show_error("单个文字框最多 25,000 字符，请拆分文字框。"); return
        super().insertFromMimeData(source)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.owner.view.finish_inline_text(cancel=True); event.accept()
        elif event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.owner.view.finish_inline_text(); event.accept()
        else: super().keyPressEvent(event)


class InlineProxy(QGraphicsProxyWidget):
    def paint(self,painter,option,widget=None):
        if self.owner is None or self.owner.disposed: return
        super().paint(painter,option,widget)
        painter.save()
        pen=QPen(QColor("#c24f45" if self.owner.error else "#4e7ec9"),1,Qt.PenStyle.DashLine)
        pen.setCosmetic(True); painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.boundingRect()); painter.restore()


class InlineTextEditor(QObject):
    def __init__(self,view,number,point,region=None):
        super().__init__(view)
        self.view,self.number,self.point=view,number,QPointF(*point)
        self.region=region; self.original_text=" ".join(region.text.splitlines()) if region else ""
        self.size_modified=self.color_modified=False
        self.angle=0; self.background_document=None; self.background_renderer=None
        self.font_job=None; self.font_attempts=set(); self.native_families={}; self.preview_fonts=OrderedDict()
        self.settings=getattr(view.window(),"settings",None)
        self.updating=False; self.modal=False; self.disposed=False; self.error=""; self.preview_style=None
        self.fontname="native" if region else "library:sans-sc"
        self.color=QColor(region.primary_style.color) if region else QColor("#141f2e"); self.drag=None
        self.input=TextInput(); self.input.owner=self
        # The document and widget must use one DPI, including Cocoa/offscreen.
        self.input.document().documentLayout().setPaintDevice(self.input)
        self.proxy=InlineProxy(); self.proxy.owner=self
        self.proxy.setWidget(self.input); self.proxy.setParentItem(view.items_by_page[number]); self.proxy.setZValue(20)
        if region: self.prepare_original()
        self.original_point=QPointF(self.point)
        self.input.setObjectName("inlinePdfText")
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setPlaceholderText("输入文字")
        self.input.setTabChangesFocus(False)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.installEventFilter(self)
        self.toolbar=QFrame(view.viewport()); self.toolbar.setObjectName("inlineTextTools")
        self.toolbar.setStyleSheet("QFrame#inlineTextTools {background:#fff;border:1px solid #dce3ee;border-radius:6px;}")
        row=QHBoxLayout(self.toolbar); row.setContentsMargins(5,3,5,3); row.setSpacing(3)
        self.move=icon_button("move","拖动移动文字框")
        self.move.setCursor(Qt.CursorShape.SizeAllCursor); self.move.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.move.installEventFilter(self); row.addWidget(self.move)
        self.font=QComboBox(); self.font.setFixedWidth(138)
        self.populate_fonts(self.fontname); row.addWidget(self.font)
        self.size=QDoubleSpinBox(); self.size.setRange(1,500); self.size.setDecimals(1); self.size.setValue(region.primary_style.size if region else 12)
        self.size.setFixedWidth(64); self.size.setSuffix(" pt"); row.addWidget(self.size)
        self.color_button=icon_button("ink","文字颜色",self.choose_color); row.addWidget(self.color_button)
        self.library_button=icon_button("gear","字体库",self.open_library); row.addWidget(self.library_button)
        self.apply=icon_button("check","完成 · "+shortcut_text("Ctrl+Return"),lambda:view.finish_inline_text())
        self.cancel=icon_button("close","取消 · Esc",lambda:view.finish_inline_text(cancel=True))
        row.addWidget(self.apply); row.addWidget(self.cancel)
        self.font.currentIndexChanged.connect(self.font_changed)
        self.size.valueChanged.connect(self.size_changed)
        self.input.textChanged.connect(self.reflow)
        self.input.setToolTip("单击页面其他位置完成；Enter 换行；"+shortcut_text("Ctrl+Return")+" 完成；Esc 取消。")
        if region: self.input.setPlainText(self.original_text)
        self.reflow()
        if region:
            self.input.blockSignals(True)
            self.format_original()
            self.input.blockSignals(False)
            self.reflow()
        self.input.document().setUndoRedoEnabled(False); self.input.document().setUndoRedoEnabled(True)
        self.input.show(); self.proxy.show(); self.toolbar.show()
        self.proxy.setFocus(Qt.FocusReason.MouseFocusReason)
        self.input.setFocus(Qt.FocusReason.MouseFocusReason)

    def populate_fonts(self,selected):
        self.font.blockSignals(True); self.font.clear()
        if self.region: self.font.addItem("原字体 · "+self.region.primary_style.font,"native")
        for label,identity in font_choices(): self.font.addItem(label,identity)
        self.font.setCurrentIndex(max(0,self.font.findData(selected))); self.font.blockSignals(False)

    def eventFilter(self,watched,event):
        if watched is self.input and event.type()==QEvent.Type.FocusOut:
            QTimer.singleShot(0,self.focus_left)
        if watched is self.move:
            if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                self.drag=(event.globalPosition(),QPointF(self.point)); return True
            if event.type()==QEvent.Type.MouseMove and self.drag:
                delta=(event.globalPosition()-self.drag[0])/self.view.transform().m11()
                page=self.view.document.doc[self.number].rect
                self.point=QPointF(max(0,min(page.width-12,self.drag[1].x()+delta.x())),max(0,min(page.height-12,self.drag[1].y()+delta.y())))
                self.reflow(); return True
            if event.type()==QEvent.Type.MouseButtonRelease:
                self.drag=None; self.input.setFocus(); return True
        return False

    def focus_left(self):
        if self.disposed or self.modal: return
        focus=QApplication.focusWidget()
        if self.input.hasFocus(): return
        if focus and (focus is self.input or self.input.isAncestorOf(focus) or focus is self.toolbar or self.toolbar.isAncestorOf(focus)): return
        if QApplication.activePopupWidget() is not None: return
        self.view.finish_inline_text()

    def font_changed(self):
        selected=self.font.currentData()
        if selected=="native": self.fontname=selected; self.apply_format(font=True); self.reflow(); return
        try: library_face(selected[8:])
        except ValueError:
            self.open_library(selected,acquire=True)
            return
        self.fontname=selected; self.apply_format(font=True); self.reflow()

    def open_library(self,selected=None,acquire=False):
        from .font_library import FontLibraryDialog
        self.modal=True
        requested=self.region.primary_style.font if self.region else ""
        dialog=FontLibraryDialog(self.view.window(),requested)
        dialog.refresh(selected or ("library:"+catalog_match(requested) if catalog_match(requested) else self.fontname))
        if acquire: QTimer.singleShot(0,dialog.acquire)
        result=dialog.exec()
        if result==QDialog.DialogCode.Accepted:
            self.fontname="native" if self.region and dialog.selected_font()=="library:"+str(catalog_match(requested)) else dialog.selected_font()
        self.populate_fonts(self.fontname)
        self.modal=False
        if self.fontname!="native": self.apply_format(font=True)
        self.reflow(); self.input.setFocus()

    def choose_color(self):
        self.modal=True
        color=QColorDialog.getColor(self.color,self.view.window(),"文字颜色")
        if color.isValid(): self.color=color; self.color_modified=True; self.apply_format(color=True)
        self.modal=False; self.reflow(); self.input.setFocus()

    def size_changed(self,*_):
        self.size_modified=True; self.apply_format(size=True); self.reflow()

    def prepare_original(self):
        from .engine import PdfDocument
        from .text_edit import write_replacement,editing_bounds
        self.allowed_bounds=editing_bounds(self.view.document.doc[self.number],self.region)
        from .rendering import PageRenderer
        page=self.view.document.doc[self.number]
        direction=fitz.Point(self.region.lines[0].direction)
        vector=(fitz.Point(direction)*page.rotation_matrix)-(fitz.Point(0,0)*page.rotation_matrix)
        self.angle=math.degrees(math.atan2(vector.y,vector.x))
        asc=self.region.primary_style.ascender*self.region.primary_style.size
        origin=fitz.Point(self.region.lines[0].origin)
        top=origin-fitz.Point(-direction.y*asc,direction.x*asc)
        point=top*page.rotation_matrix; self.point=QPointF(point.x,point.y)
        background=PdfDocument(); background.doc=fitz.open()
        background.doc.insert_pdf(self.view.document.doc,from_page=self.number,to_page=self.number)
        try:
            write_replacement(background.doc[0],self.region,[],{})
            self.background_document=background; self.background_renderer=PageRenderer(background,budget=16*1024*1024)
        except Exception: background.close()

    def native_family(self,name,text):
        if name not in self.native_families:
            try: self.native_families[name]=qt_original_family(self.view.document.doc[self.number],name,text,self)
            except (ValueError,RuntimeError): self.native_families[name]=qt_family("library:sans-sc",self)
        return self.native_families[name]

    def format_original(self):
        from .text_edit import mapped_glyphs
        mapped=mapped_glyphs(self.region,self.original_text)
        cursor=self.input.textCursor(); position=0; runs=[]; texts={}
        for char,glyph in zip(self.original_text,mapped):
            length=len(char.encode("utf-16-le"))//2
            if glyph:
                texts[glyph.style.font]=texts.get(glyph.style.font,"")+char
                if runs and runs[-1][2]==glyph.style: runs[-1]=(runs[-1][0],position+length,glyph.style)
                else: runs.append((position,position+length,glyph.style))
            position+=length
        for start,end,style in runs:
            fmt=QTextCharFormat(); font=QFont(self.native_family(style.font,texts[style.font]))
            font.setPointSizeF(style.size*72/self.input.logicalDpiY()); font.setKerning(False)
            font.setBold("bold" in style.font.lower()); font.setItalic(any(x in style.font.lower() for x in ("italic","oblique")))
            fmt.setFont(font); fmt.setForeground(QColor(style.color)); fmt.setProperty(FONT_PROPERTY,style.font)
            cursor.setPosition(start); cursor.setPosition(end,QTextCursor.MoveMode.KeepAnchor); cursor.setCharFormat(fmt)
        cursor.clearSelection(); cursor.setPosition(0); self.input.setTextCursor(cursor)

    def preview_font(self,name,text):
        from .text_edit import resolve_font
        from .fonts import supports,FONT_CACHE_BYTES
        cached=self.preview_fonts.get(name)
        if cached and supports(cached[0].font,text):
            self.preview_fonts.move_to_end(name)
            return cached[0]
        font=resolve_font(self.view.document.doc[self.number],name,text)
        cost=len(font.buffer)
        if cost<=FONT_CACHE_BYTES:
            self.preview_fonts[name]=(font,cost)
            while len(self.preview_fonts)>12 or sum(x[1] for x in self.preview_fonts.values())>FONT_CACHE_BYTES:
                self.preview_fonts.popitem(last=False)
        return font

    def reflow(self,*_):
        if self.updating or self.disposed: return
        self.updating=True
        try:
            page=self.view.document.doc[self.number]
            if len(self.input.toPlainText())>25000: raise ValueError("单个文字框最多 25,000 字符，请拆分文字框。")
            self.error=""
            if self.region:
                from .text_edit import editing_bounds,reflow_replacement
                bounds=self.allowed_bounds
                width=bounds.width; self.margin=0.
                family=self.native_family(self.region.primary_style.font,self.input.toPlainText()) if self.fontname=="native" else qt_family(self.fontname,self)
                try: reflow_replacement(page,self.region,self.input.toPlainText(),styles=self.text_styles(),bounds_override=bounds,resolver=self.preview_font)
                except (ValueError,RuntimeError) as exc:
                    self.error=str(exc); self.auto_acquire()
                self.available_height=bounds.height
            else:
                self.margin=PADDING
                maxwidth=(page.rect.width-self.point.x()) if not self.region else self.region.rect[2]-self.region.rect[0]
                self.layout=layout_text(self.input.toPlainText(),self.size.value(),self.fontname,maxwidth,min_width=maxwidth if self.region else 144)
                width=self.layout.width
                self.available_height=(page.rect.height-self.point.y()) if not self.region else __import__("pdfreader.text_edit",fromlist=["editing_bounds"]).editing_bounds(page,self.region).height
                self.error="文字已到达页面底部；请移动文字框、减少内容或调整字号。" if self.layout.height>self.available_height+.01 else ""
                for identity in self.layout.fonts: qt_family("library:"+identity,self)
                family=qt_family(self.fontname,self)
            font=QFont(family); font.setKerning(False)
            font.setPointSizeF(self.size.value()*72/self.input.logicalDpiY())
            if self.fontname!="native": font.setBold(bool(library_face(self.fontname[8:]).flags.get("bold")))
            style=(family,self.size.value(),self.color.name(),font.bold())
            if style!=self.preview_style:
                self.preview_style=style
                safe_family=family.replace("'","")
                self.input.setStyleSheet("QPlainTextEdit#inlinePdfText {background:transparent;border:0;padding:0;color:"+self.color.name()+";font-family:'"+safe_family+"';font-size:"+f"{self.size.value():g}"+"px;}")
                self.input.setFont(font); self.input.document().setDefaultFont(font)
                if not self.region:
                    # An inherited widget format can override QTextDocument's
                    # default font on Cocoa. Set the typing format explicitly.
                    self.input.blockSignals(True)
                    previous=self.input.textCursor(); cursor=self.input.textCursor()
                    fmt=QTextCharFormat(); fmt.setFont(font); fmt.setForeground(self.color)
                    if self.input.toPlainText():
                        cursor.select(QTextCursor.SelectionType.Document); cursor.setCharFormat(fmt)
                        self.input.setTextCursor(previous)
                    self.input.setCurrentCharFormat(fmt)
                    self.input.blockSignals(False)
            self.input.document().setDocumentMargin(self.margin)
            if not self.region:
                metrics=QFontMetricsF(font,self.input)
                native_width=max((metrics.horizontalAdvance(line) for line in self.input.toPlainText().replace("\t","    ").split("\n")),default=0)+self.margin*2+2
                width=min(maxwidth,max(width,native_width))
            self.input.setFixedWidth(math.ceil(width))
            block=self.input.document().begin(); native_height=self.margin*2
            while block.isValid():
                native_height+=self.input.document().documentLayout().blockBoundingRect(block).height()
                block=block.next()
            height=max(self.region.rect[3]-self.region.rect[1] if self.region else self.layout.height,native_height)
            self.body_width=width; self.body_height=max(16,min(math.ceil(height)+2,self.available_height))
            self.position()
        except (ValueError,RuntimeError) as exc:
            self.error=str(exc)
        finally: self.updating=False

    def text_styles(self):
        result=[]; block=self.input.document().begin()
        default=self.region.primary_style.font if self.region else self.fontname
        default_size=self.input.document().defaultFont().pointSizeF()
        while block.isValid():
            iterator=block.begin()
            while not iterator.atEnd():
                fragment=iterator.fragment()
                if fragment.isValid():
                    fmt=fragment.charFormat()
                    style={"font":fmt.property(FONT_PROPERTY) or default,
                           "size":(fmt.fontPointSize() or default_size)*self.input.logicalDpiY()/72,
                           "color":fmt.foreground().color().rgb()&0xffffff}
                    result.extend([style]*len(fragment.text()))
                iterator+=1
            block=block.next()
            if block.isValid(): result.append({"font":default,"size":self.size.value(),"color":self.color.rgb()&0xffffff})
        return result

    def apply_format(self,font=False,size=False,color=False):
        if not self.region: return
        self.input.blockSignals(True)
        try:
            cursor=self.input.textCursor(); previous=self.input.textCursor()
            if not cursor.hasSelection(): cursor.select(QTextCursor.SelectionType.Document)
            if font:
                from .text_edit import mapped_glyphs
                from .fonts import character_font
                text=self.input.toPlainText(); mapped=mapped_glyphs(self.region,text)
                start,end=cursor.selectionStart(),cursor.selectionEnd(); position=0
                cursor.beginEditBlock()
                for index,char in enumerate(text):
                    length=len(char.encode("utf-16-le"))//2
                    if start<=position<end:
                        identity=mapped[index].style.font if self.fontname=="native" and mapped[index] else self.fontname
                        fmt=QTextCharFormat()
                        if identity.startswith("library:"):
                            identity=character_font(identity,char)
                            face=library_face(identity[8:]); family=qt_family(identity,self)
                            bold=bool(face.flags.get("bold")); italic=bool(face.flags.get("italic"))
                        else:
                            family=self.native_family(identity,char)
                            bold="bold" in identity.lower(); italic=any(x in identity.lower() for x in ("italic","oblique"))
                        fmt.setFontFamilies([family]); fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
                        fmt.setFontItalic(italic); fmt.setProperty(FONT_PROPERTY,identity)
                        cursor.setPosition(position); cursor.setPosition(position+length,QTextCursor.MoveMode.KeepAnchor); cursor.mergeCharFormat(fmt)
                    position+=length
                cursor.endEditBlock()
            else:
                fmt=QTextCharFormat()
                if size: fmt.setFontPointSize(self.size.value()*72/self.input.logicalDpiY())
                if color: fmt.setForeground(self.color)
                cursor.mergeCharFormat(fmt)
            self.input.setTextCursor(previous)
        finally: self.input.blockSignals(False)

    def auto_acquire(self):
        if self.font_job or not self.region or not self.settings or not self.settings.values.get("auto_font_download"): return
        from .fonts import catalog_path
        candidates=[catalog_match(g.style.font) for g in self.region.glyphs]
        identity=next((x for x in candidates if x and x not in self.font_attempts and catalog_path(x) is None),None)
        if identity:
            from .font_download import FontDownload
            self.font_attempts.add(identity); self.font_job=FontDownload(identity,self)
            self.font_job.completed.connect(self.font_ready); self.font_job.failed.connect(self.font_failed)
            self.font_job.start()

    @Slot(str)
    def font_ready(self,_):
        self.font_job=None; self.native_families.clear()
        if not self.disposed: self.reflow()

    @Slot(str)
    def font_failed(self,message):
        self.font_job=None
        if not self.disposed: self.show_error(message)

    def detach(self):
        self.proxy.setParentItem(None)
        if self.proxy.scene(): self.proxy.scene().removeItem(self.proxy)

    def position(self):
        if self.disposed or not hasattr(self,"body_width") or self.number>=len(self.view.items_by_page): return
        parent=self.view.items_by_page[self.number]
        if self.proxy.parentItem() is not parent: self.proxy.setParentItem(parent)
        self.proxy.setPos(self.point); self.proxy.setRotation(self.angle)
        self.proxy.resize(self.body_width,self.body_height)
        point=self.view.mapFromScene(parent.mapToScene(self.point))
        self.toolbar.adjustSize()
        x=max(2,min(point.x(),self.view.viewport().width()-self.toolbar.width()-2))
        y=point.y()-self.toolbar.height()-5
        if y<0: y=point.y()+math.ceil(self.body_height*self.view.transform().m11())+5
        self.toolbar.move(x,max(0,min(y,self.view.viewport().height()-self.toolbar.height())))
        self.toolbar.raise_()

    def payload(self):
        if self.error: raise ValueError(self.error)
        if self.region:
            return {"region":self.region,"text":self.input.toPlainText(),"fontname":None if self.fontname=="native" else self.fontname,
                    "size":self.size.value() if self.size_modified or self.fontname!="native" else None,
                    "color":self.color.rgb()&0xffffff if self.color_modified or self.fontname!="native" else None,"styles":self.text_styles(),
                    "offset":self.unrotated_offset()}
        return {"point":(self.point.x(),self.point.y()),"text":self.input.toPlainText(),"size":self.size.value(),
                "fontname":self.fontname,"color":(self.color.redF(),self.color.greenF(),self.color.blueF())}

    def unrotated_offset(self):
        page=self.view.document.doc[self.number]
        delta=self.point-self.original_point
        point=fitz.Point(delta.x(),delta.y())*page.derotation_matrix-fitz.Point(0,0)*page.derotation_matrix
        return tuple(point)

    def show_error(self,message):
        self.error=message
        self.input.setToolTip(message)
        self.view.message.emit(message)

    def dispose(self):
        self.disposed=True
        from .fonts import release_fonts
        release_fonts(self)
        if self.font_job: self.font_job.cancel()
        self.proxy.hide(); self.toolbar.hide(); self.detach()
        self.proxy.owner=None; self.input.owner=None
        self.proxy.deleteLater(); self.toolbar.deleteLater()
        if self.background_renderer: self.background_renderer.clear()
        if self.background_document: self.background_document.close()
        self.background_renderer=self.background_document=self.region=None
        self.preview_fonts.clear(); self.native_families.clear()
        self.deleteLater()
