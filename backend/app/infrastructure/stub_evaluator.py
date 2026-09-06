"""Stub implementation of the EvaluationGenerator port.

Returns realistic-shaped OSCE evaluation data derived from simple,
deterministic rules (message counts, investigation-ordering appropriateness,
quiz accuracy) rather than any real clinical reasoning -- there's no ML
dependency here at all. This proves the evaluation flow end to end, now
across all three signals the product's intended workflow cares about (chat,
test ordering, quiz), not just the chat transcript.

STILL A PLACEHOLDER: the weighted scoring below (see CONVERSATION_WEIGHT /
TEST_ORDERING_WEIGHT / QUIZ_WEIGHT, and the MIN_HISTORY_QUESTIONS_FOR_PASS /
QUIZ_PASS_THRESHOLD cutoffs) is fixed, rule-based arithmetic picked purely to
get the EvaluationGenerator port signature and API contract shape-correct
ahead of a real evaluator. None of these numbers are derived from pedagogical
research or a real OSCE rubric -- they're arbitrary and trivially adjustable
constants. Swap this whole module out for a real/local LLM-backed
implementation later (see Settings.evaluation_backend in app/config.py)
without touching EvaluateSessionUseCase, the port signature, or anything
above it -- the replacement only needs to honor the same evaluate() shape.
"""

from __future__ import annotations

from app.domain.entities import EvaluationCriterion, SessionEvaluation
from app.domain.repositories import EvaluationGenerator

MIN_HISTORY_QUESTIONS_FOR_PASS = 2

# Placeholder weights for the three scoring buckets below -- must sum to 1.0.
# See the module docstring: arbitrary and not pedagogically derived, here
# only to make the score computation shape-correct ahead of a real evaluator.
CONVERSATION_WEIGHT = 0.4
TEST_ORDERING_WEIGHT = 0.3
QUIZ_WEIGHT = 0.3

# Minimum fraction of the scenario's total quiz questions that must be
# answered correctly (not just out of what was attempted) to "pass" the quiz
# criterion. Also arbitrary -- see module docstring.
QUIZ_PASS_THRESHOLD = 0.5


class StubEvaluationGenerator(EvaluationGenerator):
    async def evaluate(
        self,
        case_text: str,
        gold_standard: str,
        messages: list[dict[str, str]],
        ordered_tests: list[dict],
        answers: list[dict],
        relevant_test_ids: list[int],
        total_questions: int,
    ) -> SessionEvaluation:
        doctor_messages = [m for m in messages if m.get("role") == "user"]

        # --- 40%: conversation ------------------------------------------------
        # "Diagnostic reasoning" is folded into this bucket rather than scored
        # on its own: it has always been fixed at passed=True (there is no
        # rule-based way to grade diagnostic reasoning without a real LLM
        # judge), so it contributes to the conversation fraction the same way
        # a criterion that always passes always has, rather than getting its
        # own weighted bucket alongside test-ordering/quiz.
        conversation_criteria = [
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
        conversation_fraction = sum(1 for c in conversation_criteria if c.passed) / len(conversation_criteria)

        # --- 30%: investigation ordering ---------------------------------------
        ordered_test_ids = [t["test_id"] for t in ordered_tests]
        relevant_set = set(relevant_test_ids)
        relevant_ordered = sum(1 for tid in ordered_test_ids if tid in relevant_set)
        total_ordered = len(ordered_test_ids)

        if total_ordered == 0:
            test_ordering_fraction = 0.0
            test_ordering_feedback = "No investigations were ordered."
        else:
            test_ordering_fraction = relevant_ordered / total_ordered
            test_ordering_feedback = (
                f"{relevant_ordered}/{total_ordered} ordered investigation(s) were clinically relevant "
                f"to this case; {total_ordered - relevant_ordered} returned a generic/irrelevant result."
            )
        test_ordering_criterion = EvaluationCriterion(
            name="Investigation appropriateness",
            passed=relevant_ordered >= 1,
            feedback=test_ordering_feedback,
        )

        # --- 30%: quiz accuracy -------------------------------------------------
        correct_answers = sum(1 for a in answers if a.get("is_correct"))
        # Denominator is every question available for the scenario
        # (total_questions), not len(answers) -- leaving questions unanswered
        # must hurt this sub-score, per design, not just answering wrong ones.
        # A scenario with zero questions can't score above 0 here; a real
        # evaluator would more likely exclude this bucket and reweight the
        # other two rather than penalize a session whose scenario has no quiz
        # at all -- out of scope for this placeholder (see module docstring).
        quiz_fraction = (correct_answers / total_questions) if total_questions > 0 else 0.0
        quiz_criterion = EvaluationCriterion(
            name="Quiz performance",
            passed=quiz_fraction >= QUIZ_PASS_THRESHOLD,
            feedback=(
                f"{correct_answers}/{len(answers)} answered correctly, "
                f"out of {total_questions} total question(s) for this scenario."
            ),
        )

        criteria = [*conversation_criteria, test_ordering_criterion, quiz_criterion]

        score = round(
            (
                conversation_fraction * CONVERSATION_WEIGHT
                + test_ordering_fraction * TEST_ORDERING_WEIGHT
                + quiz_fraction * QUIZ_WEIGHT
            )
            * 100,
            1,
        )

        return SessionEvaluation(
            score=score,
            summary=(
                "[stub weighted evaluation -- placeholder, not real OSCE grading] "
                f"conversation {conversation_fraction * 100:.0f}% (weight {CONVERSATION_WEIGHT * 100:.0f}%), "
                f"investigations {test_ordering_fraction * 100:.0f}% (weight {TEST_ORDERING_WEIGHT * 100:.0f}%), "
                f"quiz {quiz_fraction * 100:.0f}% (weight {QUIZ_WEIGHT * 100:.0f}%) -> overall {score}."
            ),
            criteria_breakdown=criteria,
        )
