"""LLM for the deep sleep (L-4): the answer comes strictly through a tool with a JSON schema. An invalid answer ->
LLMError -> a stage error (PARTIAL), never an "empty proposal".

The route is a prefix in the model name (MEM_DEEP_MODEL_*):
- no prefix or "anthropic:": Anthropic Messages API (a name without a prefix must be claude...);
- "openai:": a model inside your perimeter over an OpenAI-compatible protocol (your own router or accelerator):
  address MEM_OPENAI_BASE_URL, key MEM_OPENAI_API_KEY, and only models listed in MEM_OPENAI_IN_CONTOUR_MODELS;
- cloud routes ("yandex:", "gigachat:", "openai:" outside the list) are not supported for sleep: memory records would
  go to a cloud without masking, and mem has no label dictionary. Such a setting is an error; the sleep marks the stages PARTIAL.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Protocol

import httpx


class LLMError(Exception):
    pass


class LLM(Protocol):
    def call(self, model: str, system: str, user: str, tool: dict) -> dict: ...


def _validate(obj, schema: dict, path: str = "$") -> None:
    """Minimal JSON schema check: type, required, properties, items, enum."""
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if t == "null" or (isinstance(t, list) and obj is None and "null" in t):
        return
    tt = t[0] if isinstance(t, list) else t
    if tt and not isinstance(obj, types[tt]):
        raise LLMError(f"{path}: expected {tt}")
    if "enum" in schema and obj not in schema["enum"]:
        raise LLMError(f"{path}: value outside enum")
    if tt == "object":
        for k in schema.get("required", []):
            if k not in obj:
                raise LLMError(f"{path}: missing field {k}")
        for k, sub in schema.get("properties", {}).items():
            if k in obj and obj[k] is not None:
                _validate(obj[k], sub, f"{path}.{k}")
    if tt == "array":
        for i, it in enumerate(obj):
            _validate(it, schema.get("items", {}), f"{path}[{i}]")


class AnthropicLLM:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None, timeout: float = 90.0):
        self.key = api_key or os.environ.get("MEM_ANTHROPIC_API_KEY")
        if not self.key:
            raise LLMError("MEM_ANTHROPIC_API_KEY is not set")
        self.timeout = timeout

    def call(self, model: str, system: str, user: str, tool: dict) -> dict:
        body = {"model": model, "max_tokens": 2000, "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [tool], "tool_choice": {"type": "tool", "name": tool["name"]}}
        last = None
        for attempt in range(3):
            try:
                r = httpx.post(self.URL, json=body, timeout=self.timeout, headers={
                    "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
                if r.status_code in (429, 500, 502, 503, 529):
                    last = f"HTTP {r.status_code}"
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    raise LLMError(f"Anthropic API HTTP {r.status_code}")
                blocks = [b for b in r.json().get("content", []) if b.get("type") == "tool_use"]
                if not blocks:
                    raise LLMError("answer without a tool call")
                out = blocks[0]["input"]
                _validate(out, tool["input_schema"])
                return out
            except httpx.HTTPError as e:
                last = type(e).__name__
                time.sleep(2 * (attempt + 1))
        raise LLMError(f"Anthropic API unavailable: {last}")


# Cloud provider hosts: an "in-perimeter" router cannot point at them, whatever model name is on the list.
CLOUD_HOSTS = ("api.cloud.yandex.net", "yandex.net", "yandex.ru", "yandexcloud.net", "sberbank.ru", "sber.ru",
               "api.openai.com", "openai.azure.com", "api.anthropic.com", "openrouter.ai", "api.deepseek.com",
               "api.mistral.ai", "generativelanguage.googleapis.com", "api.groq.com", "api.together.xyz")


def _plain_schema(schema):
    """"type: [x, null]" -> "x": some OpenAI-compatible servers do not accept a list of types."""
    if isinstance(schema, dict):
        out = {k: _plain_schema(v) for k, v in schema.items()}
        t = out.get("type")
        if isinstance(t, list):
            rest = [x for x in t if x != "null"]
            out["type"] = rest[0] if rest else "string"
        return out
    if isinstance(schema, list):
        return [_plain_schema(x) for x in schema]
    return schema


class OpenAICompatLLM:
    """A model inside the perimeter over an OpenAI-compatible protocol (chat/completions with a tool call)."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0):
        m = re.fullmatch(r"(https?)://([A-Za-z0-9.\-]+)(:\d{1,5})?(/[A-Za-z0-9._~\-/]*)?", (base_url or "").strip())
        if not m:
            raise LLMError("MEM_OPENAI_BASE_URL: expected http(s)://host[:port]/v1 (no login or query)")
        host = m.group(2).lower().rstrip(".")
        if any(host == h or host.endswith("." + h) for h in CLOUD_HOSTS):
            raise LLMError(f"MEM_OPENAI_BASE_URL points at a cloud provider ({host}); that is not a model inside "
                           "your perimeter, and mem has no masking")
        self.url = base_url.strip().rstrip("/") + "/chat/completions"
        self.key, self.timeout = api_key, timeout

    def call(self, model: str, system: str, user: str, tool: dict) -> dict:
        body = {"model": model, "max_tokens": 2000, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "tools": [{"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""),
                                                             "parameters": _plain_schema(tool["input_schema"])}}],
                "tool_choice": {"type": "function", "function": {"name": tool["name"]}}}
        headers = {"content-type": "application/json"} | ({"authorization": f"Bearer {self.key}"} if self.key else {})
        last = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 * attempt)
            try:
                # trust_env=False: proxy variables from the environment (for reaching Anthropic) do not apply to the router
                r = httpx.post(self.url, json=body, timeout=self.timeout, headers=headers, trust_env=False)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                    continue
                if r.status_code != 200:
                    raise LLMError(f"model router: HTTP {r.status_code}")      # the response body is never put into the error
                msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
                calls = [c for c in msg.get("tool_calls") or [] if (c.get("function") or {}).get("name") == tool["name"]]
                if not calls:
                    raise LLMError("answer without a tool call")
                args = calls[0]["function"].get("arguments")
                out = json.loads(args) if isinstance(args, str) else args
                if not isinstance(out, dict):
                    raise LLMError("tool arguments are not an object")
                _validate(out, tool["input_schema"])
                return out
            except LLMError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                last = type(e).__name__
            except Exception as e:  # noqa: BLE001 — address, answer, anything: only the type goes into the error text
                raise LLMError(f"model router: failure ({type(e).__name__})") from None
        raise LLMError(f"model router unavailable: {last}")


