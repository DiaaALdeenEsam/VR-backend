# Patient Simulation Backend

FastAPI backend for a medical patient-simulation trainer, in a clean-architecture
layout, backed by a real SQLite database (SQLModel + Alembic, fully async).

For a fuller narrative than this file — architecture diagrams, a traced
request flow, and a build-order walkthrough of every feature, in English or
Arabic (toggle in the top-right corner) — see
[`docs/project-internals.html`](docs/project-internals.html), or
[`docs/project-internals-ar.pdf`](docs/project-internals-ar.pdf) for a
print-ready Arabic PDF of the same content (13 pages, generated from that
same HTML via headless Chrome — regenerate it after editing the HTML rather
than hand-editing the PDF). For a step-by-step, Arabic-language guide to
testing every endpoint with copy-paste `curl` examples, see
[`docs/api-guide.html`](docs/api-guide.html).

## Setup

```powershell
# from backend/
python -m venv venv          # (already present if you cloned this as-is)
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env       # then edit DATABASE_URL etc. if needed
```

## Running migrations

Migrations are the source of truth for the schema — there is no `create_all()`
anywhere in the app.

```powershell
alembic upgrade head
```

This creates (by default) `app.db` next to this file. To create a new migration
after changing `app/infrastructure/db/models.py`:

```powershell
alembic revision -m "describe the change"   # write it by hand, or
alembic revision --autogenerate -m "describe the change"  # then review the diff
```

## Seeding legacy data

Point the seed script at a directory containing the legacy
`scenarios.json` / `test_categories.json` / `tests.json` / `questions.json`
files (see their shapes in `app/infrastructure/seed.py`'s docstring):

```powershell
python -m app.infrastructure.seed C:\path\to\legacy-json-dir
```

It's safe to run against a partial directory (any of the four files may be
missing) and maps each legacy string id to the new DB-assigned integer id as it
goes, so ids don't need to be numeric or contiguous in the source JSON.

`seed_data/` in this repo is a ready-to-use example set (Arabic, realistic):
two scenarios (one *with* `gold_standard` for the happy evaluate path, one
*without* for the 400 case), three test categories, eight tests with
plausible results, and OSCE quiz questions for both scenarios:

```powershell
python -m app.infrastructure.seed seed_data
```

## Running the server

```powershell
uvicorn app.main:app --reload
```

Then browse `http://127.0.0.1:8000/docs` for interactive API docs, or
`GET /health` for a DB-connectivity check.

## Running tests

```powershell
pytest
```

Each test gets its own throwaway in-memory SQLite database with the real
Alembic migrations applied against it (not `create_all()`) — see
`tests/conftest.py`. No file/state is shared between tests or with the dev
`app.db`.

`tests/test_all_endpoints_e2e.py` is a dedicated end-to-end suite that walks
through all 11 endpoints in one realistic flow (session → chat → order a
test → answer a question → review → evaluate), seeded via the *real*
`app/infrastructure/seed.py` functions loading `seed_data/*.json` — so it
also doubles as a regression test for the seed script and seed data
themselves, not just the API:

```powershell
pytest tests/test_all_endpoints_e2e.py -v
```

## Manually testing all 11 endpoints

Two ways to exercise every endpoint yourself against a real running server:

**1. Swagger UI** — after `uvicorn app.main:app --reload`, open
`http://127.0.0.1:8000/docs` and use "Try it out" on each route.

**2. The smoke-test runner script** — walks the same 11-endpoint flow as the
E2E suite above, printing each request/response:

```powershell
alembic upgrade head
python -m app.infrastructure.seed seed_data
uvicorn app.main:app          # in another terminal
python scripts/smoke_test.py  # defaults to http://127.0.0.1:8000
```

