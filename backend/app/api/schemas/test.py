from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TestCategoryRead(BaseModel):
    id: int
    name: str


class TestSummary(BaseModel):
    """The investigation menu -- no `result` here; that only appears once ordered."""

    id: int
    category_id: int
    name: str


class TestOrderCreate(BaseModel):
    test_id: int


class TestOrderResponse(BaseModel):
    ordered_test_id: int
    test_id: int
    name: str
    result: str
    ordered_at: datetime
