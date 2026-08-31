from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_list_questions_use_case
from app.api.schemas.question import ChoiceRead, QuestionRead
from app.application.use_cases.list_questions import ListQuestionsUseCase

router = APIRouter(tags=["questions"])


@router.get("/scenarios/{scenario_id}/questions", response_model=list[QuestionRead])
async def list_questions(
    scenario_id: int,
    use_case: ListQuestionsUseCase = Depends(get_list_questions_use_case),
) -> list[QuestionRead]:
    questions = await use_case.execute(scenario_id)
    return [
        QuestionRead(
            id=q.id,
            text=q.text,
            choices=[ChoiceRead(id=c.id, text=c.text) for c in q.choices],
        )
        for q in questions
    ]
