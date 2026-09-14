from __future__ import annotations

from pydantic import BaseModel, Field


class ChoiceRead(BaseModel):
    id: int
    text: str


class QuestionRead(BaseModel):
    """Deliberately excludes correct_choice_id -- that's the answer key."""

    id: int
    text: str
    choices: list[ChoiceRead]


class QuizQuestionRead(QuestionRead):
    """A QuestionRead plus which post-session-quiz stage it belongs to --
    'diagnosis'/'severity'/'management_immediate'/'management_monitoring'/
    'management_disposition' (see QuestionCategory, migration 0007). Never
    None here -- GET /sessions/{id}/quiz only ever returns categorized
    questions (see GetPostSessionQuizUseCase), unlike plain QuestionRead
    above, which is also used for GET /scenarios/{id}/questions's full,
    uncategorized list."""

    category: str


class PostSessionQuizResponse(BaseModel):
    """Response for GET /sessions/{id}/quiz. `questions` is ordered
    diagnosis -> severity -> management_immediate -> management_monitoring ->
    management_disposition (see GetPostSessionQuizUseCase /
    SqlQuestionRepository.list_post_session_quiz), so a client can render it
    top-to-bottom with no client-side sorting."""

    session_id: str
    scenario_id: int
    questions: list[QuizQuestionRead] = Field(default_factory=list)
