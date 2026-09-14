"""One-off script: inserts the 5 post-session-quiz questions for the Asthma
scenario (sc_asthma_severe_attack in seed_data/questions.json -- disease
name, attack-severity classification, and management plan across its 3
stages) into the current database, scoped ONLY to that scenario.

Unlike scripts/import_patient_case_from_json.py (which goes through
PatientCaseRepository, a real application-layer repository), this reuses
app.infrastructure.seed.seed_questions() directly -- the exact same
placeholder-then-patch logic `python -m app.infrastructure.seed seed_data`
already uses for every other scenario's questions, just invoked here against
one legacy scenario key instead of re-running the whole (non-idempotent)
seeder, which would duplicate every other scenario/category/test in an
already-seeded database.

Safety model (same live-verification discipline as
scripts/wipe_all_except_asthma.py):
  - Refuses to run if the target scenario (id ASTHMA_SCENARIO_ID) doesn't
    exist or its name doesn't match exactly.
  - Refuses to run if ANY question already exists for that scenario with one
    of the 5 target categories -- prevents duplicate insertion on a re-run.
  - Single transaction; any failure rolls back everything.
  - Never touches any other scenario, question, or table.

Usage:
    python scripts/insert_asthma_quiz_questions.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.models import ChoiceModel, QuestionModel, ScenarioModel
from app.infrastructure.seed import seed_questions

BACKEND_DIR = Path(__file__).resolve().parent.parent
QUESTIONS_JSON = BACKEND_DIR / "seed_data" / "questions.json"

ASTHMA_LEGACY_KEY = "sc_asthma_severe_attack"
ASTHMA_SCENARIO_ID = 6
ASTHMA_SCENARIO_NAME = "Asthma"

QUIZ_CATEGORIES = {
    "diagnosis",
    "severity",
    "management_immediate",
    "management_monitoring",
    "management_disposition",
}


async def _verify_scenario(session: AsyncSession) -> None:
    scenario = await session.get(ScenarioModel, ASTHMA_SCENARIO_ID)
    if scenario is None:
        print(f"No scenario with id={ASTHMA_SCENARIO_ID} exists -- aborting, nothing written.")
        raise SystemExit(1)
    if scenario.name != ASTHMA_SCENARIO_NAME:
        print(
            f"Scenario id={ASTHMA_SCENARIO_ID} has name={scenario.name!r}, expected "
            f"{ASTHMA_SCENARIO_NAME!r} -- aborting rather than writing quiz questions onto "
            "an unexpected scenario."
        )
        raise SystemExit(1)


async def _verify_no_existing_quiz_rows(session: AsyncSession) -> None:
    result = await session.exec(
        select(QuestionModel).where(
            QuestionModel.scenario_id == ASTHMA_SCENARIO_ID,
            QuestionModel.category.is_not(None),  # type: ignore[union-attr]
        )
    )
    existing = list(result.all())
    if existing:
        print(
            f"{len(existing)} categorized question(s) already exist for scenario "
            f"id={ASTHMA_SCENARIO_ID}: {[(q.id, q.category) for q in existing]} -- "
            "aborting to avoid inserting duplicates. Delete them first if you intend to re-seed."
        )
        raise SystemExit(1)


async def run() -> None:
    if not QUESTIONS_JSON.is_file():
        print(f"Not found: {QUESTIONS_JSON}")
        raise SystemExit(1)

    all_questions = json.loads(QUESTIONS_JSON.read_text(encoding="utf-8"))
    quiz_questions = all_questions.get(ASTHMA_LEGACY_KEY)
    if not quiz_questions:
        print(f"No {ASTHMA_LEGACY_KEY!r} entry found in {QUESTIONS_JSON} -- nothing to insert.")
        raise SystemExit(1)

    found_categories = {q.get("category") for q in quiz_questions}
    if found_categories != QUIZ_CATEGORIES:
        print(
            f"seed_data/questions.json's {ASTHMA_LEGACY_KEY!r} entry has categories "
            f"{found_categories} -- expected exactly {QUIZ_CATEGORIES}. Aborting rather than "
            "inserting a partial/unexpected quiz."
        )
        raise SystemExit(1)

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        await _verify_scenario(session)
        await _verify_no_existing_quiz_rows(session)

        print(f"Verified: scenario id={ASTHMA_SCENARIO_ID} name={ASTHMA_SCENARIO_NAME!r}, no existing quiz rows.")
        print(f"Inserting {len(quiz_questions)} quiz questions from {QUESTIONS_JSON.name}...")

        # Reuses the exact same insertion logic `python -m app.infrastructure.seed`
        # uses for every scenario's questions.json entry -- placeholder
        # correct_choice_id=-1, insert choices, patch the real id in. Scoped to
        # only this one legacy key -> this one scenario id, so nothing else is
        # touched.
        await seed_questions(session, {ASTHMA_LEGACY_KEY: quiz_questions}, {ASTHMA_LEGACY_KEY: ASTHMA_SCENARIO_ID})

        await session.commit()

    # --- verification -------------------------------------------------------
    async with session_factory() as session:
        result = await session.exec(
            select(QuestionModel)
            .where(QuestionModel.scenario_id == ASTHMA_SCENARIO_ID)
            .order_by(QuestionModel.id)
        )
        rows = list(result.all())

        print(f"\nInserted {len(rows)} question(s) for scenario id={ASTHMA_SCENARIO_ID}:")
        for row in rows:
            choices_result = await session.exec(
                select(ChoiceModel).where(ChoiceModel.question_id == row.id).order_by(ChoiceModel.id)
            )
            choice_rows = list(choices_result.all())
            correct = next((c.text for c in choice_rows if c.id == row.correct_choice_id), "??? NOT FOUND ???")
            print(f"  id={row.id} category={row.category!r}")
            print(f"    text: {row.text}")
            print(f"    choices: {len(choice_rows)}, correct_choice_id={row.correct_choice_id} -> {correct!r}")

    await engine.dispose()
    print(f"\nDone. database: {settings.database_url}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic text prints correctly on Windows consoles
    asyncio.run(run())


if __name__ == "__main__":
    main()
