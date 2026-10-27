"""The only HTTP client in the application. Invoked only by the Send action."""
import json
import threading
import socket
import time
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QObject, Signal


def validate_endpoint(value):
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("请输入有效的 https:// API 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址不能包含账号、密码、查询参数或片段。")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程 AI 服务需要 HTTPS；HTTP 仅用于本机模型。")
    if parsed.path.endswith(("/responses", "/messages")):
        raise ValueError("此连接使用 Chat Completions。请填写服务的 API Base URL 或 /chat/completions 地址。")
    return value if value.endswith("/chat/completions") else value + "/chat/completions"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def history_for_request(messages, max_characters=24000):
    """Completed recent pairs only. Never re-upload old image attachments."""
    pairs = []
    for user, assistant in zip(messages, messages[1:]):
        if user.get("role") != "user" or assistant.get("role") != "assistant":
            continue
        content = user.get("content", "")
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            content += "\n[历史图片未再次附加]"
        pairs.append([{"role": "user", "content": content},
                      {"role": "assistant", "content": assistant.get("content", "")}])
    pairs = pairs[-4:]
    while pairs and sum(len(message["content"]) for pair in pairs for message in pair) > max_characters:
        pairs.pop(0)
    return [message for pair in pairs for message in pair]


def safe_error(value, key=""):
    message = str(value)
    if key:
        message = message.replace(key, "[已隐藏]")
    message = re.sub(r"(?i)bearer\s+[^\s\"',;]+", "Bearer [已隐藏]", message)
    message = re.sub(r"data:[^,\s]+;base64,[A-Za-z0-9+/=]+", "[附件已隐藏]", message)
    message = re.sub(r"[A-Za-z0-9+/=_-]{96,}", "[长数据已隐藏]", message)
    return message[:1200]


def event_data(response):
    """SSE frames, including comments, CRLF and multi-line data fields.

Some compatible services omit blank separators; accept a complete JSON line
there too. Never discard an incomplete/malformed event as a successful answer.
    """
    pending = []
    for raw in response:
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("服务返回了过大的流式事件。")
        line = raw.decode("utf-8-sig").rstrip("\r\n")
        if not line:
            if pending:
                yield "\n".join(pending)
                pending.clear()
        elif line.startswith("data:"):
            if pending:
                previous = "\n".join(pending)
                try:
                    if previous != "[DONE]":
                        json.loads(previous)
                except ValueError:
                    pass
                else:
                    yield previous
                    pending.clear()
            pending.append(line[5:].lstrip(" "))
            if sum(map(len, pending)) > 2 * 1024 * 1024:
                raise ValueError("服务返回了过大的流式事件。")
    if pending:
        yield "\n".join(pending)


