# PDF Reader development

- The user's primary checkout is `E:\MyPDF`, with remote `https://github.com/XjunLi/MyPDF.git`.
- Continue editing this one checkout. Use Git commits and annotated version tags for history; do not copy the project into `PDFReader-<version>` directories.
- Write build output to `dist/PDF Reader`, development scratch and test output to `work/`, and the local Python environment to `.venv/`. These paths are ignored.
- Current supported platform: Windows 10/11 x64, Python 3.12. macOS is not supported yet. Do not claim cross-platform support without implementing and validating it.
- PDF reading/editing/OCR stay offline. Only user-initiated AI requests can use the configured network service. Never use real user keys or external AI services in tests.
- The terminal feature is paused. Keep the toolbar and right sidebar limited to AI and comments; the previous implementation is recoverable from tag `v0.6.0`.
- Preserve the minimal vector icon UI, centered top tools, bottom document tabs, text selection, automatic OCR, Markdown chat, and local encrypted SQLite history.
- Before committing, run relevant tests, `git diff --check`, and inspect staged files. Never commit settings, keys, chat databases, user PDFs, virtual environments, or generated executables.
- Tests: `.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=work/pytest -o cache_dir=work/pytest-cache`.
- Integration: `.\.venv\Scripts\python.exe main.py --smoke-test work/smoke-source` (isolated local data and simulated AI responses).
- Build: `.\build.ps1`; use `-SkipInstall` only with the existing verified environment. Verify the packaged app with `--smoke-test` too.
- Update `pdfreader/__init__.py`, `assets/version_info.txt`, and `CHANGELOG.md` for a release. Push only within the user's authorized scope; never force-push published history.
