"""Ollama HTTP transport. No business logic; just the wire format and error mapping."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from round_review.errors import OllamaError


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


def build_body(request: ChatRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "stream": False,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.prompt, "images": list(request.images_b64)},
        ],
        # Low temperature: we want consistent, evidence-bound findings, not creativity.
        "options": {"temperature": 0.2},
    }
    if request.format is not None:
        body["format"] = request.format
    return body


@dataclass(frozen=True, slots=True)
class UrllibTransport:
    base_url: str
    opener: Opener = field(default=_default_opener)

    def chat(self, request: ChatRequest) -> ChatResponse:
        url = self.base_url.rstrip("/") + "/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps(build_body(request)).encode("utf-8"),
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
            raise OllamaError(f"Ollama request timed out after {request.timeout_s}s") from exc
        try:
            payload = json.loads(raw)
            return ChatResponse(
                content=str(payload["message"]["content"]),
                prompt_eval_count=int(payload.get("prompt_eval_count", 0)),
                eval_count=int(payload.get("eval_count", 0)),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OllamaError(f"Ollama response missing message.content: {exc}") from exc
