# 开发与版本管理

主开发目录为 `E:\MyPDF`，远端仓库为 [XjunLi/MyPDF](https://github.com/XjunLi/MyPDF)。源码只有一份，历史由 Git 提交与标签保存，不再复制版本目录。

## 开发环境

当前仅支持 Windows 10/11 x64，建议 Python 3.12。安装依赖需要网络；PDF 阅读、编辑和 OCR 运行时不需要网络。

```powershell
Set-Location E:\MyPDF
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r dependency-lock.txt
.\run.ps1
```

迁移后的本机已经准备 `.venv`，可直接执行 `run.ps1`。其他电脑从 GitHub 克隆后按上面步骤建立自己的环境。

## 验证与构建

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=work/pytest -o cache_dir=work/pytest-cache
.\.venv\Scripts\python.exe main.py --smoke-test work/smoke-source
.\build.ps1 -SkipInstall
& '.\dist\PDF Reader\PDF Reader.exe'
```

`build.ps1` 默认安装锁定依赖、运行全部测试，再生成 `dist/PDF Reader/PDF Reader.exe`。已有依赖时可用 `-SkipInstall`。所有构建、缓存及测试结果均位于忽略目录中；不会新增 `0.x.y` 源码副本。

## Git 工作流程

- `main` 保留可运行版本；每个独立改动做一次清晰的提交。
- 发布前同步代码版本、Windows 文件版本、变更记录及验证记录。
- 使用注释标签（例如 `v0.6.0`、`v0.6.1`）定位版本。提交和标签推送至同一远端，禁止强制覆盖历史。
- `v0.6.0` 是迁移前完整功能基线；`v0.6.1` 暂时移除终端。旧实现可以从 Git 中恢复。
- 安装包不进入 Git 源码历史；需要分享时使用构建产物或 GitHub Release。聊天记录、API Key、用户设置和私有文档均不提交。

## 平台范围

macOS 尚未适配。Qt 和 PDF 引擎具备跨平台基础，但当前本机加密依赖 Windows DPAPI，还需要替换平台相关路径、字体与构建流程，并在真实 macOS 环境中验证。移除终端不会自动使应用获得 Mac 支持。
