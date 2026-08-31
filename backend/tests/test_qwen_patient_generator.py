"""Tests for the local Qwen2.5-0.5B-Instruct-backed PatientReplyGenerator.

These exercise the real model -- a real (small) local download and real CPU
inference, not a mock. They're automatically SKIPPED (not failed) if the
heavy ML stack or the model weights aren't available in this environment (no
`torch`/`transformers` installed, no network on first run, no disk space,
etc.) via pytest.importorskip / an explicit _ensure_loaded() check, so
`pytest` stays green everywhere -- while giving real coverage wherever the
stack IS available, as it is in this project's dev environment.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

from app.api.deps import get_patient_reply_generator  # noqa: E402
from app.domain.entities import Message, Scenario, utcnow  # noqa: E402
from app.infrastructure.qwen_patient_generator import (  # noqa: E402
    QwenPatientReplyGenerator,
    _ensure_loaded,
)

if not _ensure_loaded():
    pytest.skip(
        "Qwen2.5-0.5B-Instruct could not be loaded in this environment (no network / no cached "
        "weights / load failure) -- skipping real-model tests. See "
        "app/infrastructure/qwen_patient_generator.py's fallback behavior for what a doctor's "
        "chat session does instead when this happens outside of tests.",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    return Scenario(
        id=1,
        name="التهاب الزائدة الدودية الحاد",
        case_text=(
            "مريض عمره 22 عامًا يشكو من ألم حاد في الربع السفلي الأيمن من البطن منذ 12 ساعة، "
            "مصحوب بغثيان وحمى خفيفة."
        ),
        gold_standard="التهاب الزائدة الدودية الحاد",
    )


def _looks_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text)


async def test_generate_reply_returns_nonempty_arabic_text(scenario: Scenario) -> None:
    generator = QwenPatientReplyGenerator()

    reply = await generator.generate_reply(scenario, history=[], user_message="أين يؤلمك بالضبط؟")

    assert isinstance(reply, str)
    assert reply.strip() != ""
    assert _looks_arabic(reply)


async def test_generate_reply_uses_conversation_history_without_erroring(scenario: Scenario) -> None:
    generator = QwenPatientReplyGenerator()
    history = [
        Message(session_id="s1", role="user", content="مرحبًا، ما اسمك؟", created_at=utcnow()),
        Message(session_id="s1", role="assistant", content="اسمي أحمد.", created_at=utcnow()),
    ]

    reply = await generator.generate_reply(scenario, history=history, user_message="منذ متى تشعر بالألم؟")

    assert reply.strip() != ""


async def test_falls_back_gracefully_when_the_model_cannot_be_loaded(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a load failure (bad repo id) and asserts this never raises out to the caller."""

    import app.infrastructure.qwen_patient_generator as qwen_mod

    # monkeypatch restores all of these automatically after the test, so the
    # real cached model/tokenizer (already loaded by earlier tests in this
    # module) is back in place for anything that runs after this one.
    monkeypatch.setattr(qwen_mod, "_tokenizer", None)
    monkeypatch.setattr(qwen_mod, "_model", None)
    monkeypatch.setattr(qwen_mod, "_load_failed", False)
    monkeypatch.setattr(qwen_mod, "MODEL_NAME", "this-org/this-model-does-not-exist-12345")

    generator = qwen_mod.QwenPatientReplyGenerator()
    reply = await generator.generate_reply(scenario, history=[], user_message="test")

    assert "تعذّر" in reply  # the documented fallback message, not a crash/exception
    assert qwen_mod._load_failed is True


async def test_post_message_endpoint_uses_qwen_and_returns_nonempty_reply(app, client, seed_ids) -> None:
    """End-to-end: POST /sessions/{id}/messages, with the REAL Qwen generator wired in.

    tests/conftest.py's `app` fixture overrides get_patient_reply_generator with
    the stub by default (see the comment there for why); this test overrides it
    back to the real thing, specifically to prove the wiring in
    app/api/deps.py::get_post_message_use_case actually reaches
    QwenPatientReplyGenerator end to end through the HTTP layer.
    """

    def override_with_qwen() -> QwenPatientReplyGenerator:
        return QwenPatientReplyGenerator()

    app.dependency_overrides[get_patient_reply_generator] = override_with_qwen
    try:
        create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
        assert create_resp.status_code == 201
        session_id = create_resp.json()["session_id"]

        message_resp = await client.post(
            f"/sessions/{session_id}/messages", json={"content": "هل تشعر بغثيان؟"}
        )

        assert message_resp.status_code == 201
        body = message_resp.json()
        assert body["role"] == "assistant"
        assert body["content"].strip() != ""
        assert _looks_arabic(body["content"])
    finally:
        del app.dependency_overrides[get_patient_reply_generator]
