"""Read-only reconstruction of exactly what the real evaluation flow computed
for one session -- calls the REAL production functions directly
(gather_evaluation_inputs from app/application/use_cases/evaluate_session.py,
build_prompt from app/infrastructure/evaluation_prompt.py), against the same
real app.db, so the printed prompt/inputs are provably identical to what
RagLlmEvaluator actually sent -- not a reimplementation or a guess.

Read-only: gather_evaluation_inputs() never calls uow.commit() (see its own
docstring). No data is written.

Usage:
    python scripts/reconstruct_eval_prompt.py <session_id>
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys

from app.application.use_cases.evaluate_session import gather_evaluation_inputs
from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.evaluation_prompt import (
    CASE_TEXT_MAX_CHARS,
    GOLD_STANDARD_MAX_CHARS,
    MAX_PROMPT_CHARS,
    build_prompt,
)


async def main(session_id: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    engine = create_engine_from_settings(get_settings())
    session_factory = create_session_factory(engine)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        inputs = await gather_evaluation_inputs(uow, session_id)

    await engine.dispose()

    print("=== EvaluationInputs gathered by the real gather_evaluation_inputs() ===\n")
    print(f"case_text: full length = {len(inputs.case_text)} chars")
    print(f"  CASE_TEXT_MAX_CHARS = {CASE_TEXT_MAX_CHARS} -- {'TRUNCATED' if len(inputs.case_text) > CASE_TEXT_MAX_CHARS else 'fits, not truncated'} "
          f"in the prompt (loses {max(0, len(inputs.case_text) - CASE_TEXT_MAX_CHARS)} chars if truncated)")
    print(f"gold_standard: full length = {len(inputs.gold_standard)} chars")
    print(f"  GOLD_STANDARD_MAX_CHARS = {GOLD_STANDARD_MAX_CHARS} -- {'TRUNCATED' if len(inputs.gold_standard) > GOLD_STANDARD_MAX_CHARS else 'fits, not truncated'} "
          f"in the prompt (loses {max(0, len(inputs.gold_standard) - GOLD_STANDARD_MAX_CHARS)} chars if truncated)")
    print(f"messages: {len(inputs.messages)} total (transcript)")
    for i, m in enumerate(inputs.messages, start=1):
        print(f"  {i}. [{m['role']}] {m['content']!r}")
    print("(chat-only assessment model: no ordered_tests/answers/relevant_test_ids/total_questions anymore)")

    prompt, response_language = build_prompt(**dataclasses.asdict(inputs))

    print(f"\n=== Full prompt actually sent to POST /v1/rag/chat (response_language={response_language!r}) ===")
    print(f"prompt length: {len(prompt)} chars (MAX_PROMPT_CHARS budget = {MAX_PROMPT_CHARS})")
    print()
    print(prompt)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/reconstruct_eval_prompt.py <session_id>")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
