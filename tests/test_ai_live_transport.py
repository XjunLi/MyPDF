"""Actual worker threads and a loopback HTTP server; never keys or external AI."""
import base64
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.request import build_opener, ProxyHandler
import pytest
from PySide6.QtCore import QObject, QTimer, Slot
from pdfreader.ai import AIJob


def wait_until(qapp, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    qapp.processEvents()
    assert predicate(), "asynchronous operation did not complete"


@pytest.fixture
def service(monkeypatch):
    class Service:
        plans = deque()
        requests = []
        release = threading.Event()
    service = Service()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            service.requests.append(json.loads(body))
            plan = service.plans.popleft()
            self.send_response(plan.get('code', 200))
            self.send_header('Content-Type', plan.get('mime', 'text/event-stream'))
            self.end_headers()
            try:
                for frame in plan.get('frames', []):
                    data = frame if isinstance(frame, bytes) else ('data: ' + json.dumps(frame, ensure_ascii=False) + '\r\n\r\n').encode('utf-8')
                    # Split UTF-8 tokens across writes, as real network packets do.
                    for start in range(0, len(data), 19):
                        self.wfile.write(data[start:start+19])
                    self.wfile.flush()
                    time.sleep(plan.get('delay', .01))
                if plan.get('hold'):
                    service.release.wait(3)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service.url = f'http://127.0.0.1:{server.server_port}/v1'
    # Isolate this local fixture from machine-specific proxy settings.
    monkeypatch.setattr('pdfreader.ai.build_opener', lambda *args: build_opener(ProxyHandler({}), *args))
    yield service
    service.release.set()
    server.shutdown()
    server.server_close()
    thread.join(1)


def frame(content='', reason=None, reasoning=None):
    delta = {'content': content}
    if reasoning:
        delta['reasoning_content'] = reasoning
    return {'choices': [{'delta': delta, 'finish_reason': reason}]}


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.chunks, self.answers, self.errors, self.statuses, self.threads = [], [], [], [], []
    @Slot(str)
    def chunk(self, text):
        self.chunks.append(text)
        self.threads.append(threading.get_ident())
    @Slot(str)
    def finished(self, text): self.answers.append(text)
    @Slot(str)
    def failed(self, text): self.errors.append(text)
    @Slot(str)
    def status(self, text): self.statuses.append(text)


def start_job(service):
    job = AIJob({'enabled': True, 'endpoint': service.url, 'model': 'deepseek-flash', 'key': 'synthetic-key'},
                [{'role': 'user', 'content': 'synthetic question'}])
    receiver = Receiver()
    job.chunk.connect(receiver.chunk)
    job.finished.connect(receiver.finished)
    job.failed.connect(receiver.failed)
    job.status.connect(receiver.status)
    job.start()
    return job, receiver


def test_real_stream_reasoning_unicode_and_length_are_not_silent(qapp, service):
    service.plans.append({'frames': [b': heartbeat\n\n', frame(reasoning='thinking'), frame('∇ 梯度，'), frame('长回答', 'length')], 'hold': True})
    job, receiver = start_job(service)
    wait_until(qapp, lambda: bool(receiver.answers or receiver.errors))
    assert receiver.answers == ['∇ 梯度，长回答'] and not receiver.errors
    assert job.finish_reason == 'length'
    assert 'max_tokens' not in service.requests[0]
    assert '模型正在思考' in receiver.statuses
    assert set(receiver.threads) == {threading.get_ident()}
    wait_until(qapp, lambda: not job.thread.is_alive())  # Does not wait for EOF/[DONE].


def test_disconnect_preserves_partial_but_never_reports_success(qapp, service):
    service.plans.append({'frames': [frame('Useful partial answer')]})
    job, receiver = start_job(service)
    wait_until(qapp, lambda: bool(receiver.errors or receiver.answers))
    assert not receiver.answers and '中断' in receiver.errors[0]
    assert ''.join(receiver.chunks) == 'Useful partial answer'


def test_silent_socket_times_out_with_partial_and_cancel_is_responsive(qapp, service, monkeypatch):
    monkeypatch.setattr(AIJob, 'IO_TIMEOUT', .25)
    service.plans.append({'frames': [frame('Partial')], 'hold': True})
    job, receiver = start_job(service)
    wait_until(qapp, lambda: bool(receiver.errors))
    assert '超时' in receiver.errors[0] and ''.join(receiver.chunks) == 'Partial'
    service.plans.append({'frames': [frame('Cancel me')], 'hold': True})
    job2, second = start_job(service)
    wait_until(qapp, lambda: bool(second.chunks))
    started = time.monotonic()
    job2.cancel()
    assert time.monotonic() - started < .1
    wait_until(qapp, lambda: not job2.thread.is_alive())
    assert not second.answers and not second.errors


def test_http_400_reports_sanitized_provider_message(qapp, service):
    body = json.dumps({'error': {'message': 'Unsupported file input; synthetic-key; data:image/png;base64,QUJDRA=='}}).encode()
    service.plans.append({'code': 400, 'mime': 'application/json', 'frames': [body]})
    job, receiver = start_job(service)
    wait_until(qapp, lambda: bool(receiver.errors))
    assert 'HTTP 400' in receiver.errors[0] and 'Unsupported file input' in receiver.errors[0]
    assert 'synthetic-key' not in receiver.errors[0] and 'QUJDRA' not in receiver.errors[0]


def test_multiline_sse_data_is_one_event(qapp, service):
    service.plans.append({'frames': [b'data: {"choices":\r\ndata: [{"delta":{"content":"math"},"finish_reason":"stop"}]}\r\n\r\n']})
    job, receiver = start_job(service)
    wait_until(qapp, lambda: bool(receiver.answers or receiver.errors))
    assert receiver.answers == ['math'] and not receiver.errors


@pytest.fixture
def window(qapp, pdf_path, tmp_path, monkeypatch, service):
    from pdfreader.app import MainWindow
    monkeypatch.setenv('PDFREADER_DATA_DIR', str(tmp_path / 'data'))
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.open_file(pdf_path)
    window.settings.values['ai'] = {'enabled': True, 'endpoint': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'key': 'synthetic-key'}
    # Keep the real transport and threads, but route exclusively to loopback.
    def local_job(config, messages):
        config = dict(config, endpoint=service.url)
        return AIJob(config, messages)
    monkeypatch.setattr('pdfreader.panels.AIJob', local_job)
    window.show()
    window.show_panel('ai')
    yield window
    for session in window.sessions:
        session.document.saved_state = session.document.state
    window.close()
    qapp.processEvents()


def test_deepseek_full_pdf_real_process_and_stream_keep_gui_alive(qapp, window, service):
    service.plans.append({'frames': [frame(reasoning='test'), *[frame(('Markdown **bold** ∇ and `code`.\n\n') * 12) for _ in range(60)], frame('', 'stop')]})
    heartbeat = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
    timer.start()
    window.view.goto(1)
    window.selection_changed('Chosen excerpt')
    window.ai.input.setPlainText('Question')
    window.ai.send()
    assert window.ai.preparation and not window.ai.job
    wait_until(qapp, lambda: bool(service.requests), timeout=15)
    assert window.ai.job
    window.view.set_zoom(2)
    window.view.goto(0)  # Reading remains interactive during the network stream.
    wait_until(qapp, lambda: not window.ai.job and not window.ai.preparation)
    timer.stop()
    assert len(heartbeat) > 15
    assert max(b - a for a, b in zip(heartbeat, heartbeat[1:])) < .5
    request = service.requests[0]
    serialized = json.dumps(request, ensure_ascii=False)
    assert 'file_data' not in serialized and 'max_tokens' not in request
    background = request['messages'][1]['content']
    assert len([part for part in background if part['type'] == 'image_url']) == 2
    assert 'Second page' in serialized and 'Chosen excerpt' in serialized
    assert '第 2 页' in request['messages'][-1]['content'][0]['text']
    assert window.ai.document_context['mode'] == 'pages'
    assert window.ai.entries[-1][0] == 'assistant'
    assert window.ai.store.load(window.ai.conversation_id)['document_context']['mode'] == 'pages'
    assert not list(window.ai.store.path.parent.glob('attachment-*'))


def test_image_only_truncation_explained_and_user_can_continue(qapp, window, service):
    service.plans.extend([{'frames': [frame('Beginning ∇', 'length')]}, {'frames': [frame('Rest of answer', 'stop')]}])
    window.ai.set_attachment(2)
    window.ai.input.setPlainText('Image only')
    window.ai.send()
    wait_until(qapp, lambda: bool(service.requests) and not window.ai.job and not window.ai.preparation)
    assert window.ai.entries[-1][0] == 'info' and '上限' in window.ai.entries[-1][1]
    assert window.ai.continue_button.isVisible()
    assert window.ai.store.load(window.ai.conversation_id)['continue_from'] == 'Beginning ∇'
    assert len(service.requests) == 1  # No automatic second billable request.
    assert json.dumps(service.requests[0]).count('data:image/png;base64,') == 1
    window.ai.continue_answer()
    wait_until(qapp, lambda: len(service.requests) == 2 and not window.ai.job and not window.ai.preparation)
    assert 'Beginning ∇' in service.requests[1]['messages'][-1]['content'][0]['text']
    assert window.ai.entries[-1] == ('assistant', 'Rest of answer')
    from pdfreader.ai_preparation import AttachmentJob
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not window.ai.findChildren(AttachmentJob)


def test_cancel_preparation_keeps_draft_and_never_sends(qapp, window, service):
    window.ai.input.setPlainText('Keep this draft')
    window.ai.send()
    assert window.ai.preparation
    window.ai.cancel()
    wait_until(qapp, lambda: not window.ai.preparation)
    assert window.ai.input.toPlainText() == 'Keep this draft'
    assert not window.ai.snapshot()['pending']
    assert not service.requests
    assert not list(window.ai.store.path.parent.glob('attachment-*'))


def test_cancel_during_request_serialization_does_not_connect(qapp, monkeypatch):
    from unittest.mock import Mock
    import pdfreader.ai as client
    entered, release = threading.Event(), threading.Event()
    original = client.json.dumps
    def delayed_encode(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)
    opener = Mock()
    monkeypatch.setattr(client.json, 'dumps', delayed_encode)
    monkeypatch.setattr(client, 'build_opener', opener)
    job = AIJob({'enabled': True, 'endpoint': 'http://127.0.0.1:9/v1', 'model': 'fake'}, [])
    job.start()
    assert entered.wait(1)
    job.cancel()
    release.set()
    wait_until(qapp, lambda: not job.thread.is_alive())
    opener.assert_not_called()
