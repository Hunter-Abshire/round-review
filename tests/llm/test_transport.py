import json
import urllib.error
from io import BytesIO
from typing import Any

import pytest

from round_review.errors import OllamaError
from round_review.llm.transport import ChatRequest, ChatResponse, UrllibTransport


def make_request(**overrides: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "model": "qwen3-vl:8b",
        "system": "be a coach",
        "prompt": "review these",
        "images_b64": ("aGVsbG8=", "d29ybGQ="),
        "format": {"type": "object"},
        "timeout_s": 42.0,
    }
    return ChatRequest(**{**base, **overrides})


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = BytesIO(json.dumps(payload).encode())

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def capture_opener(payload: dict[str, Any]) -> tuple[list[Any], Any]:
    seen: list[Any] = []

    def opener(req: Any, timeout: float) -> FakeResponse:
        seen.append((req, timeout))
        return FakeResponse(payload)

    return seen, opener


OK_PAYLOAD = {
    "message": {"role": "assistant", "content": '{"findings": []}'},
    "prompt_eval_count": 1500,
    "eval_count": 120,
}


def test_posts_expected_body_to_api_chat() -> None:
    seen, opener = capture_opener(OK_PAYLOAD)
    transport = UrllibTransport(base_url="http://ollama:11434/", opener=opener)
    resp = transport.chat(make_request())

    req, timeout = seen[0]
    assert req.full_url == "http://ollama:11434/api/chat"
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert timeout == 42.0
    body = json.loads(req.data)
    assert body["model"] == "qwen3-vl:8b"
    assert body["stream"] is False
    assert body["format"] == {"type": "object"}
    assert body["options"] == {"temperature": 0.2, "num_ctx": 24576}
    assert body["think"] is False
    assert body["messages"][0] == {"role": "system", "content": "be a coach"}
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "review these"
    assert body["messages"][1]["images"] == ["aGVsbG8=", "d29ybGQ="]
    assert resp == ChatResponse(content='{"findings": []}', prompt_eval_count=1500, eval_count=120)


def test_format_omitted_when_none() -> None:
    seen, opener = capture_opener(OK_PAYLOAD)
    UrllibTransport("http://x", opener=opener).chat(make_request(format=None))
    assert "format" not in json.loads(seen[0][0].data)


def test_custom_context_size_sent_to_ollama() -> None:
    seen, opener = capture_opener(OK_PAYLOAD)
    UrllibTransport("http://x", opener=opener, num_ctx=32768).chat(make_request())
    assert json.loads(seen[0][0].data)["options"]["num_ctx"] == 32768


def test_complete_structured_json_in_thinking_used_when_content_empty(caplog) -> None:
    payload = {
        "message": {"content": "", "thinking": '{"findings": []}'},
        "done": True,
        "done_reason": "stop",
    }
    _, opener = capture_opener(payload)
    response = UrllibTransport("http://x", opener=opener).chat(
        make_request(format={"type": "object", "required": ["findings"]})
    )
    assert response.content == '{"findings": []}'
    assert "message.thinking" in caplog.text


@pytest.mark.parametrize(
    ("thinking", "done", "reason", "schema"),
    [
        ('Reasoning first. {"findings": []}', True, "stop", {"type": "object"}),
        ('{"findings":', True, "stop", {"type": "object"}),
        ('{"findings": []}', True, "length", {"type": "object"}),
        ('{"findings": []}', False, "stop", {"type": "object"}),
        ('{"findings": []}', True, "stop", None),
        ('{"analysis": []}', True, "stop", {"type": "object", "required": ["findings"]}),
        ("[]", True, "stop", {"type": "object"}),
    ],
)
def test_unusable_thinking_does_not_replace_content(thinking, done, reason, schema) -> None:
    _, opener = capture_opener(
        {"message": {"content": "", "thinking": thinking}, "done": done, "done_reason": reason}
    )
    response = UrllibTransport("http://x", opener=opener).chat(make_request(format=schema))
    assert response.content == ""


def test_content_takes_precedence_over_thinking() -> None:
    _, opener = capture_opener(
        {
            "message": {"content": '{"findings": []}', "thinking": '{"findings": [1]}'},
            "done": True,
            "done_reason": "stop",
        }
    )
    response = UrllibTransport("http://x", opener=opener).chat(make_request())
    assert response.content == '{"findings": []}'


def test_http_error_is_ollama_error() -> None:
    def opener(req: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, BytesIO(b"model not found"))  # type: ignore[arg-type]

    with pytest.raises(OllamaError, match="500"):
        UrllibTransport("http://x", opener=opener).chat(make_request())


def test_connection_refused_is_ollama_error() -> None:
    def opener(req: Any, timeout: float) -> Any:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(OllamaError, match="refused"):
        UrllibTransport("http://x", opener=opener).chat(make_request())


def test_timeout_is_ollama_error() -> None:
    def opener(req: Any, timeout: float) -> Any:
        raise TimeoutError()

    with pytest.raises(OllamaError, match="timed out"):
        UrllibTransport("http://x", opener=opener).chat(make_request())


def test_malformed_response_is_ollama_error() -> None:
    _, opener = capture_opener({"unexpected": True})
    with pytest.raises(OllamaError, match=r"message\.content"):
        UrllibTransport("http://x", opener=opener).chat(make_request())


def test_keeps_the_model_resident_between_calls() -> None:
    seen, opener = capture_opener(OK_PAYLOAD)
    UrllibTransport("http://x", opener=opener, keep_alive="30m").chat(make_request())
    assert json.loads(seen[0][0].data)["keep_alive"] == "30m"


def test_keep_alive_is_omitted_when_unset() -> None:
    seen, opener = capture_opener(OK_PAYLOAD)
    UrllibTransport("http://x", opener=opener, keep_alive="").chat(make_request())
    assert "keep_alive" not in json.loads(seen[0][0].data)


def test_a_timeout_says_which_setting_controls_it() -> None:
    def opener(req: Any, timeout: float) -> Any:
        raise TimeoutError()

    with pytest.raises(OllamaError, match="request_timeout_s"):
        UrllibTransport("http://x", opener=opener).chat(make_request())
