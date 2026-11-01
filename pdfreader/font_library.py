from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QDialogButtonBox,QProgressBar,QCheckBox,QFileDialog)
from .fonts import font_choices,catalog_path,import_font,catalog
from .font_download import FontDownload


class FontLibraryDialog(QDialog):
    def __init__(self,parent=None,requested=""):
        super().__init__(parent)
        self.setWindowTitle("字体库")
        self.resize(480,270)
        self.job=None
        self.closed=False
        self.settings=getattr(parent,"settings",None) or getattr(parent.parent() if parent else None,"settings",None)
        layout=QVBoxLayout(self)
        note=QLabel((f"原字体：{requested}\n" if requested else "")+"常用字体已随包附带。开源清单中的其他字体可以按需获取，也可导入本机字体。")
        note.setWordWrap(True); layout.addWidget(note)
        self.combo=QComboBox(); layout.addWidget(self.combo)
        self.status=QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.progress=QProgressBar(); self.progress.hide(); layout.addWidget(self.progress)
        row=QHBoxLayout()
        self.download=QPushButton("获取字体")
        self.download.clicked.connect(self.acquire)
        row.addWidget(self.download)
        self.import_button=QPushButton("导入本机字体…")
        self.import_button.clicked.connect(self.import_local); row.addWidget(self.import_button)
        row.addStretch(); layout.addLayout(row)
        self.auto=QCheckBox("编辑时自动获取缺失的开源字体")
        self.auto.setChecked(bool(self.settings and self.settings.values.get("auto_font_download",False)))
        self.auto.setEnabled(self.settings is not None)
        self.auto.toggled.connect(self.save_preference); layout.addWidget(self.auto)
        source=QLabel("仅从固定版本的 Noto 官方开源仓库获取字体；不发送 PDF、文字或密钥。")
        source.setObjectName("muted"); source.setWordWrap(True); layout.addWidget(source)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("使用所选字体")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)
        self.combo.currentIndexChanged.connect(self.update_state)
        self.refresh()

    def refresh(self,selected=None):
        selected=selected or self.combo.currentData()
        self.combo.blockSignals(True); self.combo.clear()
        for label,identity in font_choices(): self.combo.addItem(label,identity)
        self.combo.setCurrentIndex(max(0,self.combo.findData(selected)))
        self.combo.blockSignals(False); self.update_state()

    def update_state(self):
        identity=self.combo.currentData() or ""
        available=identity.startswith("library:import:") or bool(identity and catalog_path(identity[8:]))
        self.download.setEnabled(not available and self.job is None)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(available and self.job is None)
        if self.job is None:
            self.status.setText("已在本机可用，可离线使用。" if available else f"尚未获取 · {catalog()[identity[8:]]['bytes']/1024/1024:.1f} MiB")

    def acquire(self):
        if self.closed or self.job: return
        identity=self.combo.currentData()[8:]
        self.job=FontDownload(identity,self)
        self.job.completed.connect(self.completed); self.job.failed.connect(self.failed)
        self.job.progress.connect(self.progress.setValue)
        self.combo.setEnabled(False); self.download.setEnabled(False); self.import_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.progress.setValue(0); self.progress.show(); self.status.setText("正在获取开源字体…")
        self.job.start()

    @Slot(str)
    def completed(self,identity):
        self.job=None; self.progress.hide(); self.combo.setEnabled(True); self.import_button.setEnabled(True)
        self.refresh(identity)

    @Slot(str)
    def failed(self,message):
        self.job=None; self.progress.hide(); self.combo.setEnabled(True); self.import_button.setEnabled(True)
        self.update_state(); self.status.setText(message)

    def import_local(self):
        path,_=QFileDialog.getOpenFileName(self,"导入字体","","字体 (*.ttf *.otf *.ttc)")
        if path:
            try: self.refresh(import_font(path))
            except Exception as exc: self.status.setText(str(exc))

    def save_preference(self,value):
        if self.settings:
            self.settings.values["auto_font_download"]=value
            try: self.settings.save()
            except Exception as exc: self.status.setText("设置保存失败："+str(exc))

    def done(self,result):
        self.closed=True
        if self.job: self.job.cancel()
        super().done(result)
        self.deleteLater()

    def selected_font(self): return self.combo.currentData()