CLOUD = ("yandex", "gigachat")


class RoutedLLM:
    """Route by the prefix in the model name. Clients are created on first use."""

    def __init__(self):
        self._made: dict[str, LLM] = {}

    @staticmethod
    def split(model: str) -> tuple[str, str]:
        m = (model or "").strip()
        kind, sep, name = m.partition(":")
        kind, name = kind.strip().lower(), name.strip()
        if not sep:
            if not m.lower().startswith("claude"):
                raise LLMError(f"model \"{m[:40]}\" has no route prefix: without a prefix only Anthropic models are allowed")
            return "anthropic", m
        if kind == "anthropic":
            if not name.lower().startswith("claude"):
                raise LLMError("anthropic: only claude... models")
            return kind, name
        if kind in CLOUD:
            raise LLMError(f"route {kind}: a cloud model is not supported for memory sleep, records would go out without "
                           "masking; use a model inside your perimeter (openai: from MEM_OPENAI_IN_CONTOUR_MODELS) or Anthropic")
        if kind == "openai":
            allowed = {x.strip().lower() for x in (os.environ.get("MEM_OPENAI_IN_CONTOUR_MODELS") or "").split(",")
                       if x.strip()}
            if not name or name.lower() not in allowed:
                raise LLMError(f"openai:{name[:40]} is not in MEM_OPENAI_IN_CONTOUR_MODELS; a model outside the perimeter "
                               "is not supported for memory sleep")
            return kind, name
        raise LLMError(f"unknown route prefix \"{kind[:20]}\"")

    def provider(self, kind: str) -> LLM:
        if kind not in self._made:
            self._made[kind] = AnthropicLLM() if kind == "anthropic" else OpenAICompatLLM(
                os.environ.get("MEM_OPENAI_BASE_URL", ""), os.environ.get("MEM_OPENAI_API_KEY", ""))
        return self._made[kind]

    def check(self, *models: str) -> None:
        """All names and keys are checked before the first call: a setup error shows at once, not at the third sleep stage."""
        for m in models:
            self.provider(self.split(m)[0])

    def call(self, model: str, system: str, user: str, tool: dict) -> dict:
        kind, name = self.split(model)
        return self.provider(kind).call(name, system, user, tool)


class FakeLLM:
    """For tests: answers by tool name; records what was sent (S-6)."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.sent: list[dict] = []

    def call(self, model: str, system: str, user: str, tool: dict) -> dict:
        self.sent.append({"model": model, "tool": tool["name"], "user": user})
        r = self.responses.get(tool["name"])
        out = r(user) if callable(r) else r
        if out is None:
            out = {"pairs": []} if tool["name"] == "report_contradictions" else \
                  {"groups": []} if tool["name"] == "suggest_slots" else {"summary": ""}
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except json.JSONDecodeError as e:
                raise LLMError("non-JSON answer") from e
        _validate(out, tool["input_schema"])
        return out
