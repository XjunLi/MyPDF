import json
from urllib.error import HTTPError
from unittest.mock import patch
from pdfreader.ai import AIJob


class Response:
    headers = {"Content-Type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        for text in ["你好", "，世界。"]:
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n").encode()
        yield b"data: [DONE]\n"


def test_streaming_and_explicit_context_only(qapp):
    config = {"endpoint": "http://127.0.0.1:12345/v1", "model": "test-model", "enabled": True, "key": "test-key"}
    messages = [{"role": "user", "content": "A deliberate excerpt"}]
    job = AIJob(config, messages)
    chunks, answers, failures = [], [], []
    job.chunk.connect(chunks.append)
    job.finished.connect(answers.append)
    job.failed.connect(failures.append)
    with patch("pdfreader.ai.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        job._run()
        request = opener.return_value.open.call_args.args[0]
        assert json.loads(request.data)["messages"] == messages
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.full_url == "http://127.0.0.1:12345/v1/chat/completions"
    assert chunks == ["你好", "，世界。"]
    assert answers == ["你好，世界。"]
    assert not failures


def test_disabled_ai_never_connects(qapp):
    job = AIJob({"enabled": False}, [])
    errors = []
    job.failed.connect(errors.append)
    with patch("pdfreader.ai.build_opener") as opener:
        job._run()
        opener.assert_not_called()
    assert errors


def test_errors_do_not_leak_api_key(qapp):
    config = {"endpoint": "https://example.com/v1", "model": "test", "enabled": True, "key": "sensitive-test-key"}
    job = AIJob(config, [])
    errors = []
    job.failed.connect(errors.append)
    with patch("pdfreader.ai.build_opener", side_effect=ValueError("bad sensitive-test-key")):
        job._run()
    assert errors == ["bad [已隐藏]"]


def test_cancelled_job_never_delivers_stale_response(qapp):
    job = AIJob({"endpoint": "http://127.0.0.1:12345/v1", "model": "test", "enabled": True}, [])
    answers = []
    job.finished.connect(answers.append)
    job.cancel()
    with patch("pdfreader.ai.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        job._run()
    assert not answers


def test_http_client_serializes_inline_pdf_and_current_image_together(qapp):
    from pdfreader.ai_context import background_message
    config = {"endpoint": "https://example.invalid/v1", "model": "test", "enabled": True, "key": "private-key"}
    messages = [background_message({"name": "sample.pdf", "pages": 2, "mode": "pdf", "data": "cGRm"}),
                {"role": "user", "content": [{"type": "text", "text": "Question"},
                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}},
                 {"type": "text", "text": "Selected words"}]}]
    job = AIJob(config, messages)
    with patch("pdfreader.ai.build_opener") as opener:
        opener.return_value.open.return_value = Response()
        job._run()
        assert opener.return_value.open.call_count == 1
        request = opener.return_value.open.call_args.args[0]
        assert json.loads(request.data)["messages"] == messages
        assert "private-key" not in request.data.decode()


def test_pdf_rejection_has_compatibility_hint_without_echoing_service_response(qapp):
    config = {"endpoint": "https://example.invalid/v1", "model": "test", "enabled": True, "key": "private-key"}
    job = AIJob(config, [{"role": "user", "content": [{"type": "file", "file": {"filename": "doc.pdf", "file_data": "cGRm"}}]}])
    errors = []
    job.failed.connect(errors.append)
    with patch("pdfreader.ai.build_opener") as opener:
        opener.return_value.open.side_effect = HTTPError(config["endpoint"], 400, "private-key rejected", {}, None)
        job._run()
    assert "全文文字" in errors[0] and "HTTP 400" in errors[0]
    assert "private-key" not in errors[0]
