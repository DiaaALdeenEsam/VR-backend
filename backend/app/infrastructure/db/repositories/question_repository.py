from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Choice, Question
from app.domain.repositories import QuestionRepository
from app.infrastructure.db.models import ChoiceModel, QuestionModel


def _choice_to_entity(row: ChoiceModel) -> Choice:
    assert row.id is not None
    return Choice(id=row.id, question_id=row.question_id, text=row.text)


def _question_to_entity(row: QuestionModel, choices: list[ChoiceModel]) -> Question:
    assert row.id is not None
    return Question(
        id=row.id,
        scenario_id=row.scenario_id,
        text=row.text,
        correct_choice_id=row.correct_choice_id,
        choices=[_choice_to_entity(c) for c in choices],
    )


class SqlQuestionRepository(QuestionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _choices_for(self, question_id: int) -> list[ChoiceModel]:
        result = await self._session.exec(
            select(ChoiceModel).where(ChoiceModel.question_id == question_id).order_by(ChoiceModel.id)
        )
        return list(result.all())

    async def list_by_scenario(self, scenario_id: int) -> list[Question]:
        result = await self._session.exec(
            select(QuestionModel).where(QuestionModel.scenario_id == scenario_id).order_by(QuestionModel.id)
        )
        questions: list[Question] = []
        for row in result.all():
            assert row.id is not None
            choices = await self._choices_for(row.id)
            questions.append(_question_to_entity(row, choices))
        return questions

    async def get(self, question_id: int) -> Question | None:
        row = await self._session.get(QuestionModel, question_id)
        if row is None:
            return None
        choices = await self._choices_for(question_id)
        return _question_to_entity(row, choices)
