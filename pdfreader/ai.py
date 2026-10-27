"""The only HTTP client in the application. Invoked only by the Send action."""
import json
import threading
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


class AIJob(QObject):
    chunk = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, config, messages):
        super().__init__()
        self.config = dict(config)
        self.messages = list(messages)
        self.cancelled = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="PDF Reader-AI")
        self.thread.start()

    def cancel(self):
        self.cancelled.set()

    def _emit(self, signal, value):
        if not self.cancelled.is_set():
            try:
                signal.emit(value)
            except RuntimeError:
                pass

    def _run(self):
        try:
            if self.cancelled.is_set():
                return
            if not self.config.get("enabled"):
                raise ValueError("请先在 AI 设置中启用连接。")
            if not self.config.get("model"):
                raise ValueError("请先填写模型名称。")
            endpoint = validate_endpoint(self.config["endpoint"])
            payload = {"model": self.config["model"], "messages": self.messages,
                       "stream": True, "max_tokens": 2048}
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if self.config.get("key"):
                headers["Authorization"] = "Bearer " + self.config["key"]
            request = Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
            answer = ""
            with build_opener(NoRedirect()).open(request, timeout=45) as response:
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    body = response.read(2 * 1024 * 1024)
                    result = json.loads(body)
                    answer = result["choices"][0]["message"]["content"] or ""
                    self._emit(self.chunk, answer)
                else:
                    for raw in response:
                        if self.cancelled.is_set():
                            return
                        line = raw.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        content = line[5:].strip()
                        if content == "[DONE]":
                            break
                        event = json.loads(content)
                        if event.get("error"):
                            raise ValueError("服务返回错误，请检查模型名称和账户状态。")
                        choices = event.get("choices", [])
                        if choices:
                            chunk = choices[0].get("delta", {}).get("content") or ""
                            if isinstance(chunk, str):
                                answer += chunk
                                self._emit(self.chunk, chunk)
                        if len(answer) > 250_000:
                            raise ValueError("响应过长，已停止接收。")
            if not answer.strip():
                raise ValueError("服务没有返回文字，请检查模型是否支持 Chat Completions。")
            self._emit(self.finished, answer)
        except HTTPError as exc:
            # Do not echo response bodies or request headers containing secrets.
            parts = [part for message in self.messages if isinstance(message.get("content"), list) for part in message["content"]]
            hint = ""
            if exc.code in (400, 413, 415, 422):
                if any(part.get("type") == "file" for part in parts):
                    hint += " 当前服务可能不支持 PDF 文件输入或超过文档限制；可在 AI 设置中改用全文文字。"
                if any(part.get("type") == "image_url" for part in parts):
                    hint += " 页面截图需要支持图片输入的模型。"
                hint += " 内容超限时，可通过 ＋ 仅发送选区或当前页。"
            self._emit(self.failed, f"AI 服务返回 HTTP {exc.code}。请检查地址、密钥、额度和模型名称。" + hint)
        except URLError:
            self._emit(self.failed, "无法连接 AI 服务，请检查地址、网络或本地模型是否正在运行。")
        except Exception as exc:
            message = str(exc)
            key = self.config.get("key", "")
            if key:
                message = message.replace(key, "[已隐藏]")
            self._emit(self.failed, message)
