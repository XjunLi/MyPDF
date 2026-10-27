from PySide6.QtCore import Qt, QSize, QTimer, QEvent
from PySide6.QtGui import QAction, QActionGroup, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolBar, QToolButton, QMenu, QSizePolicy, QSplitter, QTabWidget, QStackedWidget,
    QListWidget, QTreeWidget, QLineEdit, QSpinBox, QComboBox, QProgressBar,
    QAbstractSpinBox, QTabBar)
from .icons import icon, icon_button, split_tool_button
from .viewer import PdfView
from .panels import AIPanel
from .terminal import TerminalPanel
from .paths import resource_root


class ReadingNotice(QLabel):
    """Brief operation feedback above the footer, leaving tabs accessible."""
    def __init__(self, anchor):
        super().__init__(anchor)
        self.full_text = ""
        self.setObjectName("readingNotice")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(4500)
        self.timer.timeout.connect(self.hide)
        anchor.installEventFilter(self)
        self.hide()

    def setText(self, text):
        self.timer.stop()
        self.full_text = text
        if not text:
            super().setText("")
            self.hide()
            return
        self.place_notice()
        self.show()
        self.raise_()
        self.timer.start()

    def place_notice(self):
        anchor = self.parentWidget()
        width = min(680, max(1, anchor.width() - 32), self.fontMetrics().horizontalAdvance(self.full_text) + 28)
        self.setFixedSize(width, self.fontMetrics().height() + 20)
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, max(0, width - 26)))
        self.move(max(0, (anchor.width() - width) // 2), max(0, anchor.height() - self.height() - 14))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and not self.isHidden():
            self.place_notice()
        return super().eventFilter(watched, event)


class DocumentStrip(QWidget):
    """Flexible footer area: extra documents must not push navigation out."""
    def __init__(self, tabs):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(tabs)

    def sizeHint(self):
        return QSize(320, 36)

    def minimumSizeHint(self):
        return QSize(120, 36)


class DocumentTabBar(QTabBar):
    def minimumSizeHint(self):
        return QSize(120, 36)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        size.setWidth(max(120, min(250, size.width())))
        return size


class ReaderLayout:
    def toolbar_action(self, bar, name, label, callback, shortcut=None, **kwargs):
        action = self.action(label, callback, shortcut, **kwargs)
        action.setIcon(icon(name))
        action.setToolTip(label + (f"  {shortcut}" if shortcut else ""))
        if bar is not None:
            bar.addAction(action)
        return action

    def _build_ui(self):
        self.setAcceptDrops(True)
        self.top = QToolBar("工具", self)
        self.top.setObjectName("topTools")
        self.top.setMovable(False)
        self.top.setIconSize(QSize(20, 20))
        self.top.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(self.top)
        self.toolbar_left_space = QWidget()
        self.toolbar_left_space.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top.addWidget(self.toolbar_left_space)
        self.root_menu = QMenu(self)
        menu_button = icon_button("menu", "菜单")
        menu_button.setIconSize(QSize(20, 20))
        menu_button.setFixedSize(34, 34)
        menu_button.setMenu(self.root_menu)
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.top.addWidget(menu_button)
        self.open_action = self.toolbar_action(self.top, "open", "打开 PDF", self.open_dialog, "Ctrl+O")
        self.save_action = self.toolbar_action(self.top, "save", "保存", self.save, "Ctrl+S", document=True)
        self.top.addSeparator()
        self.undo_action = self.toolbar_action(self.top, "undo", "撤销", self.undo, "Ctrl+Z", edit=True)
        self.redo_action = self.toolbar_action(self.top, "redo", "重做", self.redo, "Ctrl+Shift+Z", edit=True)
        self.top.addSeparator()
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.mode_actions = {}
        modes = [
            ("select", "选择文字", "拖动划词 · 双击选词 · Ctrl+C 复制"),
            ("hand", "抓手", "拖动页面"),
            ("highlight", "高亮", "先选择文字，再点击高亮"),
            ("underline", "下划线", "为所选文字添加下划线"),
            ("strike", "删除线", "为所选文字添加删除线"),
            ("comment", "注释", "有选区时注释文字；没有选区时单击页面放置便笺"),
            ("text", "添加文字", "拖出文字框"),
            ("edit", "编辑文字", "点击文字行；拖框可编辑多行"),
            ("image", "插入图片", "拖出图片放置框"),
            ("ink", "画笔", "在页面上绘制"),
            ("signature", "手写签名", "拖出签名位置，再书写签名"),
        ]
        for mode, label, hint in modes:
            selectable = mode not in ("highlight", "underline", "strike")
            a = self.toolbar_action(None, mode, label, lambda checked=False, m=mode, h=hint: self.set_mode(m, h), document=True, checkable=selectable)
            a.setToolTip(label + " · " + hint)
            if selectable:
                self.tool_group.addAction(a)
            self.mode_actions[mode] = a
            if mode in ("highlight", "text"):
                button = split_tool_button(a)
                self.top.addWidget(button)
                if mode == "highlight":
                    self.markup_button = button
                else:
                    self.insert_button = button
            elif mode not in ("hand", "underline", "strike", "image"):
                self.top.addAction(a)
        for button, group in [(self.markup_button, ("highlight", "underline", "strike")), (self.insert_button, ("text", "image"))]:
            menu = QMenu(self)
            for mode in group:
                menu.addAction(self.mode_actions[mode])
            button.setMenu(menu)
        self.mode_actions["select"].setChecked(True)
        self.top.addSeparator()
        self.ocr_action = self.toolbar_action(None, "ocr", "识别扫描页", lambda: self.start_ocr(), edit=True)
        ocr_menu = self.ocr_menu = QMenu("文字识别", self)
        ocr_menu.addAction(self.ocr_action)
        self.auto_ocr = QAction("自动识别扫描页", self)
        self.auto_ocr.setCheckable(True)
        self.auto_ocr.setChecked(self.settings.values.get("auto_ocr", True))
        self.auto_ocr.toggled.connect(self.toggle_auto_ocr)
        ocr_menu.addAction(self.auto_ocr)
        ocr_menu.addSeparator()
        # The combo is the single source of truth for existing worker settings.
        self.language = QComboBox(self)
        self.language.hide()
        language_group = QActionGroup(self)
        for label, code in [("中文 + English", "eng+chi_sim"), ("English", "eng"), ("中文", "chi_sim")]:
            self.language.addItem(label, code)
            a = ocr_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(code == self.settings.values.get("language", "eng+chi_sim"))
            language_group.addAction(a)
            a.triggered.connect(lambda checked=False, c=code: self.language.setCurrentIndex(self.language.findData(c)))
        self.language.setCurrentIndex(max(0, self.language.findData(self.settings.values.get("language"))))
        self.search_action = self.toolbar_action(self.top, "search", "查找", self.show_search, "Ctrl+F", document=True)
        self.top.addSeparator()
        self.left_action = self.toolbar_action(self.top, "sidebar", "页面与目录", self.toggle_left, "F8", checkable=True)
        self.panel_actions = {}
        for key, label in [("ai", "AI 助手"), ("comment", "批注列表"), ("terminal", "终端")]:
            self.panel_actions[key] = self.toolbar_action(self.top, key, label, lambda checked=False, k=key: self.toggle_panel(k), checkable=True)
        self.toolbar_right_space = QWidget()
        self.toolbar_right_space.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top.addWidget(self.toolbar_right_space)

        self.search_bar = QToolBar("查找", self)
        self.search_bar.setObjectName("searchBar")
        self.search_bar.setMovable(False)
        self.search_bar.setIconSize(QSize(18, 18))
        self.search_bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("查找文档文字")
        self.search_input.setFixedWidth(280)
        self.search_input.returnPressed.connect(self.search)
        self.search_input.textChanged.connect(self.schedule_search)
        self.search_bar.addWidget(self.search_input)
        self.search_count = QLabel("")
        self.search_count.setMinimumWidth(100)
        self.search_bar.addWidget(self.search_count)
        self.toolbar_action(self.search_bar, "up", "上一个结果", lambda: self.next_search(-1), "Shift+F3", document=True)
        self.toolbar_action(self.search_bar, "down", "下一个结果", lambda: self.next_search(1), "F3", document=True)
        self.toolbar_action(self.search_bar, "close", "关闭查找", self.hide_search)
        self.search_bar.hide()
        self.search_escape = QShortcut(QKeySequence("Escape"), self.search_bar)
        self.search_escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.search_escape.activated.connect(self.hide_search)

        self.splitter = QSplitter()
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.document_tabs = DocumentTabBar()
        self.document_tabs.setObjectName("documentTabs")
        self.document_tabs.setDocumentMode(True)
        self.document_tabs.setDrawBase(False)
        self.document_tabs.setExpanding(False)
        self.document_tabs.setMovable(True)
        self.document_tabs.setTabsClosable(True)
        self.document_tabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.document_tabs.setUsesScrollButtons(True)
        self.document_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document_tabs.setFixedHeight(36)
        self.document_tabs.setAccessibleName("PDF 文档标签")
        self.document_tabs.currentChanged.connect(self.tab_changed)
        self.document_tabs.tabCloseRequested.connect(self.close_document)
        self.document_tabs.hide()
        central_layout.addWidget(self.search_bar)
        central_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.left = QTabWidget()
        self.left.setMinimumWidth(160)
        self.left.setMaximumWidth(300)
        self.left.setIconSize(QSize(18, 18))
        self.thumbnails = QListWidget()
        self.thumbnails.setObjectName("thumbnails")
        self.thumbnails.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbnails.setFlow(QListWidget.Flow.TopToBottom)
        self.thumbnails.setWrapping(False)
        self.thumbnails.setMovement(QListWidget.Movement.Static)
        self.thumbnails.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbnails.setIconSize(QSize(108, 144))
        self.thumbnails.setSpacing(3)
        self.thumbnails.itemClicked.connect(lambda item: self.view.goto(item.data(Qt.ItemDataRole.UserRole)))
        self.thumbnails.verticalScrollBar().valueChanged.connect(self.queue_visible_thumbs)
        self.bookmarks = QTreeWidget()
        self.bookmarks.setHeaderHidden(True)
        self.bookmarks.itemClicked.connect(lambda item, column: self.view.goto(item.data(0, Qt.ItemDataRole.UserRole)))
        self.left.addTab(self.thumbnails, icon("pages"), "")
        self.left.addTab(self.bookmarks, icon("outline"), "")
        self.left.setTabToolTip(0, "页面")
        self.left.setTabToolTip(1, "目录")
        self.splitter.addWidget(self.left)
        self.stack = QStackedWidget()
        self.welcome = self.make_welcome()
        self.stack.addWidget(self.welcome)
        self.view = PdfView(self.document)
        self.empty_document, self.empty_view = self.document, self.view
        self.stack.addWidget(self.view)
        self.connect_view(self.view)
        self.splitter.addWidget(self.stack)
        self.right = QTabWidget()
        self.right.setMinimumWidth(300)
        self.right.setMaximumWidth(600)
        self.right.setIconSize(QSize(18, 18))
        self.ai = AIPanel(self.settings, self.ai_context, image_provider=self.ai_page_image,
                          document_provider=self.ai_document_context, document_info=self.ai_document_info)
        self.ai.historyOpenRequested.connect(self.open_ai_history)
        self.right.addTab(self.ai, icon("ai"), "AI")
        self.comments = QWidget()
        cl = QVBoxLayout(self.comments)
        cl.setContentsMargins(12, 12, 12, 12)
        row = QHBoxLayout()
        heading = QLabel("批注")
        heading.setObjectName("panelTitle")
        row.addWidget(heading, 1)
        row.addWidget(icon_button("edit", "编辑批注", self.edit_comment))
        row.addWidget(icon_button("trash", "删除批注", self.delete_comment))
        cl.addLayout(row)
        self.comment_list = QListWidget()
        self.comment_list.setWordWrap(True)
        self.comment_list.itemClicked.connect(self.select_comment)
        self.comment_list.itemDoubleClicked.connect(self.edit_comment)
        cl.addWidget(self.comment_list, 1)
        self.right.addTab(self.comments, icon("comment"), "")
        self.right.setTabToolTip(1, "批注")
        self.terminal = TerminalPanel()
        self.right.addTab(self.terminal, icon("terminal"), "")
        self.right.setTabToolTip(2, "终端")
        close_panel = icon_button("close", "收起侧栏", lambda: self.toggle_panel(None))
        self.right.setCornerWidget(close_panel)
        self.right.currentChanged.connect(self.sync_panel_actions)
        self.splitter.addWidget(self.right)
        self.splitter.setSizes([176, 950, 350])
        self.splitter.setStretchFactor(1, 1)
        self.left.hide()
        self.right.hide()

        self.footer = QToolBar("阅读", self)
        self.footer.setObjectName("footer")
        self.footer.setMovable(False)
        self.footer.setIconSize(QSize(18, 18))
        self.footer.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self.footer)
        self.status_label = ReadingNotice(self.stack)
        self.document_strip = DocumentStrip(self.document_tabs)
        self.footer.addWidget(self.document_strip)
        self.modified_label = QLabel("")
        self.modified_label.setObjectName("muted")
        self.footer.addWidget(self.modified_label)
        self.ocr_progress = QProgressBar()
        self.ocr_progress.setFixedSize(96, 14)
        self.ocr_progress_action = self.footer.addWidget(self.ocr_progress)
        self.ocr_progress_action.setVisible(False)
        self.ocr_stop = icon_button("stop", "取消 OCR", self.cancel_ocr)
        self.ocr_stop_action = self.footer.addWidget(self.ocr_stop)
        self.ocr_stop_action.setVisible(False)
        self.footer.addSeparator()
        self.previous_action = self.toolbar_action(self.footer, "prev", "上一页", lambda: self.navigate_page(-1), "Alt+Left", document=True)
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setFixedWidth(44)
        self.page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.page_spin.setToolTip("页码 · 输入后按 Enter 跳转")
        self.page_spin.setAccessibleName("页码")
        self.page_spin.editingFinished.connect(self.commit_page)
        self.footer.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ")
        self.page_total.setObjectName("muted")
        self.footer.addWidget(self.page_total)
        self.next_action = self.toolbar_action(self.footer, "next", "下一页", lambda: self.navigate_page(1), "Alt+Right", document=True)
        self.footer.addSeparator()
        self.toolbar_action(self.footer, "minus", "缩小", lambda: self.view.set_zoom(self.view.zoom / 1.15), "Ctrl+-", document=True)
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "300%", "500%"])
        self.zoom_combo.setFixedWidth(86)
        self.zoom_combo.lineEdit().setStyleSheet("padding:0; border:0; background:transparent;")
        self.zoom_combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.setAccessibleName("缩放比例")
        self.zoom_combo.activated.connect(self.change_zoom)
        self.zoom_combo.lineEdit().returnPressed.connect(self.change_zoom)
        self.footer.addWidget(self.zoom_combo)
        self.toolbar_action(self.footer, "plus", "放大", lambda: self.view.set_zoom(self.view.zoom * 1.15), "Ctrl+=", document=True)
        self.toolbar_action(self.footer, "fit_width", "适合宽度", self.fit_width, "Ctrl+0", document=True)
        self.toolbar_action(self.footer, "fit_page", "适合整页", lambda: self.view.fit_page(), "Ctrl+9", document=True)
        self.footer.addSeparator()
        self.toolbar_action(self.footer, "fullscreen", "全屏", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(), "F11")

    def make_welcome(self):
        widget = QWidget()
        widget.setObjectName("welcome")
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(14)
        title = QLabel("开始阅读")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        desc = QLabel("打开文件，或将多份 PDF 拖到这里")
        desc.setObjectName("muted")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)
        layout.addSpacing(12)
        open_button = QPushButton("打开 PDF")
        open_button.setObjectName("primary")
        open_button.setFixedSize(148, 40)
        open_button.clicked.connect(self.open_dialog)
        layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for label, callback in [("新建文档", self.new_document), ("试读示例", lambda: self.open_file(str(resource_root() / "samples" / "welcome.pdf")))]:
            button = QPushButton(label)
            button.setObjectName("textButton")
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        return widget
