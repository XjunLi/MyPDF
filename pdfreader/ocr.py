"""Isolated OCR worker; deliberately imports no GUI or network clients."""
from pathlib import Path
import json
import sys

import pymupdf as fitz
from .paths import tessdata_path


def recognize(input_path, output_dir, pages, language="eng+chi_sim"):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = []
    with fitz.open(input_path) as doc:
        for index, number in enumerate(pages):
            p = doc[number]
            p.set_rotation(0)
            # Bound raster memory for oversized engineering sheets.
            dpi = min(240, max(72, int((18_000_000 / (p.rect.width * p.rect.height)) ** .5 * 72)))
            pix = p.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False, annots=False)
            data = pix.pdfocr_tobytes(language=language, tessdata=str(tessdata_path()))
            with fitz.open(stream=data, filetype="pdf") as layer:
                text = layer[0].get_text().strip()
                if text:
                    # Retain Tesseract's Unicode text and exact positioning,
                    # but remove the duplicate raster. The original PDF is untouched.
                    for image in layer[0].get_images():
                        layer[0].delete_image(image[0])
                    filename = output / f"layer-{number}.pdf"
                    layer.save(filename, garbage=4, deflate=True)
                    result.append([number, str(filename)])
            progress = json.dumps({"done": index + 1, "total": len(pages), "page": number + 1})
            (output / "progress.json").write_text(progress, encoding="utf-8")
            print(progress, flush=True)
    (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def main(args):
    try:
        recognize(args[0], args[1], json.loads(args[2]), args[3])
        return 0
    except Exception as exc:
        output = Path(args[1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "error.txt").write_text(str(exc), encoding="utf-8")
        print(str(exc), file=sys.stderr, flush=True)
        return 1