class AIJob(QObject):
    chunk = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    status = Signal(str)
    IO_TIMEOUT = 60
    TOTAL_TIMEOUT = 600
    RESPONSE_LIMIT = 250_000

    def __init__(self, config, messages):
        super().__init__()
        self.config = dict(config)
        self.messages = list(messages)
        self.cancelled = threading.Event()
        self.thread = None
        self.finish_reason = None
        self._response = None
        self._timeout = False
        self._phase = ""
        self._buffer = ""
        self._last_emit = 0

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="PDF Reader-AI")
        self.thread.start()

    def _abort_socket(self):
        # shutdown does not wait on the BufferedReader lock held by readline().
        # close() on the GUI thread could otherwise block until the next chunk.
        try:
            raw = self._response.fp.raw
            raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass

    def cancel(self):
        self.cancelled.set()
        self._abort_socket()

    def _expire(self):
        self._timeout = True
        self._abort_socket()

    def _emit(self, signal, value):
        if not self.cancelled.is_set():
            try:
                signal.emit(value)
            except RuntimeError:
                pass

    def _set_status(self, phase):
        if phase != self._phase:
            self._phase = phase
            self._emit(self.status, phase)

    def _deliver(self, value="", force=False):
        self._buffer += value
        if self._buffer and (force or time.monotonic() - self._last_emit >= .04):
            self._emit(self.chunk, self._buffer)
            self._buffer = ""
            self._last_emit = time.monotonic()

    def _service_error(self, error):
        if isinstance(error, dict):
            error = error.get("message") or error.get("code") or "服务返回错误。"
        return safe_error(error, self.config.get("key", ""))

    def _run(self):
        watchdog = None
        try:
            if self.cancelled.is_set():
                return
            if not self.config.get("enabled"):
                raise ValueError("请先在 AI 设置中启用连接。")
            if not self.config.get("model"):
                raise ValueError("请先填写模型名称。")
            endpoint = validate_endpoint(self.config["endpoint"])
            payload = {"model": self.config["model"], "messages": self.messages, "stream": True}
            # The old 2048-token cap cut answers off, often during reasoning.
            # Use the provider's model default unless the user chose a limit.
            if self.config.get("max_output_tokens"):
                payload["max_tokens"] = int(self.config["max_output_tokens"])
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if urlsplit(endpoint).hostname == "api.deepseek.com" and len(data) >= 48 * 1024 * 1024:
                raise ValueError("请求超过 DeepSeek 的 48 MiB 限制，未发送。请改用全文文字或仅当前页。")
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if self.config.get("key"):
                headers["Authorization"] = "Bearer " + self.config["key"]
            request = Request(endpoint, data=data, headers=headers, method="POST")
            answer = ""
            watchdog = threading.Timer(self.TOTAL_TIMEOUT, self._expire)
            watchdog.daemon = True
            watchdog.start()
            self._set_status("正在连接服务")
            if self.cancelled.is_set():
                return
            with build_opener(NoRedirect()).open(request, timeout=self.IO_TIMEOUT) as response:
                self._response = response
                self._set_status("等待模型回复")
                if self.cancelled.is_set():
                    return
                if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                    body = response.read(2 * 1024 * 1024 + 1)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError("服务响应过大，已停止接收。")
                    result = json.loads(body)
                    if result.get("error"):
                        raise ValueError(self._service_error(result["error"]))
                    choice = result["choices"][0]
                    answer = choice["message"].get("content") or choice["message"].get("refusal") or ""
                    if not isinstance(answer, str):
                        raise ValueError("服务返回了不支持的回答格式。")
                    self.finish_reason = choice.get("finish_reason") or "stop"
                    self._deliver(answer, force=True)
                else:
                    terminated = False
                    for data in event_data(response):
                        if self.cancelled.is_set():
                            return
                        if self._timeout:
                            raise TimeoutError()
                        if data == "[DONE]":
                            terminated = True
                            break
                        event = json.loads(data)
                        if event.get("error"):
                            raise ValueError(self._service_error(event["error"]))
                        choices = event.get("choices", [])
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            self._set_status("模型正在思考")
                        chunk = delta.get("content") or delta.get("refusal") or ""
                        if isinstance(chunk, str) and chunk:
                            answer += chunk
                            self._set_status("正在接收回答")
                            self._deliver(chunk)
                        if len(answer) > self.RESPONSE_LIMIT:
                            raise ValueError("回答超过本机显示上限，已停止接收；已收到的内容已保留。")
                        if choice.get("finish_reason") is not None:
                            self.finish_reason = choice["finish_reason"]
                            terminated = True
                            break
                    if self._timeout:
                        raise TimeoutError()
                    if not terminated:
                        raise ValueError("连接在回答完成前中断，已保留收到的内容。可点击继续或重新发送。")
                self._deliver(force=True)
            if self._timeout:
                raise TimeoutError()
            if not answer.strip():
                if self.finish_reason == "length":
                    raise ValueError("模型在生成正文前用完了输出额度。请把输出上限改为“模型默认”后重试。")
                raise ValueError("服务没有返回正文。请检查模型、输入类型或服务返回的限制。")
            self._emit(self.finished, answer)
        except HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read(16 * 1024).decode("utf-8", errors="replace"))
                detail = self._service_error(body.get("error", body))
            except (ValueError, OSError, AttributeError):
                pass
            parts = [part for message in self.messages if isinstance(message.get("content"), list) for part in message["content"]]
            hint = ""
            if exc.code in (400, 413, 415, 422):
                if any(part.get("type") == "file" for part in parts):
                    hint += " 此服务若不接受 PDF 文件，可在设置改用全文文字；DeepSeek 会自动使用逐页图片和文字。"
                if any(part.get("type") == "image_url" for part in parts):
                    hint += " 请确认当前模型支持图片输入。"
            self._emit(self.failed, f"HTTP {exc.code}" + ("：" + detail if detail else "。请检查服务地址、模型和账户状态。") + hint)
        except (TimeoutError, socket.timeout):
            self._deliver(force=True)
            self._emit(self.failed, "等待 AI 服务超时，已停止接收并保留已有回答。可点击继续或重试。")
        except URLError as exc:
            self._deliver(force=True)
            message = "等待 AI 服务超时。" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "无法连接 AI 服务，请检查地址、网络或本地模型是否正在运行。"
            self._emit(self.failed, message)
        except Exception as exc:
            self._deliver(force=True)
            self._emit(self.failed, self._service_error(exc))
        finally:
            if watchdog:
                watchdog.cancel()
            self._response = None
