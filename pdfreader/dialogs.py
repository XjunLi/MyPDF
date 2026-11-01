from PySide6.QtCore import Qt, QPointF, QTimer, Slot
import re
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QImageReader
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QDialogButtonBox, QPlainTextEdit, QSpinBox, QPushButton, QFormLayout,
    QLineEdit, QComboBox, QCheckBox, QDoubleSpinBox, QColorDialog, QTabWidget, QFileDialog)


class DisposableDialog(QDialog):
    def done(self,result):
        super().done(result)
        self.deleteLater()


class TextDialog(DisposableDialog):
    def __init__(self, parent, text="", editing=False, region=None, pdf_page=None):
        super().__init__(parent)
        self.settings=getattr(parent,"settings",None)
        self.region=region
        self.pdf_page=pdf_page
        self.font_job=None
        self.font_attempts=set()
        self.setWindowTitle("修改 PDF 文字" if editing else "添加文字")
        self.resize(510, 420)
        layout = QVBoxLayout(self)
        native = bool(region and region.glyphs and not region.invisible)
        style = region.primary_style if region else None
        note = QLabel("默认沿用原字体、字号和颜色，不会自动缩小字号。" if native else
                      "扫描页的字在图像里，OCR 不含原始字体；覆写会清除选框内图像背景。" if region and region.invisible else
                      "文字将写入 PDF。超出选框时请调整文字或重新绘制更大的框。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.text = QPlainTextEdit(text)
        self.text.setPlaceholderText("输入文字，支持中文和英文…")
        layout.addWidget(self.text)
        self.preserve = QCheckBox("保留原文样式")
        self.preserve.setChecked(native)
        self.preserve.setVisible(native)
        layout.addWidget(self.preserve)
        if native:
            name = re.sub(r"-\d+$", "", style.font.split("+")[-1]).replace("MicrosoftYaHei", "微软雅黑").replace("-Bold", " 粗体")
            description = f"{name} · {style.size:g} pt" if len(region.styles) == 1 else f"原文包含 {len(region.styles)} 种样式，将分别保留字体、字号和颜色。"
            original = QLabel(description)
            original.setObjectName("muted")
            original.setWordWrap(True)
            layout.addWidget(original)
        form = QFormLayout()
        self.font = QComboBox()
        for label, code in [("自动（中英文）", None), ("Helvetica", "helv"), ("Helvetica Bold", "hebo"),
                            ("Times Roman", "tiro"), ("Times Italic", "tiit"), ("Courier", "cour")]:
            self.font.addItem(label, code)
        from .fonts import font_choices
        for label,identity in font_choices(): self.font.addItem(label,identity)
        form.addRow("替代字体" if editing else "字体", self.font)
        self.size = QDoubleSpinBox()
        self.size.setRange(1, 500)
        self.size.setDecimals(2)
        self.size.setValue(style.size if style else 11)
        form.addRow("字号 (pt)", self.size)
        self.color = QColor(style.color) if style and native else QColor("#141f2e")
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self.choose_color)
        self.update_color()
        form.addRow("文字颜色", self.color_button)
        self.custom_style = QWidget()
        self.custom_style.setLayout(form)
        self.custom_style.setVisible(not native)
        self.preserve.toggled.connect(lambda checked: self.custom_style.setVisible(not checked))
        layout.addWidget(self.custom_style)
        font_row=QHBoxLayout()
        self.font_status=QLabel()
        self.font_status.setWordWrap(True)
        font_row.addWidget(self.font_status,1)
        self.library_button=QPushButton("字体库…")
        self.library_button.clicked.connect(self.open_font_library)
        font_row.addWidget(self.library_button)
        layout.addLayout(font_row)
        self.font_timer=QTimer(self)
        self.font_timer.setSingleShot(True); self.font_timer.setInterval(180)
        self.font_timer.timeout.connect(self.check_original_fonts)
        self.text.textChanged.connect(lambda:self.font_timer.start())
        if native: self.font_timer.start()
        self.raster_replace = QCheckBox("扫描件覆写：清除选框内图像，以白底重新写入")
        self.raster_replace.setVisible(editing and not native)
        self.preserve.toggled.connect(lambda checked: self.raster_replace.setVisible(editing and not checked))
        self.raster_replace.setChecked(bool(region and region.invisible))
        self.raster_replace.setToolTip("用于修改扫描页上的旧字。选框内的原图像会被删除；请仔细框选。")
        layout.addWidget(self.raster_replace)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用修改" if editing else "添加")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def open_font_library(self):
        from .font_library import FontLibraryDialog
        requested=self.region.primary_style.font if self.region and self.region.primary_style else ""
        dialog=FontLibraryDialog(self,requested)
        if dialog.exec():
            identity=dialog.selected_font()
            from .fonts import font_choices
            selected=self.font.currentData()
            while self.font.count()>6: self.font.removeItem(6)
            for label,value in font_choices(): self.font.addItem(label,value)
            self.font.setCurrentIndex(max(0,self.font.findData(identity)))
            if self.region:
                from .fonts import catalog_match
                exact=catalog_match(requested)
                self.preserve.setChecked(bool(exact and identity=="library:"+exact))
            self.check_original_fonts()

    def check_original_fonts(self):
        if not self.region or not self.preserve.isChecked(): return
        from .text_edit import mapped_glyphs,resolve_font,TextEditError
        from .fonts import catalog_match
        text=self.text.toPlainText()
        by_font={}
        for char,glyph in zip(text,mapped_glyphs(self.region,text)):
            if glyph and char!="\n": by_font[glyph.style.font]=by_font.get(glyph.style.font,"")+char
        missing=[]
        for name,chars in by_font.items():
            try: resolve_font(self.pdf_page,name,chars)
            except (TextEditError,RuntimeError,ValueError): missing.append(name)
        self.font_status.setText("缺少字形或原字体："+"、".join(missing)+"。可在字体库获取、导入或选择替代。" if missing else "")
        if missing and self.settings and self.settings.values.get("auto_font_download") and self.font_job is None:
            identity=next((match for name in missing if (match:=catalog_match(name)) and match not in self.font_attempts),None)
            if identity:
                from .font_download import FontDownload
                self.font_attempts.add(identity)
                self.font_job=FontDownload(identity,self)
                self.font_job.completed.connect(self.font_ready); self.font_job.failed.connect(self.font_failed)
                self.font_status.setText("正在获取缺失的开源字体…")
                self.font_job.start()

    @Slot(str)
    def font_ready(self,_):
        self.font_job=None
        self.check_original_fonts()

    @Slot(str)
    def font_failed(self,message):
        self.font_job=None
        self.font_status.setText(message)

    def done(self,result):
        if self.font_job: self.font_job.cancel()
        super().done(result)

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "文字颜色")
        if color.isValid():
            self.color = color
            self.update_color()

    def update_color(self):
        self.color_button.setText(self.color.name().upper())
        self.color_button.setStyleSheet(f"color:{self.color.name()};")

    def rgb(self):
        return (self.color.redF(), self.color.greenF(), self.color.blueF())


