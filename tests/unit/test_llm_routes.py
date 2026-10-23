"""Model routes for memory sleep: a prefix in the model name, a model inside the perimeter, refusal of clouds."""
import json

import httpx
import pytest

from mem import llm as llmmod
from mem.llm import LLMError, OpenAICompatLLM, RoutedLLM

TOOL = {"name": "write_summary", "description": "Summary.",
        "input_schema": {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}}


def test_split_routes(monkeypatch):
    """Sleep route: a name without a prefix is Anthropic only; cloud routes are refused (records would go out without
    masking); "openai:" only for models on the in-perimeter list; an unknown prefix is an error."""
    monkeypatch.setenv("MEM_OPENAI_IN_CONTOUR_MODELS", "T-Pro-2.1, qwen3-32b")
    r = RoutedLLM()
    assert r.split("claude-sonnet-5") == ("anthropic", "claude-sonnet-5")
    assert r.split("Anthropic:claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")
    assert r.split("openai:t-pro-2.1") == ("openai", "t-pro-2.1") == r.split(" OpenAI : t-pro-2.1")   # any case
    for bad, why in (("yandexgpt-5.1/latest", "no route prefix"), ("yandex:yandexgpt-5.1/latest", "not supported"),
                     ("gigachat:GigaChat-2-Max", "not supported"), ("openai:gpt-5", "MEM_OPENAI_IN_CONTOUR_MODELS"),
                     ("anthropic:gpt-4", "claude"), ("ollama:qwen", "unknown route prefix"),
                     ("openai:", "MEM_OPENAI_IN_CONTOUR_MODELS")):
        with pytest.raises(LLMError, match=why):
            r.split(bad)


def test_check_finds_bad_settings_before_first_call(monkeypatch):
    """A sleep setup error shows before the first call: no Anthropic key, no router address, a cloud model."""
    monkeypatch.delenv("MEM_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MEM_OPENAI_IN_CONTOUR_MODELS", "t-pro-2.1")
    monkeypatch.delenv("MEM_OPENAI_BASE_URL", raising=False)
    with pytest.raises(LLMError, match="MEM_ANTHROPIC_API_KEY"):
        RoutedLLM().check("claude-sonnet-5")
    with pytest.raises(LLMError, match="MEM_OPENAI_BASE_URL"):
        RoutedLLM().check("openai:t-pro-2.1")
    monkeypatch.setenv("MEM_OPENAI_BASE_URL", "http://router:8000/v1")
    RoutedLLM().check("openai:t-pro-2.1")
    with pytest.raises(LLMError, match="not supported"):
        RoutedLLM().check("openai:t-pro-2.1", "yandex:yandexgpt-5.1/latest")


def test_openai_compat_call_and_errors(monkeypatch):
    """A model inside the perimeter: a request with a tool call, the answer is checked against the schema; the server's response
    body never gets into the error text; network failures and 503 are retried, then it is an error; an invalid answer is a model error."""
    monkeypatch.setattr(llmmod.time, "sleep", lambda s: None)
    sent, answers = [], []

    def post(url, json=None, timeout=None, headers=None, trust_env=True):
        sent.append((url, json, headers, trust_env))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return httpx.Response(a[0], content=a[1].encode(), request=httpx.Request("POST", url))
    monkeypatch.setattr(llmmod.httpx, "post", post)
    ok = {"choices": [{"message": {"tool_calls": [{"function": {"name": "write_summary",
                                                                "arguments": json.dumps({"summary": "result"})}}]}}]}
    c = OpenAICompatLLM("http://router:8000/v1/", "k")
    answers += [(503, "busy"), httpx.ConnectError("x"), (200, json.dumps(ok))]
    assert c.call("t-pro-2.1", "s", "u", TOOL) == {"summary": "result"}
    url, body, headers, trust_env = sent[-1]
    assert url == "http://router:8000/v1/chat/completions" and headers["authorization"] == "Bearer k"
    assert trust_env is False                                    # environment proxies do not apply to the in-perimeter router
    assert body["tool_choice"]["function"]["name"] == "write_summary" and body["messages"][1]["content"] == "u"
    answers += [(401, "secret: key sk-123 is invalid")]
    with pytest.raises(LLMError) as e:
        c.call("m", "s", "u", TOOL)
    assert "sk-123" not in str(e.value) and "401" in str(e.value)
    answers += [(200, json.dumps({"choices": [{"message": {"content": "as text"}}]}))]
    with pytest.raises(LLMError, match="without a tool call"):
        c.call("m", "s", "u", TOOL)
    answers += [(200, "not json")]
    with pytest.raises(LLMError, match="failure \\(JSONDecodeError\\)"):
        c.call("m", "s", "u", TOOL)
    answers += [(200, json.dumps({"choices": [{"message": {"tool_calls": [
        {"function": {"name": "write_summary", "arguments": {"summary": 5}}}]}}]}))]
    with pytest.raises(LLMError, match="expected string"):                 # answer does not match the schema
        c.call("m", "s", "u", TOOL)
    answers += [httpx.InvalidURL("http://user:pass@r bad")]
    with pytest.raises(LLMError) as e:
        c.call("m", "s", "u", TOOL)
    assert "pass" not in str(e.value) and "InvalidURL" in str(e.value)
    answers += [(200, json.dumps({"choices": [{"message": {"tool_calls": [
        {"function": {"name": "write_summary", "arguments": {"summary": "as object"}}}]}}]}))]
    assert OpenAICompatLLM("http://router:8000/v1").call("m", "s", "u", TOOL) == {"summary": "as object"}
    assert "authorization" not in sent[-1][2]                               # no key, no header
    answers += [(503, "")] * 3
    with pytest.raises(LLMError, match="unavailable: HTTP 503"):
        c.call("m", "s", "u", TOOL)
    for bad in ("router:8000", "http://user:pass@router/v1", "http://router/v1?x=1", "http://ro uter/v1"):
        with pytest.raises(LLMError, match="MEM_OPENAI_BASE_URL"):
            OpenAICompatLLM(bad)


def test_router_address_cannot_point_to_cloud_and_schema_is_plain():
    """The "in-perimeter" router address cannot be a cloud provider's address (Yandex, Sber, OpenAI, Anthropic and
    others), whatever model name is on the list; the tool schema for the router has no type lists."""
    from mem.llm import _plain_schema
    for url in ("https://llm.api.cloud.yandex.net/v1", "https://api.openai.com/v1", "https://gigachat.devices.sberbank.ru/api/v1",
                "https://API.Anthropic.com/v1", "https://openrouter.ai/api/v1"):
        with pytest.raises(LLMError, match="points at a cloud provider"):
            OpenAICompatLLM(url)
    OpenAICompatLLM("http://10.0.0.5:8000/v1")
    OpenAICompatLLM("https://llm.internal.example.ru/v1")
    assert _plain_schema({"type": "object", "properties": {"a": {"type": ["string", "null"]}}}) == \
        {"type": "object", "properties": {"a": {"type": "string"}}}


def test_routed_call_goes_to_its_provider(monkeypatch):
    """The call goes to the client of its route with the model name without the prefix."""
    monkeypatch.setenv("MEM_OPENAI_IN_CONTOUR_MODELS", "t-pro-2.1")
    got = []

    class P:
        def call(self, model, system, user, tool):
            got.append(model)
            return {"summary": "ok"}
    r = RoutedLLM()
    r._made["openai"] = P()
    assert r.call("openai:t-pro-2.1", "s", "u", TOOL) == {"summary": "ok"} and got == ["t-pro-2.1"]
