# PatientSimClient — Unity C# Client

Generated with NSwag from the backend's live OpenAPI spec (`openapi.json`,
namespace `PatientSim.Api`, class `PatientSimClient`). Covers all 11 REST
endpoints of the Patient Simulation API. Build-verified with a throwaway
console project (compiled clean, 0 warnings/errors) and smoke-tested against
a live local server — see §2 below for the exact calls that were confirmed
working.

## 1. Adding this to a Unity project

1. Copy `PatientSimClient.cs` into your Unity project's `Assets/` (e.g.
   `Assets/Scripts/Api/PatientSimClient.cs`).
2. Install the **Newtonsoft Json** package via Unity's Package Manager:
   `com.unity.nuget.newtonsoft-json` (Window → Package Manager → search
   "Newtonsoft Json", or add
   `"com.unity.nuget.newtonsoft-json": "3.2.1"` to `Packages/manifest.json`).
   This is the only external dependency the generated client needs — it
   otherwise only uses `System.Net.Http` (BCL).
3. Instantiate and call it like any other C# class:
   ```csharp
   using var httpClient = new System.Net.Http.HttpClient();
   var client = new PatientSim.Api.PatientSimClient("http://<host>:8000", httpClient);
   var scenarios = await client.List_scenarios_scenarios_getAsync();
   ```

No API key/auth header is required — the backend has no inbound
authentication today (see the backend analysis from earlier in this
conversation).

## 2. Smoke-tested calls (verified against a live local server)

These three calls were run end-to-end against a real running
`uvicorn app.main:app` instance (real DB, real seeded scenario data) and
confirmed working:

| Call | Result |
|---|---|
| `Health_check_health_getAsync()` → `GET /health` | `{"status": "ok", "database": "ok"}` |
| `List_scenarios_scenarios_getAsync()` → `GET /scenarios` | Returned 5 real seeded scenarios (ids 1–5) |
| `Create_session_sessions_postAsync(new SessionCreate { Scenario_id = 1 })` → `POST /sessions` | Returned a real `session_id` (UUID), e.g. `01cb1b13-0816-48cf-99d5-198e2c2b0cec` |

The remaining 8 REST endpoints (`/sessions/{id}` review, `/sessions/{id}/tests`,
`/sessions/{id}/answers`, `/sessions/{id}/evaluate`, `/sessions/{id}/messages`,
`/test-categories`, `/test-categories/{id}/tests`,
`/scenarios/{id}/relevant-tests`, `/scenarios/{id}/questions`, `/transcribe`,
`/sessions/{id}/chat-voice`) were generated from the same spec and share the
same client/DTO patterns as the three above, but were not individually
smoke-tested — exercise them the same way if you need extra confidence
before relying on them.

## 3. ⚠️ `/ws/voice` is NOT included — must be hand-implemented

**The generated client does not cover the `/ws/voice` WebSocket endpoint.**
OpenAPI/NSwag has no representation for WebSockets, so this could not be
(and was not) generated or faked.

This matters more than it might sound: `POST /sessions/{id}/messages` and
`POST /sessions/{id}/evaluate` only ever return an immediate placeholder
(`status: "pending"`) — the real patient reply and the real evaluation
result are both delivered **later, over `/ws/voice?session_id=...`**. A
Unity client that only uses `PatientSimClient` and never opens that socket
will see every chat message and evaluation stay stuck at `"pending"`
forever.

Implement the WebSocket connection separately in Unity (e.g. with the
`NativeWebSocket` package — Unity has no built-in WS client on every
scripting backend/platform). The exact frame protocol — connect sequence,
frame `type`/`status` shapes, the `session_busy` vs. connection-closing error
distinction, reconnect behavior — is fully specified in
[`docs/unity-integration-guide.md`](../backend/docs/unity-integration-guide.md)
§5, which should be read before writing that part of the integration.
