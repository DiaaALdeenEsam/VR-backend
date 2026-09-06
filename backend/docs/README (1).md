# Medical VR Project — Backend Integration

This README is the backend team's quick-start for integrating the current
Medical VR AI/runtime services. It covers Retrieval-Augmented Generation (RAG)
chat, virtual-patient role-play, cases, OSCE training, scoring, feedback, and
the speech-to-text (STT) and text-to-speech (TTS) boundary. Audio is only one
consumer of the service: the backend also uses these endpoints for dialogue
orchestration, case/session state, training phases, milestones, scoring, and
feedback. The backend owns VR/game state, transport, and audio; the project
services provide the AI and training behavior described below.

For the repository map, compatibility links, protected clinical-data boundary,
and canonical locations for legacy inputs and local artifacts, see
[`docs/reference/project-layout.md`](docs/reference/project-layout.md).

## Connection

Temporary backend-test URL:

```text
https://vr-rag-api.nport.link
```

LAN URL:

```text
http://10.20.0.100:8010
```

The nport URL is temporary and should be rechecked before each test window.
The backend team must receive the RAG API key through a secure channel. Never
commit it, place it in a ticket, or print it in logs.

Health and readiness do not require the key:

```bash
export RAG_API_BASE_URL='https://vr-rag-api.nport.link'
curl -H 'User-Agent: VR-Project-Backend/1.0' "$RAG_API_BASE_URL/healthz"
curl -H 'User-Agent: VR-Project-Backend/1.0' "$RAG_API_BASE_URL/readyz"
```

Expected readiness values include:

```json
{
  "status": "ready",
  "collection": "medical_chapters",
  "chunks": 9050
}
```

For authenticated operations, load the server-issued key through your secure
secret store:

```bash
export RAG_API_KEY='<receive securely from the service owner>'
```

For nport requests, send an explicit application `User-Agent`, for example
`VR-Project-Backend/1.0`. The temporary edge can reject Python's default
`Python-urllib` signature with HTTP 403; that is unrelated to API-key
authentication.

## Choose the integration surface

The current system has two complementary HTTP surfaces:

| Surface | Base URL | Use |
| --- | --- | --- |
| Direct RAG API | `https://vr-rag-api.nport.link` (temporary) / `http://10.20.0.100:8010` (LAN) | Stateless retrieval, clinician chat, and direct patient-role chat; the backend owns history and session state |
| Full VR runtime | `http://10.20.0.100:8000` (LAN only) | Persisted free-chat sessions, cases, OSCE phases, milestones, scores, feedback, and the browser runtime |

Use port `8010` when the backend wants a focused stateless AI/RAG boundary. Use
port `8000` when it needs the current persisted VR training lifecycle. These
are not interchangeable: port `8010` does not persist sessions or training,
while port `8000` is an unauthenticated LAN test service and is not exposed
through nport.

The full runtime API includes:

- `GET /api/roles`, `/api/chapters`, and `/api/cases` for role, persona, chapter,
  and case discovery;
- `POST /api/sessions` and `POST /api/chat` for persisted free conversation;
- `POST /api/training/start` and `/api/training/{id}/message` for OSCE training;
- `/api/training/{id}/advance`, `/scores`, `/milestones`, `/complete`, and
  `/feedback` for the training lifecycle.

The detailed request and response flow is in
[`docs/api/backend-vr-runtime-handoff.md`](docs/api/backend-vr-runtime-handoff.md).

For the complete backend flow, use the full runtime for persisted application
behavior and use the direct RAG API as the focused AI/RAG dependency. Do not
make the backend reconstruct patient prompts, case disclosure rules, scoring,
or feedback from `/v1/rag/query` results.

## Backend responsibilities beyond audio

The backend is not only an STT/TTS bridge. It should also coordinate the
application lifecycle around the project services:

