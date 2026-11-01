"""Explicit or opted-in downloads of pinned, licensed font files only.

The request URL is fixed by the bundled catalog; no PDF data, file path, font
name extracted from a document, conversation or AI credential is sent.
"""
import hashlib
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Signal
from .fonts import cache_dir, catalog, catalog_path


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


class FontDownload(QObject):
    completed=Signal(str)
    failed=Signal(str)
    progress=Signal(int)

    def __init__(self,identity,parent=None):
        super().__init__(parent)
        self.identity=identity
        self.cancelled=threading.Event()
        self.response=None
        self.thread=None

    def emit_safe(self,signal,value):
        if not self.cancelled.is_set():
            try: signal.emit(value)
            except RuntimeError: pass

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name="PDF Reader-font")
        self.thread.start()

    def cancel(self):
        self.cancelled.set()
        try: self.response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError,OSError): pass

    def run(self):
        temp=None
        try:
            if self.cancelled.is_set(): return
            if catalog_path(self.identity):
                self.emit_safe(self.completed,"library:"+self.identity); return
            item=catalog()[self.identity]
            url=urlsplit(item["url"])
            if url.scheme!="https" or url.hostname!="raw.githubusercontent.com" or url.query or url.fragment:
                raise ValueError("字体来源不在内置开源清单中。")
            expected=item["bytes"]
            if not 0<expected<=64*1024*1024: raise ValueError("字体文件超出容量限制。")
            root=cache_dir()
            fd,name=tempfile.mkstemp(prefix="font-",suffix=".part",dir=root)
            temp=Path(name)
            digest=hashlib.sha256(); received=0; started=time.monotonic()
            with os.fdopen(fd,"wb") as stream:
                with build_opener(NoRedirect()).open(Request(item["url"],headers={"User-Agent":"PDFReader-font-library"}),timeout=20) as response:
                    self.response=response
                    while not self.cancelled.is_set():
                        if time.monotonic()-started>120: raise TimeoutError("字体下载超时。")
                        data=response.read(64*1024)
                        if not data: break
                        received+=len(data)
                        if received>expected: raise ValueError("字体文件大小与清单不符。")
                        stream.write(data); digest.update(data)
                        self.emit_safe(self.progress,int(received*100/expected))
            if self.cancelled.is_set(): return
            if received!=expected or digest.hexdigest()!=item["sha256"]:
                raise ValueError("字体校验失败，未安装。可以稍后重试或导入本机字体。")
            os.replace(temp,root/item["filename"]); temp=None
            self.emit_safe(self.completed,"library:"+self.identity)
        except Exception as exc:
            self.emit_safe(self.failed,"获取字体失败："+str(exc)[:240])
        finally:
            self.response=None
            if temp:
                try: temp.unlink(missing_ok=True)
                except OSError: pass
