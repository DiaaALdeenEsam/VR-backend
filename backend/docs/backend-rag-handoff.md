# Backend RAG API Handoff

For the complete actual-project flow, use the [Backend VR Runtime API
Handoff](backend-vr-runtime-handoff.md). This page covers the direct chapter
RAG/AI service, including its recommended stateless chat operation. It is one
part of the backend integration, not an audio-only interface: the backend may
use its responses for dialogue orchestration, UI/game events, case flow, and
other application behavior in addition to sending selected text to TTS.

Status: ready for integration testing. The remote service and temporary nport
public tunnel were verified on 2026-09-06.

## Connection

Base URL on the project LAN:

```text
http://10.20.0.100:8010
```

Temporary backend-test URL through nport:

```text
https://vr-rag-api.nport.link
```

The nport URL is a temporary public integration tunnel to remote port `8010`,
maintained by the remote user service `vr-rag-nport.service`. It has a
server-controlled lease and should be rechecked before each test session.

Interactive API documentation and the machine-readable schema are available
at `/docs` and `/openapi.json` on that base URL. The backend team must receive
the query key through its approved secret-sharing channel. Do not put the key
in Git, Plane, source code, tickets, or chat.

The LAN route and the temporary nport route are both verified. The backend team
should use HTTPS through nport for the current external test. Tailscale and
production network reachability remain unverified.

For nport requests, send an explicit application `User-Agent` such as
`VR-Project-Backend/1.0`; the temporary edge may reject Python's default
`Python-urllib` signature with HTTP 403. This is specific to the temporary
tunnel and is not an API authentication response.

## Choose the service by feature

The direct RAG API is the focused authenticated AI boundary. The full runtime
is the current legacy application boundary for persisted sessions and training:

| Backend feature | Service and route | Notes |
| --- | --- | --- |
| Clinician/doctor grounded answer | `8010 POST /v1/rag/chat` | Stateless; send the complete history |
| Direct Syrian-Arabic patient role-play | `8010 POST /v1/rag/patient/chat` | Stateless; send history, persona, and case query |
| Retrieval-only diagnostics | `8010 POST /v1/rag/query` | Optional; the backend owns prompt and role policy |
| Roles, personas, chapters, and cases | `8000 GET /api/roles`, `/api/chapters`, `/api/cases` | LAN-only full runtime |
| Persisted free conversation | `8000 POST /api/sessions`, `/api/chat` | Session ID is maintained by the full runtime |
| OSCE training, milestones, scores, feedback | `8000 /api/training/*` | Five-phase legacy training lifecycle |

Use `8010` when the backend wants a deliberate stateless AI/RAG call. Use
`8000` when it needs the current persisted case and training behavior. Do not
reconstruct patient disclosure, phase progression, scoring, or feedback from
raw `/v1/rag/query` results.

## Text and audio integration boundary

```text
STT audio -> backend transcript/history
  -> clinician/doctor: POST /v1/rag/chat -> TTS audio
  -> virtual patient:  POST /v1/rag/patient/chat -> TTS audio
```

This service accepts text only. It does not accept audio, perform speech to
text (STT), or perform text to speech (TTS). TTS is one output consumer; the
backend may also use the response text, speaker role, grounding metadata, and
application state to drive VR events and UI behavior. For direct RAG routes,
the backend keeps the conversation history and sends it on every request. The
API retrieves against the latest user turn, generates the role-appropriate
answer, and returns it in `choices[0].message.content`.

For Arabic STT, set `response_language: "ar"`; for English, set
`response_language: "en"`. `auto` is available, but an explicit STT/UX locale
is more reliable than script inference. The answer language is enforced
independently from Arabic retrieval translation.

`/v1/rag/chat` is clinician-facing and intentionally sounds like a medical
assistant. Do not use it as the virtual-patient backend. For a direct port-8010
patient test, use `/v1/rag/patient/chat`, which applies the existing
persona-based patient prompt, sanitized patient context, and anti-leak
controls. For actual selected-case persistence and OSCE behavior, use the full
runtime API described in [backend-vr-runtime-handoff.md](backend-vr-runtime-handoff.md).

## Chat request and response