| Backend need | Project endpoint | State owner |
| --- | --- | --- |
| Clinician RAG answer | `POST /v1/rag/chat` | Backend sends full history; RAG service retrieves and generates |
| Direct patient role-play test | `POST /v1/rag/patient/chat` | Backend sends full history, persona, and case query |
| Role/persona/case discovery | `GET /api/roles`, `/api/chapters`, `/api/cases` | Full runtime |
| Persisted free conversation | `POST /api/sessions`, `POST /api/chat` | Full runtime session |
| OSCE training lifecycle | `POST /api/training/*`, related status/scores/feedback routes | Full runtime session and database |

The direct RAG API is stateless and authenticated. The full runtime is the
current legacy persisted training service on the LAN and is unauthenticated;
it is not exposed through nport. These surfaces are complementary, not
interchangeable.

## Choose the chat role

```text
STT audio → backend transcript/history
  ├─ clinician/doctor → POST /v1/rag/chat → TTS audio
  └─ virtual patient  → POST /v1/rag/patient/chat → TTS audio
```

The API accepts text only. Both chat routes are stateless: the backend owns the
conversation history and sends the complete history on every request. Use
`/v1/rag/chat` for clinician-facing medical answers. Use
`/v1/rag/patient/chat` when the response must be a Syrian-Arabic patient role;
that route applies the existing patient persona, sanitized context, and
anti-leak prompt boundary.

Do not send virtual-patient traffic to the clinician route. It is intentionally
an assistant/doctor answer boundary.

## Test the full VR web UI and runtime

