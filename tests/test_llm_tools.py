"""LLM decision path (agent/llm_tools.py) — hermetic, no network, no key, no SDK.

We stub the OpenAI-compatible client (Groq): the unit under test is OUR code (schema
wiring, tool-call extraction, fallback behavior), not the SDK. Live key/model
validation is covered by evidence/preflight.py instead.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent.llm_tools as L


class _FakeFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeCall:
    def __init__(self, function):
        self.function = function


class _FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResp:
    def __init__(self, choice):
        self.choices = [choice]


class _FakeCompletions:
    def __init__(self, responses, calls):
        self.responses = responses
        self.calls = calls

    def create(self, model, messages, tools=None, tool_choice=None):
        self.calls.append({"model": model, "messages": messages, "tools": bool(tools)})
        return self.responses.pop(0)


def _client(responses):
    calls = []
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(responses, calls))), calls


def _tool_resp(name, arguments):
    return _FakeResp(_FakeChoice(_FakeMsg(tool_calls=[_FakeCall(_FakeFn(name, arguments))])))


def _text_resp(content):
    return _FakeResp(_FakeChoice(_FakeMsg(content=content)))


def test_decide_tool_extracts_function_call(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    client, calls = _client([_tool_resp("lookup_booking", '{"booking_id": 12}')])
    out = L.decide_tool(client, "check my booking 12")
    assert out == {"tool": "lookup_booking", "args": {"booking_id": 12}}
    assert calls[0]["model"] == "openai/gpt-oss-20b"
    assert calls[0]["tools"] is True


def test_decide_tool_none_when_no_tool_call(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    client, _ = _client([_text_resp("just chatting")])
    assert L.decide_tool(client, "just chatting") is None


def test_decide_tool_defaults_model(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    client, calls = _client([_tool_resp("cancel_booking", '{"booking_id": 7}')])
    L.decide_tool(client, "cancel booking 7")
    assert calls[0]["model"] == "openai/gpt-oss-20b"


def test_conversation_turn_chat_reply(monkeypatch):
    """Non-tool query -> spoken text (persona chit-chat path)."""
    client, calls = _client([_text_resp("I'm Riya! How can I help your bookings today?")])
    out = L.conversation_turn(client, "who are you?", ["hi"])
    assert out == {"text": "I'm Riya! How can I help your bookings today?"}
    # history + transcript both folded into messages
    assert calls[0]["messages"][-1] == {"role": "user", "content": "who are you?"}


def test_conversation_turn_tool_call(monkeypatch):
    client, _ = _client([_tool_resp("update_booking", '{"booking_id": 12, "changes": {"date": "2026-09-18"}}')])
    out = L.conversation_turn(client, "make it friday")
    assert out["tool"] == "update_booking" and out["args"]["booking_id"] == 12


def test_conversation_turn_offline():
    out = L.conversation_turn(None, "hello")
    assert out["text"]


def test_confirmation_turn_text(monkeypatch):
    client, _ = _client([_text_resp("Done! Booking 12 is now on September 18th.")])
    out = L.confirmation_turn(client, "update_booking", {"id": 12, "date": "2026-09-18"})
    assert "September 18th" in out


def test_confirmation_turn_offline():
    out = L.confirmation_turn(None, "lookup_booking", {"id": 12})
    assert "lookup_booking" in out


def test_make_llm_none_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert L.make_llm() is None