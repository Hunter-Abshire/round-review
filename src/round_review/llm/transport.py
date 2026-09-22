"""Ollama HTTP transport. No business logic; just the wire format and error mapping."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from round_review.config import DEFAULT_NUM_CTX
from round_review.errors import OllamaError

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    system: str
    prompt: str
    images_b64: tuple[str, ...]
    format: dict[str, Any] | str | None
    timeout_s: float


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    prompt_eval_count: int
    eval_count: int


class Transport(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse: ...


Opener = Callable[..., Any]


def _default_opener(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)


def build_body(
    request: ChatRequest, num_ctx: int = DEFAULT_NUM_CTX, keep_alive: str = ""
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "stream": False,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.prompt, "images": list(request.images_b64)},
        ],
        # Low temperature: we want consistent, evidence-bound findings, not creativity.
        "options": {"temperature": 0.2, "num_ctx": num_ctx},
    }
    if request.format is not None:
        body["format"] = request.format
        body["think"] = False
    if keep_alive:
        # A review is dozens of calls in a row; reloading the model between them would cost
        # more than the inference.
        body["keep_alive"] = keep_alive
    return body


def _response_content(payload: dict[str, Any], schema: dict[str, Any] | str | None) -> str:
    message = payload["message"]
    content = str(message["content"])
    if (
        content.strip()
        or not isinstance(schema, dict)
        or schema.get("type") != "object"
        or payload.get("done") is not True
        or payload.get("done_reason") != "stop"
    ):
        return content
    # Some Qwen/Ollama versions route the complete schema response into thinking,
    # even with think=false. Never promote reasoning prose or truncated responses.
    thinking = message.get("thinking")
    if not isinstance(thinking, str):
        return content
    try:
        document = json.loads(thinking)
    except json.JSONDecodeError:
        return content  # Preserve the empty answer so the normal parse/retry path handles it.
    if not isinstance(document, dict) or any(
        key not in document for key in schema.get("required", [])
    ):
        return content
    log.warning("Ollama returned structured JSON in message.thinking; using it for validation")
    return thinking


@dataclass(frozen=True, slots=True)
class UrllibTransport:
    base_url: str
    opener: Opener = field(default=_default_opener)
    num_ctx: int = DEFAULT_NUM_CTX
    keep_alive: str = ""

    def chat(self, request: ChatRequest) -> ChatResponse:
        url = self.base_url.rstrip("/") + "/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps(build_body(request, self.num_ctx, self.keep_alive)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(req, timeout=request.timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise OllamaError(f"Ollama HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"cannot reach Ollama at {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OllamaError(
                f"Ollama request timed out after {request.timeout_s:.0f}s; raise "
                "request_timeout_s in config.toml, or lower coach_frames or frame_width "
                "so each call carries less"
            ) from exc
        try:
            payload = json.loads(raw)
            return ChatResponse(
                content=_response_content(payload, request.format),
                prompt_eval_count=int(payload.get("prompt_eval_count", 0)),
                eval_count=int(payload.get("eval_count", 0)),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Ollama response missing message.content: {exc}") from exc
