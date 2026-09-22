"""Which models Ollama has pulled, so the app can offer them rather than ask you to type."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from round_review.llm.transport import Opener, _default_opener


@dataclass(frozen=True, slots=True)
class InstalledModel:
    name: str
    size_bytes: int
    modified_at: str


def list_models(base_url: str, opener: Opener = _default_opener) -> list[InstalledModel]:
    """Installed models, newest first. An unreachable Ollama is an empty list, never an
    error: not being able to offer a menu must not stop the settings screen opening."""
    url = base_url.rstrip("/") + "/api/tags"
    try:
        request = urllib.request.Request(url, method="GET")
        with opener(request, timeout=5.0) as response:
            payload: Any = json.loads(response.read())
    except Exception:  # any failure here just means no menu
        return []
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    models = [
        InstalledModel(
            name=str(entry["name"]),
            size_bytes=int(entry.get("size", 0)),
            modified_at=str(entry.get("modified_at", "")),
        )
        for entry in entries
        if isinstance(entry, dict) and entry.get("name")
    ]
    return sorted(models, key=lambda m: m.modified_at, reverse=True)
