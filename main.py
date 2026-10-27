import sys
from pathlib import Path


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--inspect-pdf":
        from pdfreader.diagnostics_pdf import inspect_pdf
        return inspect_pdf(sys.argv[2], sys.argv[3])
    if len(sys.argv) > 2 and sys.argv[1] == "--attachment-worker":
        from pdfreader.ai_preparation import prepare
        return prepare(sys.argv[2])
    if len(sys.argv) > 1 and sys.argv[1] == "--ocr-worker":
        from pdfreader.ocr import main as worker
        return worker(sys.argv[2:])
    if len(sys.argv) > 2 and sys.argv[1] == "--smoke-test":
        from pdfreader.diagnostics import smoke
        return smoke(sys.argv[2])
    from pdfreader.app import run
    return run([arg for arg in sys.argv[1:] if Path(arg).suffix.lower() == ".pdf"])


if __name__ == "__main__":
    sys.exit(main())