class CommentDialog(DisposableDialog):
    def __init__(self, parent, text="", quote="", editing=False):
        super().__init__(parent)
        self.setWindowTitle("修改注释" if editing else "文字注释" if quote else "添加便笺")
        self.resize(450, 320 if quote else 240)
        layout = QVBoxLayout(self)
        if quote:
            label = QLabel("引用原文")
            label.setObjectName("muted")
            layout.addWidget(label)
            excerpt = QPlainTextEdit(quote)
            excerpt.setReadOnly(True)
            excerpt.setMaximumHeight(90)
            layout.addWidget(excerpt)
        self.text = QPlainTextEdit(text)
        self.text.setPlaceholderText("写下你的想法…")
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存注释")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.text.setFocus()


class SignaturePad(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(500, 220)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.strokes = []
        self.color=QColor("#203651")
        self.active = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#dfe5ec"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(25, self.height() - 50, self.width() - 25, self.height() - 50)
        painter.setPen(QPen(self.color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for stroke in self.strokes:
            for a, b in zip(stroke, stroke[1:]):
                painter.drawLine(QPointF(*a), QPointF(*b))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.strokes.append([(event.position().x(), event.position().y())])
            self.active = True

    def mouseMoveEvent(self, event):
        if self.active:
            self.strokes[-1].append((max(0, min(self.width(), event.position().x())), max(0, min(self.height(), event.position().y()))))
            self.update()

    def mouseReleaseEvent(self, event):
        self.active = False

    def normalized(self):
        valid = [s for s in self.strokes if len(s) > 1]
        if not valid:
            return []
        points = [p for stroke in valid for p in stroke]
        x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
        width = max(p[0] for p in points) - x0 or 1
        height = max(p[1] for p in points) - y0 or 1
        return [[((x - x0) / width, (y - y0) / height) for x, y in s] for s in valid]


class SignatureDialog(DisposableDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.setWindowTitle("添加签名")
        self.resize(550,390)
        self.color=QColor("#25364c"); self.image_path=None
        layout=QVBoxLayout(self)
        note=QLabel("选择手写、键入姓名或签名图片。这些方式放置可见签名，不生成证书数字签名。")
        note.setWordWrap(True); layout.addWidget(note)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs)
        hand=QWidget(); hand_layout=QVBoxLayout(hand)
        self.pad=SignaturePad(); hand_layout.addWidget(self.pad)
        clear=QPushButton("清空笔迹"); clear.clicked.connect(self.clear); hand_layout.addWidget(clear)
        self.tabs.addTab(hand,"手写")
        typed=QWidget(); typed_layout=QVBoxLayout(typed)
        self.name=QLineEdit(); self.name.setPlaceholderText("键入姓名"); self.name.setMaxLength(80)
        typed_layout.addWidget(self.name)
        self.signature_font=QComboBox()
        self.signature_font.addItem("手写风格（英文）","library:handwriting")
        self.signature_font.addItem("思源黑体（中英文）","library:sans-sc")
        self.signature_font.addItem("Noto Serif","library:serif")
        typed_layout.addWidget(self.signature_font)
        self.preview=QLabel(); self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.preview.setMinimumHeight(110)
        typed_layout.addWidget(self.preview)
        self.name.textChanged.connect(self.update_preview); self.signature_font.currentIndexChanged.connect(self.update_preview)
        self.tabs.addTab(typed,"键入")
        image=QWidget(); image_layout=QVBoxLayout(image)
        self.image_preview=QLabel("PNG / JPG 签名图片，支持透明背景")
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.image_preview.setMinimumHeight(140)
        image_layout.addWidget(self.image_preview)
        choose=QPushButton("选择签名图片…"); choose.clicked.connect(self.choose_image); image_layout.addWidget(choose)
        self.tabs.addTab(image,"图片")
        row=QHBoxLayout(); row.addStretch()
        self.color_button=QPushButton("签名颜色")
        self.color_button.clicked.connect(self.choose_color); row.addWidget(self.color_button); layout.addLayout(row)
        self.status=QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("放置签名")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.update_preview()

    def update_preview(self,*_):
        from .fonts import qt_family
        qt_family("library:sans-sc",self)
        family=qt_family(self.signature_font.currentData(),self)
        self.preview.setText(self.name.text() or "Your name")
        self.preview.setStyleSheet("font-family:'"+family+"';font-size:32px;color:"+self.color.name()+";")

    def choose_color(self):
        color=QColorDialog.getColor(self.color,self,"签名颜色")
        if color.isValid():
            self.color=color; self.pad.color=color; self.pad.update(); self.update_preview()

    def choose_image(self):
        path,_=QFileDialog.getOpenFileName(self,"选择签名图片","","图片 (*.png *.jpg *.jpeg)")
        if path:
            try:
                from pathlib import Path
                if Path(path).stat().st_size>64*1024*1024: raise ValueError("请选择小于 64 MiB 的图片。")
                reader=QImageReader(path); size=reader.size()
                if size.isEmpty() or size.width()*size.height()>24_000_000: raise ValueError("图片无法读取或像素过大。")
                pixmap=QPixmap(path)
                if pixmap.isNull(): raise ValueError("无法读取此签名图片。")
                self.image_path=path
                self.image_preview.setPixmap(pixmap.scaled(450,150,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
                self.status.clear()
            except Exception as exc: self.status.setText(str(exc))

    def accept(self):
        if self.tabs.currentIndex()==0 and not self.pad.normalized(): self.status.setText("请先书写签名。"); return
        if self.tabs.currentIndex()==1 and not self.name.text().strip(): self.status.setText("请先键入姓名。"); return
        if self.tabs.currentIndex()==2 and not self.image_path: self.status.setText("请先选择签名图片。"); return
        super().accept()

    def done(self,result):
        from .fonts import release_fonts
        release_fonts(self)
        super().done(result)

    def clear(self):
        self.pad.strokes.clear(); self.pad.update()

    def rgb(self): return self.color.redF(),self.color.greenF(),self.color.blueF()


class AISettingsDialog(DisposableDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle("AI 连接设置")
        self.setMinimumWidth(560)
        self.resize(580, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        text = QLabel("支持兼容 Chat Completions 的服务和本地模型。PDF 阅读、编辑和 OCR 始终在本机执行。")
        text.setWordWrap(True)
        layout.addWidget(text)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.endpoint = QLineEdit(config.get("endpoint", "http://127.0.0.1:11434/v1"))
        self.endpoint.setPlaceholderText("https://你的服务地址/v1")
        self.model = QLineEdit(config.get("model", ""))
        self.model.setPlaceholderText("填写服务支持的模型名称")
        self.key = QLineEdit(config.get("key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        for field in (self.endpoint, self.model, self.key):
            field.setMinimumHeight(34)
        self.remember = QCheckBox("记住密钥（系统账户保护的本机加密）")
        self.remember.setChecked(config.get("remember_key", False))
        self.document_mode = QComboBox()
        self.document_mode.addItem("完整文档 · 保留图表，自动适配 DeepSeek", "pdf")
        self.document_mode.addItem("全文文字 · 兼容模式", "text")
        self.document_mode.setCurrentIndex(1 if config.get("document_mode") == "text" else 0)
        self.output_limit = QComboBox()
        for label, value in (("模型默认", 0), ("8,192 token", 8192), ("16,384 token", 16384), ("32,768 token", 32768), ("65,536 token", 65536)):
            self.output_limit.addItem(label, value)
        self.output_limit.setCurrentIndex(max(0, self.output_limit.findData(config.get("max_output_tokens", 0))))
        self.online = QCheckBox("允许 AI 向上述服务发送消息及附加的 PDF、截图和文字")
        self.online.setChecked(config.get("enabled", False))
        form.addRow("API Base URL", self.endpoint)
        form.addRow("模型", self.model)
        form.addRow("API Key", self.key)
        form.addRow("文档背景", self.document_mode)
        form.addRow("输出上限", self.output_limit)
        layout.addLayout(form)
        layout.addWidget(self.remember)
        layout.addWidget(self.online)
        note = QLabel("新对话默认附加全文、当前页截图和选区。点击发送才请求服务，＋ 可调整范围。每轮携带全文背景和最近会话，可能增加服务用量。\n\nDeepSeek：整份 PDF 在本机转换为逐页文字与图片。其他服务使用原生 PDF 输入，不支持时可改用全文文字。截图需要支持图片的模型。\n\n聊天和文档背景加密保存在本机，密钥不写入聊天记录。远程服务的数据保存和使用方式取决于其政策。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"endpoint": self.endpoint.text().strip(), "model": self.model.text().strip(),
                "key": self.key.text().strip(), "remember_key": self.remember.isChecked(),
                "enabled": self.online.isChecked(), "document_mode": self.document_mode.currentData(),
                "max_output_tokens": self.output_limit.currentData()}
