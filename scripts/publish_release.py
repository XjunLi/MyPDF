"""Prepare a release only when both native reports match this exact tag."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
release = root/"work/release"
tag = os.environ["RELEASE_TAG"]
commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
for platform in ("Windows-x64","macOS-arm64"):
    report = json.loads((release/f"PDFReader-validation-{platform}.json").read_text())
    assert report["version"] == tag.removeprefix("v") and report["commit"] == commit
    assert report["source"]["ok"] and report["packaged"]["ok"]
    assert (release/f"PDFReader-{platform}.zip").stat().st_size > 1_000_000
assert (release/"PDFReader-source.zip").exists()
checksums = []
for path in sorted(release.iterdir()):
    if path.is_file() and path.name != "SHA256SUMS.txt":
        checksums.append(hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.name)
(release/"SHA256SUMS.txt").write_text("\n".join(checksums)+"\n")
notes = """Windows 与 macOS 原生下载，两个平台均通过回归测试及源码 / 打包版集成检查。

- Windows x64：解压完整 ZIP，运行 `PDFReader/PDF Reader.exe`。
- Mac Apple Silicon（M 系列）：选择 `macOS-arm64`。解压后将 `PDF Reader.app` 拖入“应用程序”。
- Mac 当前采用 ad-hoc 签名，尚未获 Apple Developer ID 签名或公证。首次启动可能需在“系统设置 → 隐私与安全性 → 仍要打开”允许此应用。
- 离线 PDF 阅读、编辑和中英 OCR；AI 只在点击发送时联网。macOS 本机记录由钥匙串与 AES-GCM 保护，Windows 保持 DPAPI。
- 附带对应源码、SHA256 和每个平台的实际验证 JSON；测试没有使用私人 API Key 或真实外部 AI 服务。

下载和使用路径见 [README](https://github.com/XjunLi/MyPDF#下载与运行)。
"""
(root/"work/release-notes.md").write_text(notes,encoding="utf-8")
