"""Stub implementation of the PatientReplyGenerator port.

This deliberately does NOT wire a real LLM -- it just proves the persistence
flow (message in, message out) works end to end. Swap this out for a real
implementation (e.g. calling an LLM with scenario.case_text as hidden context)
without touching PostMessageUseCase or anything above it.
"""

from __future__ import annotations

from app.domain.entities import Message, Scenario
from app.domain.repositories import PatientReplyGenerator


class StubPatientReplyGenerator(PatientReplyGenerator):
    async def generate_reply(
        self,
        scenario: Scenario,
        history: list[Message],
        user_message: str,
    ) -> str:
        return f"[placeholder patient reply for scenario '{scenario.name}'] You said: {user_message}"
