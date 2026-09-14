from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Choice, Question
from app.domain.repositories import QuestionRepository
from app.infrastructure.db.models import ChoiceModel, QuestionCategory, QuestionModel

# Fixed display/grading order for the post-session quiz -- diagnosis and
# severity first (single questions), then the three management stages in
# their clinical sequence. Independent of each question's own id/insertion
# order, so seeding order in questions.json doesn't matter.
_QUIZ_CATEGORY_ORDER: dict[QuestionCategory, int] = {
    QuestionCategory.diagnosis: 0,
    QuestionCategory.severity: 1,
    QuestionCategory.management_immediate: 2,
    QuestionCategory.management_monitoring: 3,
    QuestionCategory.management_disposition: 4,
}


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
        category=row.category.value if row.category is not None else None,
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

    async def list_post_session_quiz(self, scenario_id: int) -> list[Question]:
        result = await self._session.exec(
            select(QuestionModel)
            .where(QuestionModel.scenario_id == scenario_id, QuestionModel.category.is_not(None))  # type: ignore[union-attr]
            .order_by(QuestionModel.id)
        )
        rows = list(result.all())
        # Re-sort by the fixed clinical-stage order above; the DB query above
        # only filters and gives a stable id-based order as a tiebreaker.
        rows.sort(key=lambda r: (_QUIZ_CATEGORY_ORDER.get(r.category, len(_QUIZ_CATEGORY_ORDER)), r.id))

        questions: list[Question] = []
        for row in rows:
            assert row.id is not None
            choices = await self._choices_for(row.id)
            questions.append(_question_to_entity(row, choices))
        return questions
