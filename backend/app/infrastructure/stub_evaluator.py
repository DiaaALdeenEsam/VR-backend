"""Stub implementation of the EvaluationGenerator port.

Returns realistic-shaped OSCE evaluation data derived from simple, deterministic
checks on the conversation (message counts) rather than any real clinical
reasoning -- there's no ML dependency here at all. This proves the evaluation
flow end to end. Swap this out for a real/local LLM-backed implementation later
(see Settings.evaluation_backend in app/config.py) without touching
EvaluateSessionUseCase or anything above it.
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

        passed_count = sum(1 for c in criteria if c.passed)
        score = round(passed_count / len(criteria) * 100, 1) if criteria else 0.0

        return SessionEvaluation(
            score=score,
            summary=(
                f"[stub evaluation] {passed_count}/{len(criteria)} criteria met. "
                "This is placeholder feedback for development/testing, not real OSCE grading."
            ),
            criteria_breakdown=criteria,
        )
