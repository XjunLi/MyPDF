"""Build and verify a native distributable; run on Windows or macOS."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "LICENSE", "THIRD_PARTY.md", "CHANGELOG.md", "DEVELOPMENT.md", "VALIDATION.md", "VALIDATION.json")


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)


def licenses():
    output = ROOT / "work/dependency-licenses"
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        manifest.append({"name": name, "version": dist.version})
        for item in dist.files or []:
            if ".dist-info/" not in str(item) or not any(word in str(item).lower() for word in ("license", "copying", "notice")):
                continue
            path = Path(dist.locate_file(item))
            if path.is_file():
                relative = Path(str(item).split(".dist-info/", 1)[1])
                target = output / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    (output / "packages.json").write_text(json.dumps(sorted(manifest, key=lambda d: d["name"]), indent=2), encoding="utf-8")


def mac_icon():
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(str(ROOT / "assets/reader.svg"))
    chunks = []
    for kind, size in ((b"icp4",16),(b"icp5",32),(b"icp6",64),(b"ic07",128),(b"ic08",256),(b"ic09",512),(b"ic10",1024)):
        image = QImage(size,size,QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image); renderer.render(painter); painter.end()
        data = QByteArray(); stream = QBuffer(data); stream.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(stream,"PNG")
        raw = bytes(data)
        chunks.append(kind + struct.pack(">I",len(raw)+8) + raw)
    body = b"".join(chunks)
    (ROOT/"work/reader.icns").write_bytes(b"icns" + struct.pack(">I",len(body)+8) + body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Build on Windows or macOS; cross-compiling is not supported.")
    os.chdir(ROOT)
    (ROOT/"work").mkdir(exist_ok=True)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(ROOT/"work/pyinstaller-config")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if not args.skip_tests:
        run(sys.executable,"-m","pytest","tests","-q","--basetemp=work/pytest","-o","cache_dir=work/pytest-cache","--junitxml=work/tests.xml")
    run(sys.executable,"main.py","--smoke-test","work/smoke-source",timeout=180)
    licenses()
    run("git","archive","--format=zip","--prefix=PDFReader-source/","-o","work/PDFReader-source.zip","HEAD")
    if sys.platform == "darwin":
        mac_icon()
    run(sys.executable,"-m","PyInstaller","--noconfirm","--clean","--distpath","dist","--workpath","work/pyinstaller","packaging/pdfreader.spec")
    if sys.platform == "darwin":
        app = ROOT/"dist/PDF Reader.app"
        executable = app/"Contents/MacOS/PDF Reader"
        run("/usr/bin/codesign","--verify","--deep","--strict",app)
    else:
        app = ROOT/"dist/PDF Reader"
        executable = app/"PDF Reader.exe"
        for name in DOCS:
            shutil.copy2(ROOT/name,app/name)
        for name in ("licenses","plugins"):
            shutil.copytree(ROOT/name,app/name,dirs_exist_ok=True)
        shutil.copy2(ROOT/"work/PDFReader-source.zip",app/"PDFReader-source.zip")
    run(executable,"--smoke-test",ROOT/"work/smoke-packaged",timeout=180)
    for name in ("source","packaged"):
        result = json.loads((ROOT/f"work/smoke-{name}/result.json").read_text(encoding="utf-8"))
        if not result["ok"]:
            raise SystemExit(f"{name} smoke failed: {result['errors']}")
    print(f"Verified {platform.system()} {platform.machine()}: {app}")


if __name__ == "__main__":
    main()
