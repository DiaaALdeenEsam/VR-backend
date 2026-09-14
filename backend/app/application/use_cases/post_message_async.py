"""Async reply-generation path for slow PatientReplyGenerator backends (today,
only "rag_api" -- 6-48s observed per call against the real API; see
app/infrastructure/rag_patient_generator.py).

Replaces the single blocking `await reply_generator.generate_reply(...)` call
PostMessageUseCase makes with: persist the user message and a placeholder
assistant message immediately, return that placeholder right away (the
caller/router can respond to the HTTP request in ~1-2s, well before the real
reply exists), then generate the real reply in a background asyncio task and
push it to the session's live WS connection (see app/infrastructure/ws_hub.py)
once it's ready.

Concurrency/ordering policy -- what happens if a second message arrives for
the same session before the first reply has finished generating: REJECT with
SessionBusyError (409 Conflict), not queue or cancel. Reasoning:
  - Queueing is a worse fit than it looks: the RAG-patient route is stateless
    and needs the *complete* message history on every call. If a second
    doctor message were queued while the first reply is still in flight,
    there is no well-defined history to send for it yet -- should it include
    only the first user message (excluding the still-pending first reply)?
    That's not "the conversation so far", it's a guess. Waiting for the first
    reply before even accepting the second message sidesteps the ambiguity
    entirely rather than encoding an answer to it.
  - Cancelling the in-flight call wastes the (slow, real) external request
    already in progress, and reintroduces the ordering ambiguity above for
    whichever message actually gets sent.
  - Two messages for one session in flight at once does not correspond to
    anything a real doctor-patient conversation does -- it is a client-side
    double-submit (or a genuine bug) far more often than a deliberate action,
    so an explicit, immediate 409 (rather than silently accepting and
    reordering) is the right signal back to the client.

This is a separate use case/class from PostMessageUseCase rather than a
runtime branch inside it: the two have different transaction shapes (this one
needs a fresh UnitOfWork per phase -- see uow_factory below -- since its
background phase outlives the HTTP request that triggered it; PostMessageUseCase
does everything in one transaction) and different return-value meaning (a
placeholder vs. the finished reply). app/api/deps.py's get_post_message_use_case
constructs this one unconditionally for the router (the sole
PatientReplyGenerator, RagPatientReplyGenerator, is always 6-48s-per-call
shaped) -- PostMessageUseCase remains real, live code, just no longer wired
to production; tests still construct it directly for fast/deterministic
fixtures that don't care about the async orchestration layer (see
tests/conftest.py).

Uses `asyncio.wait_for`/`create_task`/`sleep`/`TimeoutError` directly (unlike
every other use case in this package) -- treated as a language-level
concurrency primitive, not a swappable infrastructure adapter: every
`async def`/`await` in this codebase's domain/application layers already
assumes an asyncio-flavored runtime, and orchestrating concurrent background
work is itself the application layer's job, not something to push behind a
port the way an actually-swappable external system (an LLM, a queue) would
be.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app.application.use_cases.post_message import retrieve_evidence_or_empty
from app.config import Settings
from app.domain.entities import Message, utcnow
from app.domain.exceptions import NotFoundError, RagServiceUnavailableError, RagValidationError, SessionBusyError
from app.domain.repositories import (
    AbstractUnitOfWork,
    EvidenceRetriever,
    PatientReplyGenerator,
    ReplyPushPort,
    SessionConcurrencyGuard,
    TextToSpeechPort,
)

logger = logging.getLogger(__name__)

_PENDING_PLACEHOLDER_TEXT = ""


class PostMessageAsyncUseCase:
    def __init__(
        self,
        uow_factory: Callable[[], AbstractUnitOfWork],
        reply_generator: PatientReplyGenerator,
        evidence_retriever: EvidenceRetriever,
        settings: Settings,
        concurrency_guard: SessionConcurrencyGuard,
        push_port: ReplyPushPort,
        text_to_speech: TextToSpeechPort | None = None,
    ) -> None:
        """uow_factory (not a single `uow` instance): this use case needs an
        independent transaction for each of its three phases (persist the
        user message + placeholder; generate the reply; write the finished
        result), and the middle phase runs in a background task that outlives
        whatever request-scoped `uow` triggered it -- reusing one `uow`/
        AsyncSession across that gap would mean touching a session FastAPI
        may already have closed by the time the background task gets to it.
        `uow_factory` stays an AbstractUnitOfWork-typed callable, so this
        module still only depends on the domain port, not a concrete
        SQLAlchemy type -- app/api/deps.py passes `get_uow` itself (already a
        "make a fresh one" function, not a cached instance) as this argument.

        text_to_speech: only set by the voice path (see
        app/api/routers/voice.py) -- when present, TTS also runs (on the
        finished reply text) before the result is pushed, so the voice loop
        gets one push carrying both the text and its audio rather than two
        round trips.
        """

        self._uow_factory = uow_factory
        self._reply_generator = reply_generator
        self._evidence_retriever = evidence_retriever
        self._settings = settings
        self._concurrency_guard = concurrency_guard
        self._push_port = push_port
        self._text_to_speech = text_to_speech

    async def execute(self, session_id: str, content: str) -> Message:
        if self._concurrency_guard.is_busy(session_id):
            raise SessionBusyError(session_id)

        logger.info("[post_message] flow_started | session_id=%s", session_id)

        async with self._uow_factory() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            scenario = await uow.scenarios.get(session.scenario_id)
            if scenario is None:
                raise NotFoundError("Scenario", session.scenario_id)

            user_message = Message(session_id=session_id, role="user", content=content, created_at=utcnow())
            await uow.messages.add(user_message)

            placeholder = Message(
                session_id=session_id,
                role="assistant",
                content=_PENDING_PLACEHOLDER_TEXT,
                created_at=utcnow(),
                status="pending",
            )
            placeholder = await uow.messages.add(placeholder)

            await uow.commit()

        assert placeholder.id is not None
        logger.info(
            "[post_message] user_message_persisted | session_id=%s | message_id=%s",
            session_id,
            placeholder.id,
        )
        # Registered *before* returning, so a second POST arriving the instant
        # after this call returns still sees is_busy() == True -- no window
        # where two calls could both pass the check above.
        self._concurrency_guard.start(session_id, self._generate_and_push(session_id, placeholder.id, content))
        return placeholder

    async def _generate_and_push(self, session_id: str, message_id: int, user_content: str) -> None:
        start_time = time.monotonic()
        try:
            async with self._uow_factory() as uow:
                row = await uow.messages.update_content(message_id, _PENDING_PLACEHOLDER_TEXT, status="generating")
                history = [m for m in await uow.messages.list_by_session(session_id) if m.id != message_id]
                session = await uow.sessions.get(session_id)
                assert session is not None  # existed moments ago in execute(); not deleted mid-flight in this app
                scenario = await uow.scenarios.get(session.scenario_id)
                assert scenario is not None
                await uow.commit()
            del row  # only needed to move the row to "generating"; not read again here

            filler_task = asyncio.create_task(self._push_filler_after_delay(session_id, message_id))
            failed = False
            reply_text = self._settings.rag_patient_fallback_reply

            logger.info(
                "[post_message] calling_rag_patient_api | session_id=%s | message_id=%s",
                session_id,
                message_id,
            )
            try:
                evidence = await retrieve_evidence_or_empty(self._evidence_retriever, user_content)
                reply_text = await asyncio.wait_for(
                    self._reply_generator.generate_reply(scenario, history, user_content, evidence),
                    timeout=self._settings.rag_patient_hard_timeout_seconds,
                )
                logger.info(
                    "[post_message] rag_patient_reply_received | session_id=%s | message_id=%s | duration_s=%.1f",
                    session_id,
                    message_id,
                    time.monotonic() - start_time,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[post_message] rag_patient_timeout | reason=connection | session_id=%s | message_id=%s | "
                    "timeout_s=%.0f -- falling back to fixed reply",
                    session_id,
                    message_id,
                    self._settings.rag_patient_hard_timeout_seconds,
                )
                failed = True
                reply_text = self._settings.rag_patient_fallback_reply
            except RagValidationError as exc:
                # More specific than the RagServiceUnavailableError clause
                # below (RagValidationError is a subclass of it -- see that
                # class's docstring, app/domain/exceptions.py) -- catches
                # first, purely so this log line can say reason="validation"
                # instead of reason="connection". Behavior is identical
                # either way: fall back to the same fixed reply, `failed`
                # still True so the JSON reply frame's status still reads
                # "failed", not "complete" -- this isn't a real reply, and
                # nothing about that changes just because the cause was a
                # request-validation problem instead of an outage.
                logger.warning(
                    "[post_message] rag_patient_validation_error | reason=validation | session_id=%s | "
                    "message_id=%s | error=%s -- falling back to fixed reply",
                    session_id,
                    message_id,
                    exc,
                )
                failed = True
                reply_text = self._settings.rag_patient_fallback_reply
            except RagServiceUnavailableError as exc:
                logger.warning(
                    "[post_message] rag_patient_unavailable | reason=connection | session_id=%s | "
                    "message_id=%s | error=%s -- falling back to fixed reply",
                    session_id,
                    message_id,
                    exc,
                )
                failed = True
                reply_text = self._settings.rag_patient_fallback_reply
            finally:
                filler_task.cancel()

            async with self._uow_factory() as uow:
                final = await uow.messages.update_content(
                    message_id, reply_text, status="failed" if failed else "complete"
                )
                await uow.commit()
            logger.info(
                "[post_message] reply_persisted | session_id=%s | message_id=%s | status=%s",
                session_id,
                message_id,
                final.status,
            )

            delivered = await self._push_port.push(
                session_id,
                {
                    "type": "reply",
                    "id": final.id,
                    "role": final.role,
                    "content": final.content,
                    "status": final.status,
                    "created_at": final.created_at.isoformat(),
                },
            )
            logger.info(
                "[post_message] pushed_via_websocket | session_id=%s | message_id=%s | delivered=%s",
                session_id,
                message_id,
                delivered,
            )

            # Synthesize audio for BOTH a real reply and the fixed fallback
            # line -- not gated on `failed`. The fallback text is fixed and
            # known in advance (Settings.rag_patient_fallback_reply), so
            # synthesizing it costs no more than any other short string and
            # lets a voice client always expect text+audio together, even on
            # failure, rather than silently getting text with no audio for
            # this one status. `status` on the JSON "reply" frame above is
            # untouched by this -- it still reads "failed" whenever `failed`
            # is True, so a client can still tell a real reply from the
            # canned fallback; only whether audio accompanies it changes.
            if self._text_to_speech is not None:
                logger.info(
                    "[post_message] tts_started | session_id=%s | message_id=%s",
                    session_id,
                    message_id,
                )
                try:
                    audio = await self._text_to_speech.synthesize(final.content)
                except Exception:
                    logger.warning(
                        "[post_message] tts_failed | session_id=%s | message_id=%s -- reply delivered without audio",
                        session_id,
                        message_id,
                    )
                else:
                    logger.info(
                        "[post_message] tts_completed | session_id=%s | message_id=%s | audio_bytes=%d",
                        session_id,
                        message_id,
                        len(audio),
                    )
                    await self._push_port.push_bytes(session_id, audio)

            logger.info(
                "[post_message] flow_completed | session_id=%s | message_id=%s | status=%s | duration_s=%.1f",
                session_id,
                message_id,
                final.status,
                time.monotonic() - start_time,
            )
        except Exception:
            logger.error(
                "[post_message] flow_failed | session_id=%s | message_id=%s | duration_s=%.1f",
                session_id,
                message_id,
                time.monotonic() - start_time,
                exc_info=True,
            )
            raise

    async def _push_filler_after_delay(self, session_id: str, message_id: int) -> None:
        """Interim "still thinking" signal if the real reply isn't back yet by
        Settings.rag_patient_filler_after_seconds. A pure UI hint, not dialogue
        -- no content/audio, just a status event -- so there is nothing here
        that could contradict or duplicate the eventual real reply."""

        await asyncio.sleep(self._settings.rag_patient_filler_after_seconds)
        logger.warning(
            "[post_message] still_generating_filler_sent | session_id=%s | message_id=%s | after_s=%.0f",
            session_id,
            message_id,
            self._settings.rag_patient_filler_after_seconds,
        )
        await self._push_port.push(session_id, {"type": "thinking", "id": message_id})
