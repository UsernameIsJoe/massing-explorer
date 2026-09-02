from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.1"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. Is it running? ({e})"
            ) from e

    def list_models(self) -> list[str]:
        url = f"{self.base_url}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
        except urllib.error.URLError as e:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}: {e}") from e

    def ping(self) -> bool:
        try:
            self.list_models()
            return True
        except OllamaError:
            return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        return self._post("/api/chat", payload)
