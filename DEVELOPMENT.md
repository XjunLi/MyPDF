# 开发、构建与发布

主开发目录为 `E:\MyPDF`，远端为 [XjunLi/MyPDF](https://github.com/XjunLi/MyPDF)。使用同一 checkout 和 Git 历史，不创建每版本源码副本。

## Windows

```powershell
Set-Location E:\MyPDF
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r dependency-lock.txt
.\.venv\Scripts\python.exe main.py
.\build.ps1 -SkipInstall
```

固定输出：`dist/PDF Reader/PDF Reader.exe`。

## macOS

需要 macOS 15+、Python 3.12，在目标架构的 Mac 上原生构建：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r dependency-lock-macos.txt
.venv/bin/python main.py
bash build-macos.sh
```

固定输出：`dist/PDF Reader.app`。本次仅发布 Apple Silicon（arm64）Mac 包。应用包含文档关联、Retina 图标和 Qt 的 Finder 文件打开事件处理。原生加密使用 Keychain 保存随机 256-bit 密钥，数据库记录由 AES-GCM 认证加密；本地 keychain-id 只含非秘密标识。钥匙串失败不得回退到明文。

## 验证

`python scripts/build.py` 在当前环境执行全部 pytest、源码集成检查、PyInstaller 原生打包和打包应用集成检查。测试均用隔离目录和模拟服务，禁止真实用户密钥与外部 AI 调用。Windows 可以通过 `build.ps1` 安装锁定依赖，Mac 通过 `build-macos.sh`。

```bash
python -m pytest tests -q --basetemp=work/pytest -o cache_dir=work/pytest-cache
python main.py --smoke-test work/smoke-source
python main.py --performance-test work/performance-source
python main.py --inspect-pdf /path/to/sample.pdf work/inspect-source
```

`--inspect-pdf` 只读源文件，生成本地截图和处理结果，不使用用户配置或网络。私人 PDF、截图、数据库、密钥和设置不能进入 Git。临时内容在 `work/`，打包产物在 `dist/`，本地环境在 `.venv/`。

## 发布

`.github/workflows/build.yml` 在 Windows x64、macOS arm64 分别测试与打包。main 的每次提交保存 Actions 构建产物；注释版本标签 `v*` 在全部检查通过后自动发布 GitHub Release，附上两个平台 ZIP、精确提交源码、SHA256 和每平台验证记录。正式包不写入 Git 源码历史。

发布前同步 Python 版本号、Windows version_info、macOS spec、CHANGELOG 和验证说明。先推送 main 并检查 Actions，再创建注释标签。不要重写已发布的标签或 force-push。

Mac 包目前只有 ad-hoc 签名，没有 Developer ID 和 Apple 公证，README 必须说明首次启动流程；不得声称已公证。物理打印机、所有 PDF 变体与所有系统版本仍需要实际环境验证。

## 字体与资源回归

字体清单位于 `assets/fonts/catalog.json`，随包文件必须通过大小和 SHA-256 校验并附带对应 OFL。下载只能使用清单中固定提交的 HTTPS 官方地址；默认不开启，自动获取需用户勾选。禁止从 PDF 中的网址获取字体或将 PDF 内容、路径、文字、API Key 放入字体请求。商业字体缺失时提示导入或明确替代。

fontTools 对 CID-keyed CFF 字体子集必须保留 glyph IDs；仅检查文字提取不能证明显示正确。`tests/test_font_pixels.py` 将保存重开的子集字形与完整字体逐像素比较。

`--performance-test` 使用隔离设置、关闭 OCR 与网络请求，测量预热后的 48 次编辑器 / 10 次签名窗口生命周期及 200 次混合缩放滚动。断言关闭后无编辑器或 Qt 字体注册残留，位图 <=64 MiB、字体输入缓存 <=64 MiB、子集 <=8 MiB，并记录 RSS 与 median / p95 / max。内存阈值用于发现持续保留，不代表所有 PDF 都具有相同延迟；第三方原生库和文档本身也占用内存。发布构建必须运行源码、打包版；macOS 另外运行原生 Cocoa / Retina 检查。
