"""Listing the models Ollama actually has, so the app can offer them."""

import json
from typing import Any

from round_review.llm.models import list_models


def opener_for(payload: object, fail: Exception | None = None) -> Any:
    def opener(req: Any, timeout: float) -> Any:
        if fail:
            raise fail

        class Response:
            def read(self) -> bytes:
                return json.dumps(payload).encode()

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        return Response()

    return opener


def test_lists_installed_models_newest_first() -> None:
    payload = {
        "models": [
            {"name": "qwen3-vl:4b", "size": 3_000_000_000, "modified_at": "2026-09-01T00:00:00Z"},
            {"name": "qwen3-vl:8b", "size": 6_000_000_000, "modified_at": "2026-09-20T00:00:00Z"},
        ]
    }
    models = list_models("http://x", opener_for(payload))
    assert [m.name for m in models] == ["qwen3-vl:8b", "qwen3-vl:4b"]
    assert models[0].size_bytes == 6_000_000_000


def test_an_unreachable_ollama_is_an_empty_list_not_an_error() -> None:
    assert list_models("http://x", opener_for(None, fail=OSError("refused"))) == []


def test_a_malformed_reply_is_an_empty_list() -> None:
    assert list_models("http://x", opener_for({"unexpected": True})) == []
    assert list_models("http://x", opener_for({"models": "nope"})) == []


def test_entries_without_a_name_are_skipped() -> None:
    payload = {"models": [{"size": 1}, {"name": "good:1b", "size": 2}]}
    assert [m.name for m in list_models("http://x", opener_for(payload))] == ["good:1b"]