```bash
curl -sS -X POST "$RAG_API_BASE_URL/v1/rag/chat" \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: VR-Project-Backend/1.0' \
  -H "X-API-Key: $RAG_API_KEY" \
  -d '{
    "messages":[
      {"role":"user","content":"مرحبا، شو أعراض التهاب الكبد؟"},
      {"role":"assistant","content":"...previous Arabic answer..."},
      {"role":"user","content":"طيب وشو الفحوصات المطلوبة؟"}
    ],
    "top_k":5,
    "content_types":["investigations"],
    "response_language":"ar"
  }'
```

The response is OpenAI-compatible at the top level:

```json
{
  "object": "chat.completion",
  "model": "gemma4-biomedical-e4b",
  "response_language": "ar",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "<text for TTS>"}, "finish_reason": "stop"}
  ],
  "retrieval": {"query": "<latest user turn>", "count": 5, "results": ["<evidence>"]}
}
```

The API is stateless: do not send only the newest message on a follow-up.
Include prior `user` and `assistant` turns, and make the final message the new
`user` turn. System messages are rejected so the service can retain its
grounding and language controls. Limits are 24 messages, 4,000 characters per
message, and 24,000 characters total.

## Patient-role chat request and response

Use this route when the backend needs the RAG API itself to produce the
virtual-patient reply:

```bash
curl -sS -X POST "$RAG_API_BASE_URL/v1/rag/patient/chat" \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: VR-Project-Backend/1.0' \
  -H "X-API-Key: $RAG_API_KEY" \
  -d '{
    "messages":[
      {"role":"user","content":"مرحبا، شو عم تحس اليوم؟"}
    ],
    "persona":"middle_man",
    "case_query":"التهاب الكبد",
    "top_k":5,
    "response_language":"ar"
  }'
```

Required patient fields are:

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| `messages` | object array | yes | Same 1–24 message history contract; final message is `user` |
| `persona` | string | no | One of the eight runtime personas; default `middle_man` |
| `case_query` | string | yes | 1–2,000 characters identifying the case context; send the same value on each stateless turn |
| `top_k` | integer | no | 1–15; default `5`; patient context internally uses only patient-safe content types |
| `response_language` | string | no | Must be `ar` for this route |

The response keeps the common `choices[0].message.content` field for TTS and
adds `speaker_role: "patient"`:

```json
{
  "object":"chat.completion",
  "model":"gemma4-biomedical-e4b",
  "speaker_role":"patient",
  "response_language":"ar",
  "choices":[
    {"message":{"role":"assistant","content":"<Syrian-Arabic patient reply>"}}
  ],
  "grounding":{
    "query":"التهاب الكبد",
    "collection":"medical_chapters",
    "count":2,
    "sources":[
      {"rank":1,"id":"<source id>","content_type":"symptoms","distance":0.214}
    ]
  }
}
```

The patient route never returns retrieved chunk text or unrestricted metadata.
The backend must not expose `grounding` to the trainee/patient; it is a
diagnostic summary for the integration layer. It is still stateless, so append
the previous patient reply and the next user turn to `messages` on each call.

The patient response uses `message.role: "assistant"` for the common completion
schema. Treat the top-level `speaker_role: "patient"` as the authoritative
speaker identity when routing the response to VR/UI behavior or TTS.

The direct patient route is a primitive RAG integration boundary: `case_query`
is a request for sanitized textbook-derived patient context, not a persisted
structured case identifier. Use the full runtime for the current selected-case
and OSCE lifecycle.

## Smoke test

Health and readiness do not require a key:

```bash
export RAG_API_BASE_URL='https://vr-rag-api.nport.link'
curl "$RAG_API_BASE_URL/healthz"
curl "$RAG_API_BASE_URL/readyz"
```

Expected readiness fields include `status: "ready"`, collection
`medical_chapters`, and `chunks: 9050`.

An authenticated direct retrieval request (optional lower-level operation):

```bash
export RAG_API_KEY='<receive securely from the service owner>'
curl -X POST "$RAG_API_BASE_URL/v1/rag/query" \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: VR-Project-Backend/1.0' \
  -H "X-API-Key: $RAG_API_KEY" \
  -d '{
    "query": "ما هي فحوصات مريض التهاب الكبد؟",
    "top_k": 5,
    "content_types": ["investigations"]
  }'
```

