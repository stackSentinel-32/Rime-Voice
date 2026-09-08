from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LookupReq(BaseModel):
    booking_id: int
    turn_version: int = 0
    delay_ms: Optional[int] = None


class FindReq(BaseModel):
    customer_name: str = ""
    phone: str = ""
    turn_version: int = 0
    delay_ms: Optional[int] = None


class CreateReq(BaseModel):
    customer_name: str
    date: str
    time: str
    service_type: str = "Haircut"
    phone: str = ""
    turn_version: int = 0
    delay_ms: Optional[int] = None


class UpdateReq(BaseModel):
    booking_id: int
    changes: dict = Field(default_factory=dict)
    turn_version: int = 0
    delay_ms: Optional[int] = None


class CancelReq(BaseModel):
    booking_id: int
    turn_version: int = 0
    delay_ms: Optional[int] = None


class CommitReq(BaseModel):
    proposal: dict
