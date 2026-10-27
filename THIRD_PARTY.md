# Third-party components

PDF Reader application source is supplied under GNU AGPL v3. Dependencies are unmodified upstream builds. The bundled DLLs remain separate and can be replaced with compatible builds. Build scripts and dependency pins accompany the source.

| Component | Version | License / upstream source |
| --- | --- | --- |
| PyMuPDF / MuPDF | 1.26.7 | [AGPL v3](https://pymupdf.readthedocs.io/en/latest/about.html), [source](https://github.com/pymupdf/PyMuPDF/tree/1.26.7) |
| PySide6 Essentials / Qt | 6.8.3 | [LGPL v3 / GPL](https://doc.qt.io/qtforpython-6.8/licenses.html), [source](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.8.3) |
| Shiboken6 | 6.8.3 | LGPL v3, same source tree as PySide6 |
| pywinpty | 3.0.2 | MIT, [source](https://github.com/andfoy/pywinpty/tree/v3.0.2) |
| OpenConsole / ConPTY helpers | bundled with pywinpty | MIT, [source](https://github.com/microsoft/terminal) |
| winpty helper | bundled with pywinpty | MIT, [source](https://github.com/rprichard/winpty) |
| pyte | 0.8.2 | LGPL v3, [source](https://github.com/selectel/pyte) |
| wcwidth | installed wheel version recorded in dependency-lock.txt | MIT, [source](https://github.com/jquast/wcwidth) |
| CPython | 3.12 | PSF license, [source](https://github.com/python/cpython/tree/3.12) |
| SQLite | 3.53.4, via Python sqlite3 | [Public domain dedication](https://sqlite.org/copyright.html), [source](https://sqlite.org/src/) |
| Tesseract eng / chi_sim fast language data | downloaded 2026-10-27 | Apache 2.0, [source](https://github.com/tesseract-ocr/tessdata_fast); SHA-256 recorded in OCR_MODELS.json |
| PyInstaller bootloader | 6.16.0 | GPL with bootloader exception, [source](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |

Tesseract recognition is provided by the MuPDF build with the separately bundled language data. The PDF/OCR application does not make HTTP requests. Only the AI module makes explicit user-initiated HTTP requests; user-launched terminal programs operate independently.

License texts are included under `licenses/`. Refer to the linked upstream repositories for corresponding dependency source and build instructions. Fonts used by the application are read from the Windows installation; the sample PDF contains document font subsets.
