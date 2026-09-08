"""Receptionist tools: find_bookings by name/phone, create_booking (write-safe),
and the dynamic date/time system prompt."""
from __future__ import annotations

from datetime import datetime

from backend.tools import find_bookings, validate_create
from agent import llm_tools as L


def test_find_bookings_by_name_token(db_sf):
    with db_sf() as s:
        rows = find_bookings(s, customer_name="Priya Sharma")
    assert isinstance(rows, list)
    if rows:
        assert "priya" in rows[0]["customer_name"].lower()


def test_find_bookings_empty_query_returns_rows(db_sf):
    with db_sf() as s:
        rows = find_bookings(s)
    assert len(rows) > 0 and len(rows) <= 8
    assert "phone" in rows[0]          # new field present


def test_validate_create_proposal_has_no_id(db_sf):
    with db_sf() as s:
        proposal = validate_create(s, "Test Person", "2026-09-18", "10:00",
                                   "Haircut", "+91-9000000000")
    assert proposal["id"] is None       # id assigned only at commit
    assert proposal["status"] == "confirmed"


def test_system_prompt_has_current_datetime():
    prompt = L._system_prompt()
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in prompt              # current date injected per call
    assert "Riya" in prompt
    assert "one question at a time" in prompt   # receptionist info-collection
    assert "ONLY handle" in prompt             # scope hardening: booking business only


def test_tools_schema_includes_receptionist_tools():
    names = {t["function"]["name"] for t in L._TOOLS}
    assert {"lookup_booking", "find_bookings", "create_booking",
            "update_booking", "cancel_booking"} == names
    # create requires the info a receptionist must collect
    create = next(t for t in L._TOOLS if t["function"]["name"] == "create_booking")
    assert set(create["function"]["parameters"]["required"]) == {"customer_name", "date", "time"}
