from __future__ import annotations

from pydantic import BaseModel


class ChoiceRead(BaseModel):
    id: int
    text: str


class QuestionRead(BaseModel):
    """Deliberately excludes correct_choice_id -- that's the answer key."""

    id: int
    text: str
    choices: list[ChoiceRead]