**3. `curl`, one endpoint at a time** (assumes `seed_data` is loaded, so
`scenario_id=1` is the appendicitis case and `test_id=1`/`question_id=1` exist):

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/scenarios
curl -X POST http://127.0.0.1:8000/sessions -H "Content-Type: application/json" -d "{\"scenario_id\": 1}"
# -> copy session_id from the response into SESSION_ID below
curl -X POST http://127.0.0.1:8000/sessions/SESSION_ID/messages -H "Content-Type: application/json" -d "{\"content\": \"...\"}"
curl http://127.0.0.1:8000/test-categories
curl http://127.0.0.1:8000/test-categories/1/tests
curl -X POST http://127.0.0.1:8000/sessions/SESSION_ID/tests -H "Content-Type: application/json" -d "{\"test_id\": 1}"
curl http://127.0.0.1:8000/scenarios/1/questions
curl -X POST http://127.0.0.1:8000/sessions/SESSION_ID/answers -H "Content-Type: application/json" -d "{\"question_id\": 1, \"choice_id\": 1}"
curl http://127.0.0.1:8000/sessions/SESSION_ID
curl -X POST http://127.0.0.1:8000/sessions/SESSION_ID/evaluate
```

(The `docs/api-guide.html` artifact has the same walkthrough with full
request/response examples and Arabic explanations, if you'd rather read it
there.)

## API surface

| Method | Path                              | Notes                                              |
|--------|------------------------------------|-----------------------------------------------------|
| GET    | `/scenarios`                       | id/name only — case_text & gold_standard never leave the server |
| POST   | `/sessions`                        | `{scenario_id}` → `{session_id}`                    |
| POST   | `/sessions/{id}/messages`          | `{content}` → a "pending" placeholder immediately; the real patient reply is generated in the background and pushed over `WS /ws/voice?session_id=...` (see below) |
| GET    | `/test-categories`                 |                                                       |
| GET    | `/test-categories/{id}/tests`      | menu only — no `result` until ordered                |
| POST   | `/sessions/{id}/tests`             | `{test_id}` → result, recorded as an OrderedTest     |
| GET    | `/scenarios/{id}/questions`        | choices only — no `correct_choice_id`                |
| POST   | `/sessions/{id}/answers`           | `{question_id, choice_id}` → recorded, no correctness leak; upserts on re-answer |
| GET    | `/sessions/{id}`                   | full review: messages, ordered tests, answers **with** correctness |
| POST   | `/sessions/{id}/evaluate`          | OSCE-style evaluation (score, summary, criteria); 400 if the scenario has no `gold_standard`; generated on demand, not persisted |
| POST   | `/transcribe`                      | multipart audio file → `{text}` (local faster-whisper "tiny" by default) |
| POST   | `/sessions/{id}/chat-voice`        | multipart audio file → transcript + persisted chat turn + base64 reply audio (see below) |
| WS     | `/ws/voice?session_id=...`         | same pipeline as chat-voice, one utterance per binary frame (see below) |
| GET    | `/health`                          | checks real DB connectivity, not just process liveness |

## Patient-reply generation (RAG API, async)

`POST /sessions/{id}/messages` is backed by the external RAG patient-chat API
— `app/infrastructure/rag_patient_generator.py`, calling
`POST /v1/rag/patient/chat` (see `docs/backend-rag-handoff.md`). This is the
only `PatientReplyGenerator`; there is no config toggle for it. A local
Qwen2.5-0.5B-Instruct model used to be a selectable alternative
(`PATIENT_REPLY_BACKEND=qwen`) but was removed after live reproduction
confirmed it hallucinates and breaks character systematically, while the RAG
API's anti-leak boundary was separately verified to hold cleanly under a
harder probe.

Latency against the real API runs 6-48s per call, so this backend is served
through an async ack-then-push flow rather than a blocking response:

- `POST /sessions/{id}/messages` persists the doctor's message and an
  assistant placeholder (`status: "pending"`) and returns immediately
  (~1-2s), before the real reply exists.
- The real reply is generated in the background and pushed to
  `WS /ws/voice?session_id=...` once ready (`{"type": "reply", "status":
  "complete"|"failed", ...}`), along with an interim `{"type": "thinking",
  ...}` event if it's still running after ~15s. See
  `app/application/use_cases/post_message_async.py` for the full design
  (concurrency policy, timeout/fallback behavior).
- A second message sent while the first reply is still generating gets
  `409 Conflict`, not queued or silently dropped.

`StubPatientReplyGenerator` (`app/infrastructure/patient_reply.py`, fixed
echo, zero ML/network dependency) still exists purely as a test double — the
general test suite's shared `app`/`client` fixtures override
`get_post_message_use_case` directly to the synchronous `PostMessageUseCase`
wired with it, so most of the suite keeps getting an immediate, complete
reply in the same response without exercising the async orchestration layer.
`tests/test_post_message_async.py` is the dedicated suite for that layer,
using a fake controllable-delay generator instead of the real network call.

## Voice pipeline (STT + TTS)

Three endpoints, all backed by real local libraries by default:

- **`POST /transcribe`** — upload an audio file (`multipart/form-data`,
  field name `file`), get back `{"text": "..."}`.
- **`POST /sessions/{id}/chat-voice`** — upload an audio file for a session;
  it's transcribed, run through the same persisted chat turn
  `POST /sessions/{id}/messages` uses, and the patient's reply comes back
  both as text and as base64-encoded synthesized audio
  (`reply_audio_content_type` tells you the actual MIME type — see below).
- **`WS /ws/voice?session_id=...`** — connect, then send one binary WS frame
  per utterance (a complete audio clip, not a raw PCM stream — there's no
  voice-activity segmentation here). For each one you get back two JSON text
  frames (`{"type": "transcript", ...}` then `{"type": "reply", ...}`)
  followed by one binary frame with the reply audio. An unknown `session_id`
  (or any other domain error) sends `{"type": "error", "detail": "..."}` and
  closes the connection.

Speech-to-text is [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)
("tiny" model, forced to Arabic, CPU-only, no system `ffmpeg` install needed —
`faster-whisper` bundles decoding via the `av` package). Text-to-speech is
[`gTTS`](https://gtts.readthedocs.io/) — note this makes a real network call
to Google Translate's TTS endpoint per request, so despite matching this
project's "Local\*Adapter" naming convention, it is **not** an offline
engine. (`piper-tts`, a genuinely offline engine, was considered for this
slot but isn't implemented: it needs `espeak-ng`, a system dependency that
doesn't install via `pip` alone.) Both fall back automatically to a stub
adapter — a labeled placeholder transcript, or a silent-but-valid WAV clip —
if the real backend can't load or a request to it fails, so the endpoints
stay usable even when the ML/network stack isn't fully available.

Switch backends with `STT_BACKEND` and `TTS_BACKEND` (see `.env.example`) —
the test suite's shared fixtures use `stub` for both by default for the same
"keep the general suite fast/deterministic" reason described above.
`tests/test_voice.py` covers the ports, both REST endpoints, and both WS
scenarios (a full round trip and the unknown-session error path) against the
stub adapters; run it live against the real backends by overriding the env
vars, or just hit a running `uvicorn` server directly with `curl`
(`-F "file=@clip.wav"`) or the WS client of your choice.

## Layer boundaries

`app/domain/` is pure Python — dataclasses (`entities.py`), abstract repository
interfaces and the `PatientReplyGenerator` / `EvaluationGenerator` /
`SpeechToTextPort` / `TextToSpeechPort` ports (`repositories.py`), and error
types (`exceptions.py`). It imports nothing from SQLModel, SQLAlchemy, Pydantic,
or FastAPI, and nothing else in the project imports *into* it except
`app/application/` and `app/infrastructure/`. `app/application/use_cases/` holds
one class per use case (start a session, order a test, answer a question, ...);
each is constructor-injected with an `AbstractUnitOfWork` and whatever port(s)
it needs (`ProcessVoiceChatUseCase` even composes another use case,
`PostMessageUseCase`, directly — the one place in this codebase a use case
depends on another rather than only on ports/uow) and knows nothing about
HTTP or SQL —
every mutating use case opens `async with self._uow:` and calls `commit()`
exactly once, so a failure partway through rolls back cleanly instead of
leaving partial writes. `app/infrastructure/` is where the SQLModel table
models, the async engine/session setup, and the concrete repository/UnitOfWork
implementations live — this is the only place that talks SQL, and it depends
*inward* on `domain` (implementing its interfaces), never the other way
around. `app/api/` is the thin outermost layer: FastAPI routers parse a
request, call a use case via a `Depends()`-injected provider from `api/deps.py`,
and map the result to a Pydantic response schema in `api/schemas/` — no
business logic lives here, and domain exceptions are translated to HTTP status
codes centrally in `api/error_handlers.py` rather than per-route. Net effect:
you can swap SQLite for Postgres, or the patient-reply generator for a
different implementation, by touching only `app/infrastructure/` (plus one
line in `api/deps.py::get_patient_reply_generator` to point at the new
class) — without any change to `app/domain/` or `app/application/`. This is
exactly how the local Qwen2.5-0.5B-Instruct generator was swapped for the RAG
API's patient-chat route (see "Patient-reply generation" above) with zero
changes to the `PatientReplyGenerator` port or any caller above it. The
`PatientReplyGenerator`/`EvaluationGenerator`/`SpeechToTextPort`/
`TextToSpeechPort` ports each have a stub-plus-real implementation pair in
`app/infrastructure/` already, all following the same shape — a working
example of exactly that swap, repeated four times.
