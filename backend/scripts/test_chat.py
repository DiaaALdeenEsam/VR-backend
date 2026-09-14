"""
Test the full voice-chat flow: POST /sessions/{id}/chat-voice + WebSocket push.

Usage:
    python3 test_voice.py test.ogg                       # uses http://127.0.0.1:8000
    python3 test_voice.py test.ogg http://192.168.1.134:8000
"""

import sys
import json
import threading

import requests
import websocket

AUDIO_FILE = sys.argv[1] if len(sys.argv) > 1 else "test.ogg"
BASE_URL = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
WS_BASE = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")


def main():
    print(f"Using backend: {BASE_URL}")
    print(f"Using audio file: {AUDIO_FILE}\n")

    # 1. Get a scenario
    print("1) Fetching /scenarios ...")
    r = requests.get(f"{BASE_URL}/scenarios", timeout=5)
    r.raise_for_status()
    scenario_id = r.json()[0]["id"]
    print(f"   -> scenario_id={scenario_id}\n")

    # 2. Create a session
    print("2) Creating session ...")
    r = requests.post(f"{BASE_URL}/sessions", json={"scenario_id": scenario_id}, timeout=10)
    r.raise_for_status()
    session_id = r.json()["session_id"]
    print(f"   -> session_id={session_id}\n")

    # 3. Open WebSocket BEFORE sending audio
    ws_url = f"{WS_BASE}/ws/voice?session_id={session_id}"
    print(f"3) Opening WebSocket: {ws_url} ...")

    received = {"frames": []}
    ws_connected = threading.Event()
    got_final = threading.Event()

    def on_open(ws):
        print("   -> WebSocket connected!\n")
        ws_connected.set()

    def on_message(ws, message):
        try:
            data = json.loads(message)
        except Exception:
            data = message
        print(f"   [WS PUSH] {data}")
        received["frames"].append(data)
        if isinstance(data, dict) and data.get("type") != "thinking":
            got_final.set()

    def on_error(ws, error):
        print(f"   [WS ERROR] {error}")

    def on_close(ws, code, msg):
        print(f"   [WS CLOSED] code={code} msg={msg}")

    ws = websocket.WebSocketApp(
        ws_url, on_open=on_open, on_message=on_message,
        on_error=on_error, on_close=on_close,
    )
    wst = threading.Thread(target=ws.run_forever, daemon=True)
    wst.start()

    if not ws_connected.wait(timeout=10):
        print("   !! WebSocket did not connect within 10s. Aborting.")
        return

    # 4. Send the audio file to /chat-voice
    print(f"4) POST /sessions/{{id}}/chat-voice with {AUDIO_FILE} ...")
    with open(AUDIO_FILE, "rb") as f:
        r = requests.post(
            f"{BASE_URL}/sessions/{session_id}/chat-voice",
            files={"file": f},
            timeout=30,
        )
    print(f"   -> status {r.status_code}")
    try:
        resp_json = r.json()
        print(f"   -> body: {json.dumps(resp_json, indent=2, ensure_ascii=False)}\n")
    except Exception:
        print(f"   -> raw body: {r.text[:500]}\n")

    # 5. Wait for the real reply over WS
    print("5) Waiting for reply over WebSocket (up to 70s) ...")
    if got_final.wait(timeout=70):
        print("\n✅ Got a final push frame:")
    else:
        print("\n⚠️ No final frame arrived within 70s. Frames so far:")
    for f in received["frames"]:
        print("   -", f)

    # 6. Check if any frame carries audio data (url, base64, etc.)
    print("\n6) Checking whether any WS frame contains audio output ...")
    audio_keys_found = False
    for f in received["frames"]:
        if isinstance(f, dict):
            for key in f.keys():
                if any(term in key.lower() for term in ["audio", "voice", "tts", "sound", "url"]):
                    print(f"   -> Found potential audio field: '{key}' = {str(f[key])[:100]}")
                    audio_keys_found = True
    if not audio_keys_found:
        print("   -> No audio-related field found in WS frames (text-only reply).")
        print("      If your Unity colleague expects spoken audio back, check:")
        print("      docs/unity-integration-guide.md for the exact TTS delivery mechanism")
        print("      (e.g. a separate endpoint, a URL to fetch, or base64 in this same push).")

    # 7. Reconciliation check
    print("\n7) GET /sessions/{id} ...")
    r = requests.get(f"{BASE_URL}/sessions/{session_id}", timeout=10)
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))

    ws.close()


if __name__ == "__main__":
    main()