# Native builds only: each runner packages libraries for its own architecture.
from pathlib import Path
import sys
root = Path(SPECPATH).parent
version = "0.8.0"
mac = sys.platform == "darwin"
datas = [(str(root / "assets"), "assets"), (str(root / "samples"), "samples"),
         (str(root / "work/dependency-licenses"), "dependency-licenses")]
if mac:
    datas += [(str(root / name), ".") for name in ("README.md", "LICENSE", "THIRD_PARTY.md", "CHANGELOG.md", "DEVELOPMENT.md", "VALIDATION.md", "VALIDATION.json")]
    datas += [(str(root / "licenses"), "licenses"), (str(root / "plugins"), "plugins"),
              (str(root / "work/PDFReader-source.zip"), ".")]
a = Analysis([str(root / "main.py")], pathex=[str(root)], datas=datas,
             hiddenimports=["keyring.backends.macOS"] if mac else [],
             excludes=["winpty", "pyte", "wcwidth", "pytest"] + ([] if mac else ["keyring", "cryptography"]))
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PDF Reader",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, argv_emulation=False,
          icon=str(root / ("work/reader.icns" if mac else "assets/reader.ico")),
          version=None if mac else str(root / "assets/version_info.txt"),
          target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PDF Reader")
if mac:
    app = BUNDLE(coll, name="PDF Reader.app", icon=str(root / "work/reader.icns"),
                 bundle_identifier="com.xjunli.pdfreader", version=version,
                 info_plist={
                     "CFBundleDisplayName": "PDF Reader", "CFBundleShortVersionString": version,
                     "LSMinimumSystemVersion": "15.0", "NSHighResolutionCapable": True,
                     "NSRequiresAquaSystemAppearance": True,
                     "CFBundleDocumentTypes": [{"CFBundleTypeName": "PDF document",
                         "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate",
                         "LSItemContentTypes": ["com.adobe.pdf"], "CFBundleTypeExtensions": ["pdf"]}],
                 })
