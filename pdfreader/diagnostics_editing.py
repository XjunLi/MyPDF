"""Actual widget/scene interaction checks, also run in frozen builds."""
import pymupdf as fitz
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QColor,QInputMethodEvent,QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from .dialogs import SignatureDialog


def exercise_editing(window,root,output):
    app=QApplication.instance()
    fixture=root/"editing-fixture.pdf"
    with fitz.open() as doc:
        page=doc.new_page(width=500,height=700)
        page.draw_rect((30,25,470,110),fill=(.94,.97,1),color=None)
        page.insert_text((50,70),"Editable heading",fontname="hebo",fontsize=18.5,color=(.15,.3,.65))
        page.insert_text((50,150),"Select text, choose a color, and keep reading.",fontsize=12)
        doc.save(fixture)
    window.open_file(fixture); window.toggle_panel(None); window.view.fit_page(); app.processEvents()
    def click(point):
        page=window.document.doc[0]
        local=fitz.Point(point)*page.rotation_matrix
        position=window.view.mapFromScene(window.view.items_by_page[0].mapToScene(QPointF(local.x,local.y)))
        QTest.mouseClick(window.view.viewport(),Qt.MouseButton.LeftButton,pos=position)
    window.mode_actions["edit"].trigger(); click((100,65))
    editor=window.view.inline_editor
    assert editor is not None and editor.region
    QTest.keyClick(window.view,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(window.view,"Edited heading")
    assert editor.input.toPlainText()=="Edited heading",repr(editor.input.toPlainText())
    app.processEvents(); window.grab().save(str(root/"editing-original.png"))
    QTest.keyClick(window.view,Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
    assert window.view.inline_editor is None
    assert window.document.doc[0].search_for("Edited heading")
    span=next(s for b in window.document.doc[0].get_text("dict")["blocks"] for line in b.get("lines",[]) for s in line["spans"] if "Edited heading" in s["text"])
    assert abs(span["size"]-18.5)<.01 and span["color"]==0x264da6
    output["checks"].append("on_page_original_edit_preserves_style_without_dialog")
    window.mode_actions["text"].trigger(); click((65,230))
    editor=window.view.inline_editor
    assert editor is not None
    QTest.keyClicks(window.view,"A note with ")
    event=QInputMethodEvent(); event.setCommitString("中文与公式 ∇f(x)")
    QApplication.sendEvent(window.view,event)
    assert "中文" in editor.input.toPlainText()
    app.processEvents(); window.grab().save(str(root/"editing-insert.png"))
    assert window.view.finish_inline_text()
    assert window.document.doc[0].search_for("中文")
    output["checks"].append("click_insert_native_ime_auto_size_and_subset_fonts")
    window.set_annotation_color("highlight",QColor("#66c58c"))
    box=window.document.doc[0].search_for("Select text")[0]
    window.apply_markup("highlight",{0:[tuple(box)]})
    window.set_annotation_color("ink",QColor("#e66155")); window.set_ink_width(2)
    window.view.inkChosen.emit(0,[[(60,360),(95,345),(125,365)]])
    assert window.save_as_action in window.save_button.menu().actions()
    output["checks"].append("colored_highlight_pen_width_and_visible_save_as")
    dialog=SignatureDialog(window); dialog.tabs.setCurrentIndex(1); dialog.name.setText("Jun Li")
    dialog.show(); app.processEvents(); dialog.grab().save(str(root/"signature-options.png"))
    dialog.reject(); app.processEvents()
    window.mutate(lambda:window.document.add_typed_signature(0,(60,430,280,490),"Jun Li"))
    image=QImage(120,40,QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter,QPen
    painter=QPainter(image); painter.setPen(QPen(QColor("#243b5c"),3)); painter.drawLine(5,30,65,5); painter.drawLine(65,5,110,25); painter.end()
    image_path=root/"signature.png"; image.save(str(image_path))
    window.mutate(lambda:window.document.add_image(0,(300,440,430,485),image_path))
    target=root/"editing-result.pdf"; window.document.save(target)
    assert target.stat().st_size<300000
    with fitz.open(target) as saved:
        assert saved[0].search_for("Jun Li") and saved[0].search_for("中文")
        assert len(list(saved[0].annots()))==2
        assert saved[0].get_images()
    output["checks"].append("typed_and_image_signature_roundtrip")
    window.grab().save(str(root/"editing-result.png"))
    assert window.close_document()
