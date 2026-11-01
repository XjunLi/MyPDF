"""Bounded-resource and interaction stress check; no user configuration or HTTP."""
import gc
import json
import os
from pathlib import Path
import statistics
import sys
import time
import weakref


def resident_bytes():
    if sys.platform=="win32":
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_=[("cb",wintypes.DWORD),("faults",wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ("peak","working","minpool","pool","minnonpool","nonpool","pagefile","peakpagefile")]
        data=Memory(); data.cb=ctypes.sizeof(data)
        kernel=ctypes.WinDLL("kernel32",use_last_error=True); kernel.GetCurrentProcess.restype=wintypes.HANDLE
        api=ctypes.WinDLL("psapi",use_last_error=True)
        api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
        if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(data),data.cb): raise ctypes.WinError()
        return data.working
    import subprocess
    return int(subprocess.check_output(["/bin/ps","-o","rss=","-p",str(os.getpid())],text=True).strip())*1024


def performance(directory,filename=None):
    root=Path(directory).resolve(); root.mkdir(parents=True,exist_ok=True)
    os.environ["PDFREADER_DATA_DIR"]=str(root/"settings")
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtCore import QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication,QGraphicsProxyWidget
    from .app import MainWindow
    from .style import STYLE,prepare_fonts
    from .paths import resource_root
    from .dialogs import SignatureDialog
    from . import fonts
    app=QApplication.instance() or QApplication([])
    prepare_fonts(app); app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    window=MainWindow(); window.auto_ocr.setChecked(False); window.show()
    window.open_file(filename or resource_root()/"samples/welcome.pdf")
    def settle():
        app.processEvents(); QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete); app.processEvents()
    def cycles(count):
        refs=[]
        for index in range(count):
            view=window.view
            view.begin_inline_text(0,(80,190))
            editor=view.inline_editor
            editor.input.setPlainText("A local draft / 中文 / ∇")
            refs.append(weakref.ref(editor))
            view.finish_inline_text(cancel=True)
            del editor
            if index%5==0:
                dialog=SignatureDialog(window); dialog.reject(); refs.append(weakref.ref(dialog)); del dialog
            settle()
        gc.collect(); settle()
        assert not any(ref() is not None for ref in refs),"Closed editor/dialog objects were retained"
        assert not fonts._qt_fonts and not fonts._font_owners,"Unused Qt font registrations were retained"
        assert not any(isinstance(item,QGraphicsProxyWidget) for item in window.view.scene_obj.items())
    result={"ok":False,"network":"disabled: no AI or font request","pages":window.document.count}
    try:
        settle(); cycles(8)
        baseline=resident_bytes(); cycles(40); after=resident_bytes()
        growth=max(0,after-baseline)
        assert growth<64*1024*1024,f"Post-warmup editor memory growth: {growth}"
        timings=[]
        max_cache=0
        for index in range(200):
            started=time.perf_counter()
            window.view.set_zoom((.75,1.,1.25,2.,4.)[index%5])
            bar=window.view.verticalScrollBar()
            bar.setValue(int(bar.maximum()*((index%17)/16)))
            window.view.viewport().repaint(); settle()
            timings.append((time.perf_counter()-started)*1000)
            if index==99: reading_memory_mid=resident_bytes()
            max_cache=max(max_cache,window.view.renderer.bytes)
            assert window.view.renderer.bytes<=window.view.renderer.budget
        reading_growth=max(0,resident_bytes()-reading_memory_mid)
        assert reading_growth<64*1024*1024,f"Reading memory kept growing: {reading_growth}"
        assert max(timings)<2000,"A light-fixture reading operation blocked for over two seconds"
        assert sum(value[1] for value in fonts._face_cache.values())<=fonts.FONT_CACHE_BYTES
        assert sum(len(value) for value in fonts._subset_cache.values())<=8*1024*1024
        result.update(ok=True,editor_cycles=48,signature_dialog_cycles=10,
                      memory_before_bytes=baseline,memory_after_bytes=after,memory_growth_bytes=growth,
                      unclosed_editors=0,unused_qt_fonts=len(fonts._qt_fonts),reading_operations=200,reading_second_half_memory_growth_bytes=reading_growth,
                      reading_median_ms=round(statistics.median(timings),2),
                      reading_p95_ms=round(sorted(timings)[int(len(timings)*.95)-1],2),reading_max_ms=round(max(timings),2),
                      raster_cache_peak_bytes=max_cache,raster_cache_budget_bytes=window.view.renderer.budget,
                      font_cache_input_bytes=sum(value[1] for value in fonts._face_cache.values()))
    except Exception:
        import traceback
        result["error"]=traceback.format_exc()
    finally:
        for session in window.sessions: session.document.saved_state=session.document.state
        window.view.finish_inline_text(cancel=True); window.close(); settle()
        (root/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if result["ok"] else 1
