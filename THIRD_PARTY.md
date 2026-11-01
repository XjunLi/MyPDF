# Third-party components

PDF Reader application source is supplied under GNU AGPL v3. Dependencies are unmodified upstream builds. The bundled shared libraries remain separate and can be replaced with compatible builds. Build scripts and dependency pins accompany the source.

| Component | Version | License / upstream source |
| --- | --- | --- |
| PyMuPDF / MuPDF | 1.26.7 | [AGPL v3](https://pymupdf.readthedocs.io/en/latest/about.html), [source](https://github.com/pymupdf/PyMuPDF/tree/1.26.7) |
| PySide6 Essentials / Qt | 6.8.3 | [LGPL v3 / GPL](https://doc.qt.io/qtforpython-6.8/licenses.html), [source](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.8.3) |
| Shiboken6 | 6.8.3 | LGPL v3, same source tree as PySide6 |
| CPython | 3.12 | PSF license, [source](https://github.com/python/cpython/tree/3.12) |
| SQLite | bundled Python sqlite3 version (see platform validation) | [Public domain dedication](https://sqlite.org/copyright.html), [source](https://sqlite.org/src/) |
| Tesseract eng / chi_sim fast language data | downloaded 2026-10-27 | Apache 2.0, [source](https://github.com/tesseract-ocr/tessdata_fast); SHA-256 recorded in OCR_MODELS.json |
| fontTools | 4.68.0 | [MIT](https://github.com/fonttools/fonttools/blob/main/LICENSE), [source](https://github.com/fonttools/fonttools/tree/4.68.0) |
| PyInstaller bootloader | 6.16.0 | GPL with bootloader exception, [source](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |

Tesseract recognition is provided by the MuPDF build with the separately bundled language data. PDF reading, editing and OCR do not make HTTP requests. AI sends only on user action. The separate opt-in font downloader fetches only pinned catalog files; it never sends PDF data or credentials.

License texts are included under `licenses/`. Refer to the linked upstream repositories for corresponding dependency source and build instructions. The UI reads system fonts; PDF insertion and typed signatures also use the open fonts below. The sample PDF contains document font subsets.

macOS additionally uses [cryptography 50.0.2](https://github.com/pyca/cryptography) (Apache-2.0 OR BSD-3-Clause) and [keyring 25.7.0](https://github.com/jaraco/keyring) (MIT), with cffi, pycparser, jaraco.classes, jaraco.context, jaraco.functools and more-itertools. cryptography wheels include OpenSSL. Package license/notice files and the exact dependency inventory are collected in `dependency-licenses/` inside the application resources. The macOS build also uses macholib; it is not an application feature.

## Bundled and optional open fonts

`assets/fonts/catalog.json` records each font's exact upstream URL, byte size, SHA-256, license file and whether it is bundled. Upstream files are unmodified. Generated PDF font subsets are produced at runtime.

| Fonts | Fixed official source | License text |
| --- | --- | --- |
| Noto Sans CJK SC Regular / Bold (bundled); Noto Serif CJK SC Regular / Bold (download only) | [noto-cjk at f8d1575](https://github.com/notofonts/noto-cjk/tree/f8d157532fbfaeda587e826d4cd5b21a49186f7c) | SIL OFL 1.1: `assets/fonts/OFL-CJK.txt`, `OFL-SerifCJK.txt` |
| Noto Sans, Noto Serif, Noto Sans Mono, Noto Sans Math, Noto Sans Symbols 2 | [noto-fonts at ffebf8c](https://github.com/notofonts/noto-fonts/tree/ffebf8c1ee449e544955a7e813c54f9b73848eac) | SIL OFL 1.1: `assets/fonts/OFL-Noto.txt` |
| Caveat variable font (local filename Caveat-Regular.ttf) | [google/fonts at 6c1dbe2](https://github.com/google/fonts/tree/6c1dbe27dcf7d817d4b62cec033ba77fa8e2a5ec/ofl/caveat) | SIL OFL 1.1: `assets/fonts/OFL-Caveat.txt` |

Imported local fonts are stored only in the user's application data and are not redistributed by this repository.
