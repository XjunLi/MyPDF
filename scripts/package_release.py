"""Package only a verified native build, retaining Mac executable modes/symlinks."""
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from pdfreader import __version__
output = root/"work/release"
output.mkdir(parents=True,exist_ok=True)
mac = sys.platform == "darwin"
arch = "arm64" if platform.machine().lower() in ("arm64","aarch64") else "x64"
label = ("macOS" if mac else "Windows") + "-" + arch
source = json.loads((root/"work/smoke-source/result.json").read_text(encoding="utf-8"))
packaged = json.loads((root/"work/smoke-packaged/result.json").read_text(encoding="utf-8"))
assert source["ok"] and packaged["ok"]
suites = ET.parse(root/"work/tests.xml").getroot().findall("testsuite")
counts = {field: sum(int(s.get(field,0)) for s in suites) for field in ("tests","failures","errors","skipped")}
assert counts["tests"] >= 175 and counts["failures"] == counts["errors"] == 0
commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
report = {"version":__version__,"commit":commit,"system":platform.system(),"system_version":platform.release(),
          "architecture":arch,"python":platform.python_version(),"sqlite":sqlite3.sqlite_version,
          "dependencies":{n:importlib.metadata.version(n) for n in ("PyMuPDF","PySide6-Essentials","PyInstaller")},
          "tests":counts,"source":source,"packaged":packaged,
          "encryption":"macOS Keychain + AES-256-GCM" if mac else "Windows DPAPI",
          "real_ai_service_called":False,"apple_notarized":False if mac else None}
if mac:
    report["native_cocoa"] = json.loads((root/"work/smoke-native/result.json").read_text(encoding="utf-8"))
    assert report["native_cocoa"]["ok"]
validation = output/f"PDFReader-validation-{label}.json"
validation.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
destination = output/f"PDFReader-{label}.zip"
if mac:
    subprocess.run(["/usr/bin/ditto","-c","-k","--sequesterRsrc","--keepParent",str(root/"dist/PDF Reader.app"),str(destination)],check=True)
else:
    app = root/"dist/PDF Reader"
    with ZipFile(destination,"w",ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file(): archive.write(path,Path("PDFReader")/path.relative_to(app))
with ZipFile(destination) as archive:
    assert archive.testzip() is None
shutil.copy2(root/"work/PDFReader-source.zip",output/"PDFReader-source.zip")
print(json.dumps({"file":destination.name,"bytes":destination.stat().st_size,"sha256":hashlib.sha256(destination.read_bytes()).hexdigest(),"validation":report},ensure_ascii=False))