The full runtime UI is available for the current LAN test window at
[`http://10.20.0.100:8000/freechat`](http://10.20.0.100:8000/freechat). Choose
`patient`, select a persona and chapter, then send Arabic questions. Start a
new session when changing roles; the session role is fixed after creation.
The UI uses the persisted `/api/chat` runtime, not the direct port-8010 API.
The server resolves numeric chapter selections to chapter-filtered patient RAG
context and returns only safe inspector metadata for patient sessions.

Port 8000 is an unauthenticated LAN-only test service. It is not exposed by
nport and should be disabled after the test window:

```bash
ssh pc@10.20.0.100 systemctl --user disable --now vr-project-ui.service
```

The direct port-8010 chat API is stateless and does not create a server-side
session. Only `user` and `assistant` messages are accepted; the final message
must be the new `user` turn. System messages are rejected so the service can
retain its grounding and language controls.

## Arabic virtual-patient example

For the actual patient role, provide a persona and a case query. The patient
route currently returns Arabic only. Keep `case_query` the same on every turn
because the route is stateless:

```bash
curl -sS -X POST "$RAG_API_BASE_URL/v1/rag/patient/chat" \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: VR-Project-Backend/1.0' \
  -H "X-API-Key: $RAG_API_KEY" \
  -d '{
    "messages": [
      {"role": "user", "content": "مرحبا، شو عم تحس اليوم؟"}
    ],
    "persona": "middle_man",
    "case_query": "التهاب الكبد",
    "top_k": 5,
    "response_language": "ar"
  }'
```

For a follow-up, append the previous patient answer and the new user turn to
`messages`, and send the same `persona` and `case_query` again. The response
text remains at `choices[0].message.content`; the top-level
`speaker_role` is `patient`. Only safe source summaries are returned under
`grounding`; patient evidence text is never returned by this route.

Supported personas are `boy`, `girl`, `young_man`, `young_woman`,
`middle_man`, `middle_woman`, `elderly_man`, and `elderly_woman`.

## Arabic clinician chat example

For Arabic STT, set `response_language` explicitly to `ar`. This prevents the
retriever's internal Arabic-to-English embedding translation from being
mistaken for the answer language.

```bash
curl -sS -X POST "$RAG_API_BASE_URL/v1/rag/chat" \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: VR-Project-Backend/1.0' \
  -H "X-API-Key: $RAG_API_KEY" \
  -d '{
    "messages": [
      {"role": "user", "content": "مرحبا، شو أعراض التهاب الكبد؟"}
    ],
    "top_k": 5,
    "response_language": "ar"
  }'
```

For a follow-up, include the previous assistant answer:

```json
{
  "messages": [
    {"role": "user", "content": "مرحبا، شو أعراض التهاب الكبد؟"},
    {"role": "assistant", "content": "<previous API answer>"},
    {"role": "user", "content": "طيب وشو الفحوصات المطلوبة؟"}
  ],
  "top_k": 5,
  "content_types": ["investigations"],
  "response_language": "ar"
}
```

Use `response_language: "en"` for English. `auto` is supported and detects
the dominant script of the latest user message, but an explicit STT/UX locale
is preferred.

## Response contract

The response is compatible with the common chat-completion envelope:

```json
{
  "id": "ragchat-<request-id>",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "gemma4-biomedical-e4b",
  "response_language": "ar",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<text to send to TTS>"
      },
      "finish_reason": "stop"
    }
  ],
  "retrieval": {
    "query": "<latest user message>",
    "collection": "medical_chapters",
    "count": 5,
    "results": [
      {
        "rank": 1,
        "id": "<chunk id>",
        "text": "<retrieved evidence>",
        "metadata": {
          "chapter": "<chapter>",
          "section": "<section>",
          "content_type": "<type>",
          "source_file": "<source>",
          "title": "<title>"
        },
        "distance": 0.214
      }
    ]
  }
}
```

Use `choices[0].message.content` for TTS. Preserve the nested `retrieval`
metadata when storing or displaying provenance. `distance` is a ChromaDB
distance, not a confidence score.

The patient route uses the same `choices` shape but is deliberately narrower:

```json
{
  "speaker_role": "patient",
  "response_language": "ar",
  "choices": [
    {"message": {"role": "assistant", "content": "<patient reply for TTS>"}}
  ],
  "grounding": {
    "collection": "medical_chapters",
    "count": 2,
    "sources": [
      {"rank": 1, "id": "<source id>", "content_type": "symptoms", "distance": 0.214}
    ]
  }
}
```

The patient response does not expose retrieved chunk text, diagnosis labels,
or unrestricted metadata. Do not display `grounding` to the patient; it is for
backend diagnostics only.

Limits:

- 1–24 messages;
- 4,000 characters per message;
- 24,000 characters total per conversation;
- `top_k`: 1–15, default 5;
- up to 10 non-empty `content_types` values.

Current content types include `history`, `symptoms`, `exam`, `investigations`,
`diagnosis`, `management`, and `other`.

## Authentication

Both headers are accepted for authenticated operations:

```text
X-API-Key: <server-issued-key>
Authorization: Bearer <server-issued-key>
```

`/healthz` and `/readyz` are unauthenticated. `/v1/rag/chat`,
`/v1/rag/patient/chat`, and `/v1/rag/query` require the configured key. Do not
use the Plane API key as the RAG API key. The full runtime `/api/*` routes use
the current LAN-only legacy boundary and have a separate session contract.

## Errors and retry policy

| HTTP status | Detail code | Action |
| --- | --- | --- |
| `200` | — | Read the assistant content and retrieval provenance |
| `401` | `INVALID_API_KEY` | Fix secret injection; do not retry repeatedly |
| `422` | validation error | Correct the request body/history |
| `503` | `RAG_NOT_READY` | Wait for service readiness or use fallback |
| `503` | `RAG_CHAT_TIMEOUT` | Retry with bounded backoff |
| `503` | `RAG_CHAT_EMPTY` | Retry once at the backend, then show fallback |
| `503` | `RAG_CHAT_DEPENDENCY_ERROR` | Retry with bounded backoff |
| `503` | `RAG_LANGUAGE_MISMATCH` | Do not blindly retry; inspect requested locale |
| `503` | `RAG_CHAT_FAILED` | Log a request ID and show controlled fallback |
| `503` | `PATIENT_CHAT_TIMEOUT` | Retry with bounded backoff |
| `503` | `PATIENT_CHAT_EMPTY` | Retry once, then show a patient-safe fallback |
| `503` | `PATIENT_CHAT_DEPENDENCY_ERROR` | Retry with bounded backoff |
| `503` | `PATIENT_LANGUAGE_MISMATCH` | Do not send the result to TTS; show a controlled fallback |
| `503` | `PATIENT_CHAT_UNAVAILABLE` / `PATIENT_CHAT_FAILED` | Show a controlled fallback and inspect service health |

The API itself retries one transient LM Studio generation response of
`400 {"error":"terminated"}`. The backend should not call LM Studio or
ChromaDB directly.

## Direct retrieval option

Use `POST /v1/rag/query` only when the backend intentionally owns prompt
construction and role-specific generation. It returns ranked evidence but no
assistant answer:

```bash
curl -sS -X POST "$RAG_API_BASE_URL/v1/rag/query" \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: VR-Project-Backend/1.0' \
  -H "X-API-Key: $RAG_API_KEY" \
  -d '{"query":"treatment for hepatitis B","top_k":5}'
```

Do not use unrestricted direct retrieval or clinician `/v1/rag/chat` as the
virtual-patient disclosure boundary. For a direct port-8010 patient test, use
`/v1/rag/patient/chat`, which applies the current legacy runtime's
case-grounded patient context and anti-leak controls. For selected cases,
persisted sessions, and OSCE flows, use the full runtime described above. It is
the richer current integration point, but it remains a legacy LAN test service,
not a production security boundary.

## Live validation

The current deployment was verified on 2026-09-06:

- chat harness: 5/5 through loopback;
- chat harness: 5/5 through the public nport URL;
- retrieval contract harness: 38/38, including explicit patient-route
  authentication and validation checks;
- patient-role harness: 2/2 Arabic multi-turn patient checks through loopback
  and 2/2 through the public nport URL;
- Arabic initial and multi-turn replies were Arabic-dominant;
- patient replies were Arabic-dominant and marked `speaker_role: "patient"`;
- English and Arabic responses returned retrieval provenance;
- VR-17 retrieval replay preserved chapter accuracy@3 of 77.78%.

Run the reproducible checks when the API key is available:

```bash
export RAG_API_KEY='<securely supplied RAG key>'
python3 scripts/test_rag_api_chat.py \
  --base-url https://vr-rag-api.nport.link
python3 scripts/test_rag_api_contract.py \
  --base-url https://vr-rag-api.nport.link
```

For an interactive Arabic terminal conversation, use:

```bash
python3 scripts/rag_chat_cli.py \
  --base-url https://vr-rag-api.nport.link \
  --language ar
```

For an interactive Arabic virtual-patient conversation:

```bash
python3 scripts/rag_chat_cli.py \
  --base-url https://vr-rag-api.nport.link \
  --role patient \
  --persona middle_man \
  --case-query 'التهاب الكبد' \
  --language ar
```

## Project files and documentation

- [`api/main.py`](api/main.py) — FastAPI implementation for retrieval and chat
- [`prompts/rag_chat_system.txt`](prompts/rag_chat_system.txt) — grounded chat and language instructions
- [`scripts/rag_chat_cli.py`](scripts/rag_chat_cli.py) — interactive terminal client
- [`scripts/test_rag_api_chat.py`](scripts/test_rag_api_chat.py) — English/Arabic real-like chat regression
- [`scripts/test_rag_api_patient_chat.py`](scripts/test_rag_api_patient_chat.py) — Arabic patient-role and multi-turn regression
- [`scripts/test_rag_api_contract.py`](scripts/test_rag_api_contract.py) — HTTP contract and VR-17 replay harness
- [`docs/api/rag-api.md`](docs/api/rag-api.md) — canonical API contract and deployment reference
- [`docs/api/backend-rag-handoff.md`](docs/api/backend-rag-handoff.md) — backend integration handoff
- [`docs/api/backend-vr-runtime-handoff.md`](docs/api/backend-vr-runtime-handoff.md) — full VR/STT/TTS/runtime boundary
- [`docs/research/2026-09-06-rag-chat-api-integration.md`](docs/research/2026-09-06-rag-chat-api-integration.md) — implementation, alternatives, evidence, and limitations

The service uses the configured `RAG_CHAT_MODEL` environment value, defaulting
to `gemma4-biomedical-e4b`. Model switching remains an operator-side LM Studio
operation; the backend should use this HTTP boundary rather than accessing
LM Studio directly.
