"""One-off script to import legacy JSON scenario data into the SQL database.

Usage:
    python -m app.infrastructure.seed <path-to-json-dir>

Expects, inside <path-to-json-dir>, any of:
    scenarios.json:       { "<id>": {"name": str, "case_text": str, "gold_standard": str|null} }
    test_categories.json: { "<id>": {"name": str} }
    tests.json:            { "<category_id>": { "<test_id>": {"name": str, "result": str} } }
    questions.json:        { "<scenario_id>": [ {"id": str, "text": str,
                              "choices": [{"id": str, "text": str}], "correct_choice_id": str} ] }

Legacy ids are arbitrary strings (not necessarily numeric) -- this script maps
each legacy id to the new integer primary key assigned by the database as it
inserts, and uses that mapping to resolve foreign keys in later files. Run
test_categories.json and scenarios.json before tests.json and questions.json
respectively (this script always loads them in the right order regardless of
which files are present).

This writes directly to the SQLModel table models rather than going through the
repository/use-case layers -- it is explicitly a one-off, infrastructure-only
import tool, not part of the request-handling runtime.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.models import (
    ChoiceModel,
    QuestionModel,
    ScenarioModel,
    TestCategoryModel,
    TestModel,
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


async def seed_test_categories(session: AsyncSession, data: dict[str, Any]) -> dict[str, int]:
    id_map: dict[str, int] = {}
    for legacy_id, payload in data.items():
        row = TestCategoryModel(name=payload["name"])
        session.add(row)
        await session.flush()
        assert row.id is not None
        id_map[legacy_id] = row.id
    return id_map


async def seed_tests(
    session: AsyncSession, data: dict[str, Any], category_id_map: dict[str, int]
) -> dict[str, int]:
    id_map: dict[str, int] = {}
    for legacy_category_id, tests in data.items():
        category_id = category_id_map.get(legacy_category_id)
        if category_id is None:
            print(f"  ! skipping tests for unknown category {legacy_category_id!r}")
            continue
        for legacy_test_id, payload in tests.items():
            row = TestModel(category_id=category_id, name=payload["name"], result=payload["result"])
            session.add(row)
            await session.flush()
            assert row.id is not None
            id_map[legacy_test_id] = row.id
    return id_map


async def seed_scenarios(session: AsyncSession, data: dict[str, Any]) -> dict[str, int]:
    id_map: dict[str, int] = {}
    for legacy_id, payload in data.items():
        row = ScenarioModel(
            name=payload["name"],
            case_text=payload["case_text"],
            gold_standard=payload.get("gold_standard"),
        )
        session.add(row)
        await session.flush()
        assert row.id is not None
        id_map[legacy_id] = row.id
    return id_map


async def seed_questions(
    session: AsyncSession, data: dict[str, Any], scenario_id_map: dict[str, int]
) -> None:
    for legacy_scenario_id, questions in data.items():
        scenario_id = scenario_id_map.get(legacy_scenario_id)
        if scenario_id is None:
            print(f"  ! skipping questions for unknown scenario {legacy_scenario_id!r}")
            continue

        for q in questions:
            # correct_choice_id is only known once the choices below are inserted
            # and have real ids, so insert with a placeholder and patch it after.
            question_row = QuestionModel(scenario_id=scenario_id, text=q["text"], correct_choice_id=-1)
            session.add(question_row)
            await session.flush()
            assert question_row.id is not None

            choice_id_map: dict[str, int] = {}
            for choice in q["choices"]:
                choice_row = ChoiceModel(question_id=question_row.id, text=choice["text"])
                session.add(choice_row)
                await session.flush()
                assert choice_row.id is not None
                choice_id_map[choice["id"]] = choice_row.id

            correct_id = choice_id_map.get(q["correct_choice_id"])
            if correct_id is None:
                raise ValueError(
                    f"question {q['id']!r} in scenario {legacy_scenario_id!r} references "
                    f"unknown correct_choice_id {q['correct_choice_id']!r}"
                )
            question_row.correct_choice_id = correct_id
            session.add(question_row)


async def run(json_dir: Path) -> None:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        print(f"Seeding from {json_dir} into {settings.database_url}")

        categories_path = json_dir / "test_categories.json"
        tests_path = json_dir / "tests.json"
        scenarios_path = json_dir / "scenarios.json"
        questions_path = json_dir / "questions.json"

        category_id_map: dict[str, int] = {}
        if categories_path.exists():
            category_id_map = await seed_test_categories(session, _load_json(categories_path))
            print(f"  test_categories: {len(category_id_map)}")

        if tests_path.exists():
            test_id_map = await seed_tests(session, _load_json(tests_path), category_id_map)
            print(f"  tests: {len(test_id_map)}")

        scenario_id_map: dict[str, int] = {}
        if scenarios_path.exists():
            scenario_id_map = await seed_scenarios(session, _load_json(scenarios_path))
            print(f"  scenarios: {len(scenario_id_map)}")

        if questions_path.exists():
            await seed_questions(session, _load_json(questions_path), scenario_id_map)
            print("  questions: done")

        await session.commit()

    await engine.dispose()
    print("Seed complete.")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m app.infrastructure.seed <path-to-json-dir>")
        raise SystemExit(1)

    json_dir = Path(sys.argv[1])
    if not json_dir.is_dir():
        print(f"Not a directory: {json_dir}")
        raise SystemExit(1)

    asyncio.run(run(json_dir))


if __name__ == "__main__":
    main()
