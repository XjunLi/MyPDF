from PySide6.QtCore import Qt, QPointF
import re
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QDialogButtonBox, QPlainTextEdit, QSpinBox, QPushButton, QFormLayout,
    QLineEdit, QComboBox, QCheckBox, QDoubleSpinBox, QColorDialog)


class TextDialog(QDialog):
    def __init__(self, parent, text="", editing=False, region=None):
        super().__init__(parent)
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


class CommentDialog(QDialog):
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
        self.active = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#dfe5ec"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(25, self.height() - 50, self.width() - 25, self.height() - 50)
        painter.setPen(QPen(QColor("#203651"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
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


class SignatureDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("手写签名")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("在下方签名，笔迹会放入你刚绘制的选框。此功能不生成证书数字签名。"))
        self.pad = SignaturePad()
        layout.addWidget(self.pad)
        clear = QPushButton("清空笔迹")
        clear.clicked.connect(self.clear)
        layout.addWidget(clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def clear(self):
        self.pad.strokes.clear()
        self.pad.update()


class AISettingsDialog(QDialog):
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
        self.remember = QCheckBox("记住密钥（Windows 当前用户加密）")
        self.remember.setChecked(config.get("remember_key", False))
        self.document_mode = QComboBox()
        self.document_mode.addItem("PDF 文件 · 保留图表（服务需支持 PDF 输入）", "pdf")
        self.document_mode.addItem("全文文字 · 兼容模式", "text")
        self.document_mode.setCurrentIndex(1 if config.get("document_mode") == "text" else 0)
        self.online = QCheckBox("允许 AI 向上述服务发送消息及附加的 PDF、截图和文字")
        self.online.setChecked(config.get("enabled", False))
        form.addRow("API Base URL", self.endpoint)
        form.addRow("模型", self.model)
        form.addRow("API Key", self.key)
        form.addRow("文档背景", self.document_mode)
        layout.addLayout(form)
        layout.addWidget(self.remember)
        layout.addWidget(self.online)
        note = QLabel("新对话默认附加全文、当前页截图和选区。点击发送才请求服务，＋ 可调整范围。每轮携带全文背景和最近会话，可能增加服务用量。\n\nPDF 不受支持时可改用全文文字；其他页面的图表不会包含在文字中。截图需要支持图片的模型。\n\n聊天和文档背景加密保存在本机，密钥不写入聊天记录。远程服务的数据保存和使用方式取决于其政策。")
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
                "enabled": self.online.isChecked(), "document_mode": self.document_mode.currentData()}
