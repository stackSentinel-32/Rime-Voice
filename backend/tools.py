"""Booking tool logic as pure functions over a SQLAlchemy session.

Design note (write-safety): mutating tools are split into *validate* (read + compute
the proposed row, NO persistence) and *commit* (persist). The orchestrator only calls
`commit_booking` for a NON-STALE result, so a cancelled/stale tool call can never write
to Postgres. This is what makes the "never applied to state" half of the claim true for
writes, not just reads.
"""
from __future__ import annotations

from .models import Booking


class BookingNotFound(Exception):
    pass


def booking_to_dict(b: Booking) -> dict:
    return {
        "id": b.id,
        "customer_name": b.customer_name,
        "date": b.date,
        "time": b.time,
        "service_type": b.service_type,
        "status": b.status,
    }


def _get(session, booking_id) -> Booking:
    b = session.get(Booking, int(booking_id))
    if b is None:
        raise BookingNotFound(f"booking {booking_id} not found")
    return b


# ---- reads ----------------------------------------------------------------
def lookup_booking(session, booking_id) -> dict:
    return booking_to_dict(_get(session, booking_id))


# ---- validate (no persistence) --------------------------------------------
_EDITABLE = {"date", "time", "service_type", "status", "customer_name"}


def validate_update(session, booking_id, changes: dict) -> dict:
    proposal = booking_to_dict(_get(session, booking_id))
    for k, v in (changes or {}).items():
        if k in _EDITABLE:
            proposal[k] = v
    proposal.setdefault("status", "confirmed")
    return proposal


def validate_cancel(session, booking_id) -> dict:
    proposal = booking_to_dict(_get(session, booking_id))
    proposal["status"] = "cancelled"
    return proposal


# ---- commit (persistence; only reached on a non-stale apply) --------------
def commit_booking(session, proposal: dict) -> dict:
    b = session.get(Booking, int(proposal["id"]))
    if b is None:
        raise BookingNotFound(f"booking {proposal['id']} not found")
    for k in ("customer_name", "date", "time", "service_type", "status"):
        if proposal.get(k) is not None:
            setattr(b, k, proposal[k])
    session.commit()
    return booking_to_dict(b)
