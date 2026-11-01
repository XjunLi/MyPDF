import pymupdf as fitz
import pytest
from pdfreader.engine import PdfDocument
import pdfreader.text_layout as writer


@pytest.mark.parametrize("fontname,text",[("library:sans-sc","A note 中文与公式 ∇f(x)"),("library:sans-sc-bold","Bold 中文 ∑x²"),("library:handwriting","Jun Li")])
def test_subset_glyphs_render_identically_to_full_font(tmp_path,monkeypatch,fontname,text):
    original=writer.subset_font
    reference=PdfDocument(); reference.new()
    monkeypatch.setattr(writer,"subset_font",lambda face,chars:face.buffer)
    reference.insert_text_box(0,(50,100),text,18,fontname)
    expected=reference.doc[0].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).samples
    monkeypatch.setattr(writer,"subset_font",original)
    actual=PdfDocument(); actual.new(); actual.insert_text_box(0,(50,100),text,18,fontname)
    assert actual.doc[0].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).samples==expected
    path=tmp_path/"subset.pdf"; actual.save(path)
    assert path.stat().st_size<300000
    reference.close(); actual.close()