`Authorization: Bearer $RAG_API_KEY` is also accepted. Never log the key or
the complete retrieved `text` field in production request logs.

## Direct retrieval request and response

`POST /v1/rag/query` accepts:

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| `query` | string | yes | 1–2,000 non-whitespace characters |
| `top_k` | integer | no | 1–15; default 5 |
| `content_types` | string array | no | Up to 10 non-empty metadata values |

The current corpus uses content-type values such as `history`, `symptoms`,
`exam`, `investigations`, `diagnosis`, `management`, and `other`. Omit the
filter when the required value is unknown; an unmatched filter returns no
evidence rather than falling back to unrestricted retrieval.

Response shape:

```json
{
  "query": "treatment for hepatitis B",
  "collection": "medical_chapters",
  "count": 1,
  "results": [
    {
      "rank": 1,
      "id": "23#treatment-04",
      "text": "<retrieved evidence chunk>",
      "metadata": {
        "chapter": "23",
        "section": "Treatment",
        "content_type": "management",
        "source_file": "23.pdf",
        "title": "<chapter title>"
      },
      "distance": 0.214
    }
  ]
}
```

Results are ranked best-first. Lower `distance` is better; it is not a
confidence score. Preserve `metadata` and `id` when passing evidence to the
answer-generation layer so the response remains traceable to its source.

## Client behavior

The backend should use a bounded request timeout, retry only transient `503`
failures, and avoid retrying `401` or `422` responses. For the current
synchronous model, start with a 5-second connect timeout and a 150-second read
timeout for direct chat. The full runtime's scoring and feedback operations
may require at least 180 seconds. Tune these values after measuring the
backend's complete application flow, including STT and TTS when used.

| Status | Detail code / meaning | Backend action |
| --- | --- | --- |
| `200` | Chat answer or ranked evidence returned | Send `choices[0].message.content` to TTS; retain provenance only for backend diagnostics |
| `401` | `INVALID_API_KEY` | Fix secret injection; do not retry repeatedly |
| `422` | Invalid request body | Correct payload validation |
| `503` | RAG or patient/clinician chat dependency failure | Show a controlled fallback or retry with backoff according to the route-specific code |

For `/v1/rag/chat`, retry only transient `503` responses. In particular,
`RAG_CHAT_TIMEOUT`, `RAG_CHAT_EMPTY`, and `RAG_CHAT_DEPENDENCY_ERROR` can be
retried with bounded exponential backoff; do not retry `401`, `422`, or
`RAG_LANGUAGE_MISMATCH` blindly. The service itself retries one transient LM
Studio `400 {"error":"terminated"}` generation before returning a dependency
failure.

For `/v1/rag/patient/chat`, retry `PATIENT_CHAT_TIMEOUT` and
`PATIENT_CHAT_DEPENDENCY_ERROR` with bounded backoff. Treat
`PATIENT_LANGUAGE_MISMATCH`, `PATIENT_CHAT_EMPTY`, and invalid/authentication
responses as controlled fallback conditions. The direct retrieval operation
has no pagination. None of these operations exposes ChromaDB or LM Studio to
the backend. The canonical contract and deployment details are in
[rag-api.md](rag-api.md); this document is the quick-start handoff for
integration testing. For full application behavior, follow the separate
[Backend VR Runtime API Handoff](backend-vr-runtime-handoff.md).

## Verification record

On 2026-09-02 the remote RAG and nport user services reported active. Through
the public URL, `/healthz` returned `200`, `/readyz` returned `200` with 9,050
chunks, an unauthenticated query returned `401`, and an authenticated filtered
query returned `200` with the documented response fields. On 2026-09-06 the
deployed service passed the real-like chat harness through both loopback and
the public nport URL: English single-turn, Arabic auto-language, and Arabic
multi-turn follow-up with an investigations filter passed 5/5 in each run.
The patient-role harness passed two Arabic multi-turn checks through loopback
and two through nport; replies were Arabic-dominant, marked as `patient`, and
did not expose unrestricted retrieval text. The full VR-25 retrieval contract
harness passed 38/38 checks after the patient route was added, including
patient-route authentication and validation failures. These checks verify
service, contract, role routing, and language handling; they do not replace
clinical evaluation or full application/OSCE integration validation.
