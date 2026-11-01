import hashlib
import json
import time
from pathlib import Path
import pytest
import pymupdf as fitz
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QInputMethodEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pdfreader.app import MainWindow
from pdfreader.fonts import catalog,catalog_path,library_face
from pdfreader.style import STYLE
from test_selection import visible_point


@pytest.fixture
def reader(qapp,pdf_path,tmp_path,monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR",str(tmp_path/"settings"))
    previous=qapp.styleSheet(); qapp.setStyleSheet(STYLE)
    window=MainWindow(); window.auto_ocr.setChecked(False); window.show(); window.open_file(pdf_path)
    window.view.set_zoom(1); window.view.goto(0); qapp.processEvents()
    yield window
    window.view.finish_inline_text(cancel=True)
    for session in window.sessions: session.document.saved_state=session.document.state
    window.close(); qapp.processEvents(); qapp.setStyleSheet(previous)


def place(window,point=(80,190)):
    window.mode_actions["text"].trigger()
    QTest.mouseClick(window.view.viewport(),Qt.MouseButton.LeftButton,pos=visible_point(window.view,0,point))
    assert window.view.inline_editor is not None
    return window.view.inline_editor


@pytest.mark.parametrize("rotation",[0,90,180,270])
def test_inserted_unicode_is_horizontal_on_screen_and_subsetted(document,tmp_path,rotation):
    document.doc[0].set_rotation(rotation)
    before=document.doc[0].get_pixmap(clip=fitz.Rect(0,0,400,160)).samples
    document.insert_text_box(0,(35,200),"Hello 你好 ∇ x²",14)
    assert document.doc[0].search_for("你好")
    span=next(span for b in document.doc[0].get_text("dict")["blocks"] for line in b.get("lines",[]) for span in line["spans"] if "Hello" in span["text"])
    box=fitz.Rect(span["bbox"])*document.doc[0].rotation_matrix
    assert box.x0==pytest.approx(38,abs=.1)
    assert 199<box.y0<208
    path=tmp_path/"inserted.pdf"; document.save(path)
    assert path.stat().st_size<250000
    with fitz.open(path) as reopened: assert "∇" in reopened[0].get_text() and "你好" in reopened[0].get_text()
    document.undo(); assert not document.doc[0].search_for("你好")
    assert document.doc[0].get_pixmap(clip=fitz.Rect(0,0,400,160)).samples==before
    document.redo(); assert document.doc[0].search_for("你好")


def test_single_click_typing_grows_ime_zoom_save_and_one_undo(reader,tmp_path,qapp):
    editor=place(reader)
    initial=editor.body_width,editor.body_height
    QTest.keyClicks(editor.input,"A simple note ")
    event=QInputMethodEvent(); event.setCommitString("中文 ∇")
    QApplication.sendEvent(editor.input,event)
    assert "中文 ∇" in editor.input.toPlainText()
    QTest.keyClicks(editor.input," and a longer note "*15)
    assert editor.body_width>initial[0] and editor.body_height>initial[1]
    assert not reader.document.dirty
    reader.view.set_zoom(1.25); qapp.processEvents()
    assert reader.view.inline_editor is editor and "中文" in editor.input.toPlainText()
    assert reader.view.finish_inline_text()
    assert reader.document.dirty and reader.view.mode=="select"
    assert "中文" in reader.document.page_text(0)
    assert len(reader.document.undo_stack)==1
    path=tmp_path/"note.pdf"; reader.document.save(path)
    with fitz.open(path) as pdf: assert "中文" in pdf[0].get_text()
    reader.undo(); assert "中文" not in reader.document.page_text(0)


def test_cancel_and_overflow_retain_or_discard_draft_without_pdf_changes(reader):
    editor=place(reader,(90,550))
    editor.input.setPlainText("too many lines "*100)
    original=reader.document.doc.tobytes(no_new_id=True)
    assert not reader.view.finish_inline_text()
    assert reader.view.inline_editor is editor and editor.input.toPlainText()
    assert reader.document.doc.tobytes(no_new_id=True)==original
    assert reader.view.finish_inline_text(cancel=True)
    assert reader.view.inline_editor is None and not reader.document.dirty
    editor=place(reader); QTest.keyClicks(editor.input,"cancelled")
    QTest.keyClick(editor.input,Qt.Key.Key_Escape)
    assert reader.view.inline_editor is None and "cancelled" not in reader.document.page_text(0)


def test_original_text_is_edited_in_place_and_keeps_font_size_color(reader,qapp):
    reader.document.doc[0].insert_text((40,210),"Colored title",fontname="hebo",fontsize=18.5,color=(.8,.1,.2))
    reader.after_change()
    box=reader.document.doc[0].search_for("Colored title")[0]
    reader.edit_at(0,tuple((box.tl+box.br)/2))
    editor=reader.view.inline_editor
    assert editor and editor.region and editor.input.toPlainText()=="Colored title"
    editor.input.selectAll(); QTest.keyClicks(editor.input,"Colored note")
    assert reader.view.finish_inline_text(),editor.error
    span=next(s for b in reader.document.doc[0].get_text("dict")["blocks"] for l in b.get("lines",[]) for s in l["spans"] if "Colored note" in s["text"])
    assert span["size"]==pytest.approx(18.5,abs=.01)
    assert span["color"]==0xcc1a33
    assert "Bold" in span["font"]
    assert "Colored title" not in reader.document.page_text(0)
    reader.undo(); assert reader.document.doc[0].search_for("Colored title")


def test_font_catalog_and_offline_math_coverage():
    for identity,item in catalog().items():
        if item["bundled"]:
            path=catalog_path(identity); assert path
            assert hashlib.sha256(path.read_bytes()).hexdigest()==item["sha256"]
    assert library_face("sans-sc").has_glyph(ord("中"),fallback=False)
    assert library_face("math").has_glyph(ord("∇"),fallback=False)


def test_colored_highlight_pen_and_visible_save_as(reader):
    reader.set_annotation_color("highlight",QColor("#66c58c"))
    box=reader.document.doc[0].search_for("reading")[0]
    reader.apply_markup("highlight",{0:[tuple(box)]})
    page=reader.document.doc[0]; annot=next(page.annots())
    assert annot.colors["stroke"]==pytest.approx(QColor("#66c58c").getRgbF()[:3],abs=.002)
    reader.set_annotation_color("ink",QColor("#e66155")); reader.set_ink_width(4)
    reader.view.inkChosen.emit(0,[[(70,300),(90,320)]])
    page=reader.document.doc[0]; annot=list(page.annots())[-1]
    assert annot.colors["stroke"]==pytest.approx(QColor("#e66155").getRgbF()[:3],abs=.002)
    assert annot.border["width"]==4
    assert reader.save_as_action in reader.save_button.menu().actions()


def test_typed_signature_is_offline_searchable_and_undoable(document,tmp_path):
    document.add_typed_signature(0,(50,320,270,390),"Jun Li")
    assert "Jun Li" in document.page_text(0)
    path=tmp_path/"signed.pdf"; document.save(path)
    assert path.stat().st_size<250000
    document.undo(); assert "Jun Li" not in document.page_text(0)


def test_viewport_mouse_and_keyboard_reach_native_editor(reader,qapp):
    editor=place(reader)
    QTest.keyClicks(reader.view,"native input")
    assert editor.input.toPlainText()=="native input"
    position=reader.view.mapFromScene(editor.proxy.mapToScene(12,8))
    QTest.mouseClick(reader.view.viewport(),Qt.MouseButton.LeftButton,pos=position)
    assert reader.view.inline_editor is editor
    QTest.keyClick(reader.view,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(reader.view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
    assert qapp.clipboard().text()=="native input"
    QTest.keyClick(reader.view,Qt.Key.Key_Escape)
    assert reader.view.inline_editor is None and not reader.document.dirty


def test_repeated_editors_and_signatures_release_widgets_and_qt_fonts(reader,qapp):
    import gc,weakref
    from PySide6.QtCore import QCoreApplication,QEvent
    from pdfreader import fonts
    from pdfreader.dialogs import SignatureDialog
    refs=[]
    for _ in range(12):
        editor=place(reader); refs.append(weakref.ref(editor))
        reader.view.finish_inline_text(cancel=True); del editor
        dialog=SignatureDialog(reader); refs.append(weakref.ref(dialog)); dialog.reject(); del dialog
        qapp.processEvents(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); qapp.processEvents()
    gc.collect()
    assert not any(ref() is not None for ref in refs)
    assert not fonts._qt_fonts and not fonts._font_owners


def test_moving_original_text_changes_pdf_position_and_undo_restores(reader,qapp):
    from PySide6.QtCore import QPointF
    box=reader.document.doc[0].search_for("Replace this original sentence.")[0]
    reader.edit_at(0,tuple((box.tl+box.br)/2))
    editor=reader.view.inline_editor
    assert editor is not None
    editor.point+=QPointF(10,40); editor.reflow()
    assert reader.view.finish_inline_text()
    moved=reader.document.doc[0].search_for("Replace this original sentence.")[0]
    assert moved.x0==pytest.approx(box.x0+10,abs=.1)
    assert moved.y0==pytest.approx(box.y0+40,abs=.1)
    reader.undo()
    assert reader.document.doc[0].search_for("Replace this original sentence.")[0]==box


def test_native_editor_undo_and_bounded_paste(reader,qapp):
    from PySide6.QtCore import QMimeData
    editor=place(reader)
    QTest.keyClicks(reader.view,"a word")
    QTest.keyClick(reader.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
    assert reader.view.inline_editor is editor
    assert editor.input.toPlainText()!="a word" and not reader.document.dirty
    before=editor.input.toPlainText()
    mime=QMimeData(); mime.setText("x"*25001)
    editor.input.insertFromMimeData(mime)
    assert editor.input.toPlainText()==before
