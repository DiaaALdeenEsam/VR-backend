from __future__ import annotations

from pydantic import BaseModel


class ScenarioListItem(BaseModel):
    """Deliberately excludes case_text/gold_standard -- those never leave the server."""

    id: int
    name: str
