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
| PyInstaller bootloader | 6.16.0 | GPL with bootloader exception, [source](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |

Tesseract recognition is provided by the MuPDF build with the separately bundled language data. The PDF/OCR application does not make HTTP requests. Only the AI module makes explicit user-initiated HTTP requests.

License texts are included under `licenses/`. Refer to the linked upstream repositories for corresponding dependency source and build instructions. Fonts used by the application are read from the operating system; the sample PDF contains document font subsets.

macOS additionally uses [cryptography 50.0.2](https://github.com/pyca/cryptography) (Apache-2.0 OR BSD-3-Clause) and [keyring 25.7.0](https://github.com/jaraco/keyring) (MIT), with cffi, pycparser, jaraco.classes, jaraco.context, jaraco.functools and more-itertools. cryptography wheels include OpenSSL. Package license/notice files and the exact dependency inventory are collected in `dependency-licenses/` inside the application resources. The macOS build also uses macholib; it is not an application feature.
