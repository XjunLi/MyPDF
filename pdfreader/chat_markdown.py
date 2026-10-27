"""Native, offline Markdown rendering. No WebEngine, scripts or remote images."""
import html
import re
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QTextDocument
from PySide6.QtWidgets import QTextBrowser


class PrivateDocument(QTextDocument):
    def loadResource(self, resource_type, name):
        # Markdown must not read files, contact URLs, or load tracking pixels.
        return QByteArray()


def markdown_html(text):
    document = PrivateDocument()
    document.setDefaultFont(QFont("Microsoft YaHei UI", 10))
    document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                         QTextDocument.MarkdownFeature.MarkdownNoHTML)
    body = re.search(r"<body[^>]*>(.*)</body>", document.toHtml(), re.S)
    rendered = body.group(1) if body else html.escape(text)
    rendered = rendered.replace("font-family:'monospace'", "font-family:'Consolas'")
    rendered = rendered.replace("margin-top:6px", "margin-top:8px").replace("margin-bottom:6px", "margin-bottom:10px")
    rendered = rendered.replace('border-collapse:collapse;', 'border-collapse:collapse; border:1px solid #dfe5ee;')
    # Suppress embedded images altogether, including file:// and data: URLs.
    return re.sub(r"<img\b[^>]*>", '<span style="color:#8a929e">[图片已省略]</span>', rendered)


class ChatView(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setDocument(PrivateDocument(self))
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setObjectName("chat")
        self.document().setDocumentMargin(6)
