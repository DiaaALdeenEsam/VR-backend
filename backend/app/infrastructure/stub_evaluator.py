"""Stub implementation of the EvaluationGenerator port.

Returns realistic-shaped OSCE evaluation data derived from simple,
deterministic rules (message counts) rather than any real clinical
reasoning -- there's no ML dependency here at all. This proves the
evaluation flow end to end without wiring a real model.

Chat-only assessment model (2026-09-14): this used to also score
investigation-ordering and quiz performance (see git history for the
removed TEST_ORDERING_WEIGHT/QUIZ_WEIGHT/QUIZ_PASS_THRESHOLD constants and
their criteria) -- that scoring was removed along with the corresponding
EvaluationGenerator.evaluate() parameters (see app/domain/repositories.py).
Only conversation-quality criteria remain below.

STILL A PLACEHOLDER: the pass/fail cutoffs below (see
MIN_HISTORY_QUESTIONS_FOR_PASS) are fixed, rule-based arithmetic picked
purely to get the EvaluationGenerator port signature and API contract
shape-correct ahead of a real evaluator. None of these numbers are derived
from pedagogical research or a real OSCE rubric -- they're arbitrary and
trivially adjustable constants. Swap this whole module out for a real/local
LLM-backed implementation later (see Settings.evaluation_backend in
app/config.py) without touching EvaluateSessionUseCase, the port signature,
or anything above it -- the replacement only needs to honor the same
evaluate() shape.
"""

from __future__ import annotations

from app.domain.entities import EvaluationCriterion, SessionEvaluation
from app.domain.repositories import EvaluationGenerator

MIN_HISTORY_QUESTIONS_FOR_PASS = 2


class StubEvaluationGenerator(EvaluationGenerator):
    async def evaluate(
        self,
        case_text: str,
        gold_standard: str,
        messages: list[dict[str, str]],
    ) -> SessionEvaluation:
        doctor_messages = [m for m in messages if m.get("role") == "user"]

        # "Diagnostic reasoning" has always been fixed at passed=True (there
        # is no rule-based way to grade diagnostic reasoning without a real
        # LLM judge) -- it still contributes to the conversation score the
        # same way a criterion that always passes always has.
        criteria = [
            EvaluationCriterion(
                name="History taking",
                passed=len(doctor_messages) >= MIN_HISTORY_QUESTIONS_FOR_PASS,
                feedback=(
                    "Asked a reasonable number of history questions before concluding."
                    if len(doctor_messages) >= MIN_HISTORY_QUESTIONS_FOR_PASS
                    else "Ask more history questions to build a fuller clinical picture."
                ),
            ),
            EvaluationCriterion(
                name="Patient communication",
                passed=len(messages) > 0,
                feedback=(
                    "Engaged with the patient throughout the encounter."
                    if len(messages) > 0
                    else "No messages were exchanged with the patient during this session."
                ),
            ),
            EvaluationCriterion(
                name="Diagnostic reasoning",
                passed=True,
                feedback=f"Compare your working diagnosis against the gold standard: {gold_standard}.",
            ),
        ]
        conversation_fraction = sum(1 for c in criteria if c.passed) / len(criteria)
        score = round(conversation_fraction * 100, 1)

        return SessionEvaluation(
            score=score,
            summary=(
                "[stub evaluation -- placeholder, not real OSCE grading] "
                f"conversation {conversation_fraction * 100:.0f}% -> overall {score}."
            ),
            criteria_breakdown=criteria,
        )
