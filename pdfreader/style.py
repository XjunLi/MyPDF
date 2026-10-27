def prepare_fonts(app):
    from pathlib import Path
    import os
    from PySide6.QtGui import QFontDatabase, QFont
    directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        path = directory / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 10))


STYLE = """
QWidget { font-family:'Microsoft YaHei UI','Segoe UI'; font-size:12px; color:#343b46; }
QMainWindow, QDialog { background:#fafbfc; }
QToolBar { background:#fff; border:0; spacing:3px; padding:8px 12px; }
QToolBar#topTools { border-bottom:1px solid #e5e7eb; }
QToolBar#searchBar { border-bottom:1px solid #e5e7eb; padding:6px 18px; }
QToolBar#footer { background:#fff; border-top:1px solid #e5e7eb; padding:5px 12px; }
QToolBar::separator { width:1px; background:#e5e7eb; margin:7px 7px; }
QToolButton { background:transparent; border:1px solid transparent; border-radius:6px; padding:6px; }
QToolButton:hover { background:#f0f2f5; }
QToolButton:pressed { background:#e5eaf2; }
QToolButton:checked { background:#eaf0fc; border-color:#dbe6fb; }
QToolButton:disabled { color:#b8bdc5; }
QToolButton::menu-indicator { image:none; }
QToolButton#splitTool { padding:6px 20px 6px 6px; }
QToolButton::menu-button { border:0; width:14px; }
QToolButton::menu-arrow { image:url(ASSET_ROOT/chevron-down.svg); width:9px; height:9px; }
QToolButton::menu-button:hover { background:#e8edf5; border-radius:3px; }
QPushButton { background:#fff; border:1px solid #dce1e8; border-radius:6px; padding:7px 14px; }
QPushButton:hover { background:#f1f4f8; border-color:#bdc8d8; }
QPushButton:disabled { color:#b2b8c2; border-color:#e8eaee; }
QPushButton#primary { background:#326ad5; border:1px solid #326ad5; color:#fff; }
QPushButton#primary:hover { background:#285bbc; }
QPushButton#textButton { border:0; color:#808895; background:transparent; padding:6px 10px; }
QPushButton#textButton:hover { color:#326ad5; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextBrowser { background:#fff; border:1px solid #e1e5eb; border-radius:6px; padding:7px; selection-background-color:#c6dafb; selection-color:#172948; }
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QComboBox:focus { border-color:#9bb8e8; }
QSpinBox { padding:5px 2px; }
QComboBox { min-height:18px; padding:5px 20px 5px 8px; }
QComboBox::drop-down { border:0; width:18px; }
QComboBox::down-arrow { image:url(ASSET_ROOT/chevron-down.svg); width:12px; height:12px; }
QComboBox QAbstractItemView { background:#fff; border:1px solid #e1e5eb; selection-background-color:#eaf0fc; padding:4px; outline:0; }
QMenu { background:#fff; border:1px solid #e1e5eb; border-radius:5px; padding:6px; }
QMenu::item { padding:8px 30px 8px 20px; border-radius:4px; }
QMenu::item:selected { background:#eef3fc; color:#285bbc; }
QMenu::separator { height:1px; background:#e8ebf0; margin:5px 10px; }
QMenu::item:disabled { color:#b2b8c2; }
QToolTip { background:#252c36; color:#fff; border:0; border-radius:4px; padding:6px 9px; }
QTabWidget::pane { border:0; background:#fafbfc; }
QTabBar { background:#fafbfc; }
QTabBar::tab { background:#fafbfc; min-width:30px; padding:10px 15px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { background:#fff; border-bottom:2px solid #6890d8; }
QTabBar::tab:hover { background:#f1f4f8; }
QTabBar#documentTabs { background:#fff; border:0; }
QTabBar#documentTabs::tab { background:transparent; color:#828b98; min-width:70px; padding:6px 13px; border-bottom:0; border-top:2px solid transparent; border-right:1px solid #ebedf1; }
QTabBar#documentTabs::tab:selected { background:#f1f5fc; color:#343b46; border-top-color:#6890d8; }
QTabBar#documentTabs::tab:hover { background:#eef2f8; }
QTabBar#documentTabs QToolButton { background:#fff; border:0; border-radius:0; padding:4px; }
QTabBar#documentTabs QToolButton#documentClose { background:transparent; border:0; border-radius:4px; padding:3px; }
QTabBar#documentTabs QToolButton#documentClose:hover { background:#e3e9f2; }
QListWidget, QTreeWidget { background:#fafbfc; border:0; outline:0; }
QListWidget::item { padding:9px 8px; margin:3px 2px; border-radius:5px; }
QListWidget::item:selected { background:#e9effa; color:#326ad5; }
QListWidget::item:hover { background:#f0f3f8; }
QListWidget#thumbnails::item { margin:3px 5px; padding:7px 5px; border:1px solid transparent; }
QListWidget#thumbnails::item:selected { background:#e9effa; border-color:#d0ddf3; }
QTreeWidget::item { padding:8px 4px; }
QTreeWidget::item:selected { background:#e9effa; color:#326ad5; }
QSplitter::handle { background:#e4e7ec; }
QSplitter::handle:hover { background:#b2c5e6; }
QScrollBar:vertical { width:8px; background:transparent; border:0; margin:3px 0; }
QScrollBar::handle:vertical { background:#c6cbd3; border-radius:4px; min-height:36px; }
QScrollBar::handle:vertical:hover { background:#aab2bf; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { height:8px; background:transparent; border:0; margin:0 3px; }
QScrollBar::handle:horizontal { background:#c6cbd3; border-radius:4px; min-width:36px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
QLabel#muted { color:#8a929e; font-size:11px; }
QLabel#panelTitle { font-size:13px; font-weight:600; color:#444d5b; }
QLabel#readingNotice { background:rgba(248,250,254,245); color:#4b5c74; border:1px solid #d9e2ef; border-radius:7px; padding:8px 12px; }
QWidget#welcome { background:#fafbfc; }
QLabel#welcomeTitle { color:#3d4654; font-family:'Segoe UI'; font-size:28px; font-weight:600; }
QTextBrowser#chat { background:#fafbfc; border:0; padding:4px; }
QFrame#chatComposer { background:#fff; border:1px solid #dfe5ee; border-radius:12px; }
QPlainTextEdit#chatInput { background:transparent; border:0; padding:7px 5px; font-size:13px; }
QToolButton#chatSend { background:#e8eef9; border:0; border-radius:9px; }
QToolButton#chatSend:hover { background:#dbe6f9; }
QToolButton#attachmentChip { background:#f1f4f9; color:#657993; border:0; padding:5px 7px; font-size:11px; border-radius:6px; }
QToolButton#attachmentChip:hover { background:#e8eef8; }
QProgressBar { border:0; border-radius:3px; background:#edf1f6; color:#667c9d; font-size:10px; text-align:center; }
QProgressBar::chunk { background:#ccddf7; border-radius:3px; }
"""
from .paths import resource_root
STYLE = STYLE.replace("ASSET_ROOT", (resource_root() / "assets").as_posix())
