"""Tool-calling LLM loop. Deliberately simple — per roadmap it is NOT the differentiator.

Provider-agnostic OpenAI-compatible client (OpenAI / Groq / DashScope / local). The tool
schema is fixed to the three booking tools; the LLM only chooses intent + arguments, and
the TurnManager owns dispatch/versioning/staleness. Model id comes from env (LLM_MODEL)
and must be confirmed live before use.
"""
from __future__ import annotations

import json
import os

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "lookup_booking",
            "description": "Look up a booking by id.",
            "parameters": {
                "type": "object",
                "properties": {"booking_id": {"type": "integer"}},
                "required": ["booking_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_booking",
            "description": "Change fields on a booking (date, time, service_type, status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "integer"},
                    "changes": {"type": "object"},
                },
                "required": ["booking_id", "changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Cancel a booking by id.",
            "parameters": {
                "type": "object",
                "properties": {"booking_id": {"type": "integer"}},
                "required": ["booking_id"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a concise voice booking assistant. Use the provided tools to look up, "
    "update, or cancel bookings. Confirm the outcome in one short spoken sentence."
)


def make_llm():
    """Return an OpenAI-compatible client, or None if not configured (offline demo)."""
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(base_url=base_url, api_key=api_key)


def decide_tool(client, transcript: str) -> dict | None:
    """Single tool-calling step: transcript -> {tool, args} or None (no tool)."""
    if client is None:
        return None
    resp = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": transcript}],
        tools=TOOL_SCHEMA,
        tool_choice="auto",
    )
    msg = resp.choices[0].message
    if not msg.tool_calls:
        return None
    call = msg.tool_calls[0]
    return {"tool": call.function.name, "args": json.loads(call.function.arguments)}
