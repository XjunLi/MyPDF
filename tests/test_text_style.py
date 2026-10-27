from pathlib import Path
import os
import pytest
import pymupdf as fitz
from pdfreader.engine import PdfDocument, PdfError


def styled_document(tmp_path, rotation=0):
    pdf = fitz.open()
    page = pdf.new_page(width=520, height=360)
    page.draw_rect((25, 25, 495, 110), fill=(.95, .97, 1), color=None)
    page.insert_text((40, 72), "Red title ", fontname="hebo", fontsize=19.5, color=(.8, .1, .2))
    x = 40 + fitz.get_text_length("Red title ", fontname="hebo", fontsize=19.5)
    page.insert_text((x, 72), "green tail", fontname="tiit", fontsize=11.25, color=(.1, .5, .2))
    page.insert_text((40, 190), "Leave this unchanged.", fontsize=15)
    page.set_rotation(rotation)
    path = tmp_path / "styles.pdf"
    pdf.save(path)
    pdf.close()
    result = PdfDocument()
    result.open(path)
    return result


def spans(page):
    return [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_edit_preserves_mixed_fonts_sizes_colors_and_baselines(tmp_path, rotation):
    doc = styled_document(tmp_path, rotation)
    region = doc.text_region(0, (35, 35, 470, 90))
    assert region.text == "Red title green tail"
    doc.replace_text(0, region, "Tan title green tail")
    updated = spans(doc.doc[0])
    heading = next(s for s in updated if "Tan title" in s["text"])
    tail = next(s for s in updated if "green tail" in s["text"])
    assert heading["font"] == "Helvetica-Bold"
    assert heading["size"] == pytest.approx(19.5)
    assert heading["color"] == 0xcc1a33
    assert tail["font"] == "Times-Italic"
    assert tail["size"] == pytest.approx(11.25)
    assert tail["color"] == 0x1a8033
    assert heading["origin"][1] == pytest.approx(72, abs=.01)
    assert tail["origin"][1] == pytest.approx(72, abs=.01)
    assert "Red title" not in doc.doc[0].get_text()
    assert "Leave this unchanged." in doc.doc[0].get_text()
    assert len(doc.doc[0].get_drawings()) == 1
    assert doc.doc[0].rotation == rotation
    saved = tmp_path / "edited.pdf"
    doc.save(saved)
    doc.open(saved)
    assert next(s for s in spans(doc.doc[0]) if "Tan title" in s["text"])["color"] == 0xcc1a33
    doc.close()


def test_edit_undo_and_no_change_do_not_restyle_original(tmp_path):
    doc = styled_document(tmp_path)
    original = doc.doc[0].get_pixmap().samples
    state = doc.state
    region = doc.text_region(0, (35, 35, 470, 90))
    doc.replace_text(0, region, region.text)
    assert doc.state == state and not doc.undo_stack
    doc.replace_text(0, region, "Tan title green tail")
    doc.undo()
    assert doc.doc[0].get_pixmap().samples == original
    doc.close()


def test_overflow_or_missing_font_glyph_preserves_document(tmp_path):
    doc = styled_document(tmp_path)
    region = doc.text_region(0, (35, 35, 470, 90))
    original = doc.doc.tobytes(no_new_id=True)
    for text in ("Too much text " * 100, "标题 green tail"):
        with pytest.raises(PdfError):
            doc.replace_text(0, region, text)
        assert doc.doc.tobytes(no_new_id=True) == original
        assert not doc.undo_stack
    doc.close()


def test_embedded_font_is_reused_for_replacement(tmp_path):
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    if not font_path.exists():
        pytest.skip("Arial font file not available")
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_font(fontname="EmbeddedFace", fontfile=str(font_path))
    page.insert_text((50, 100), "Original sample text", fontname="EmbeddedFace", fontsize=17.25, color=(.2, .3, .7))
    filename = tmp_path / "embedded.pdf"
    pdf.save(filename)
    pdf.close()
    doc = PdfDocument()
    doc.open(filename)
    before = spans(doc.doc[0])[0]
    region = doc.text_region(0, (45, 70, 480, 120))
    doc.replace_text(0, region, "Revised sample text")
    after = spans(doc.doc[0])[0]
    assert after["font"] == before["font"]
    assert after["size"] == pytest.approx(before["size"])
    assert after["color"] == before["color"]
    assert after["origin"] == pytest.approx(before["origin"])
    doc.close()


def test_chinese_subset_edit_keeps_heading_style_and_excludes_section_number():
    doc = PdfDocument()
    doc.open(Path(__file__).resolve().parents[1] / "samples" / "welcome.pdf")
    phrase = "让批注成为线索"
    rect = doc.doc[0].search_for(phrase)[0]
    box, text = doc.text_block_at(0, (rect.tl + rect.br) / 2)
    assert text == phrase
    region = doc.text_region(0, box)
    assert region.text == phrase
    before = region.primary_style
    doc.replace_text(0, region, "让批注留下线索")
    after = doc.text_region(0, box).primary_style
    from pdfreader.text_edit import normalized_font
    assert normalized_font(after.font) == normalized_font(before.font)
    assert after.size == pytest.approx(before.size)
    assert after.color == before.color
    assert doc.doc[0].search_for("02")
    doc.close()


def test_click_edit_uses_whitespace_but_stops_before_adjacent_column(tmp_path):
    doc = styled_document(tmp_path)
    page = doc.doc[0]
    page.insert_text((300, 190), "NEXT COLUMN", fontsize=15)
    rect = page.search_for("Leave this unchanged.")[0]
    box, _ = doc.text_block_at(0, (rect.tl + rect.br) / 2)
    assert box.x1 <= 296
    region = doc.text_region(0, box)
    doc.replace_text(0, region, "A longer revised sentence.")
    assert doc.doc[0].search_for("NEXT COLUMN")
    assert doc.doc[0].search_for("sentence.")[0].x1 < 300
    doc.close()
