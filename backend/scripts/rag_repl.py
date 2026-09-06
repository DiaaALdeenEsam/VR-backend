"""Interactive command-line REPL for manually testing the RAG API's retrieval
endpoint (POST /v1/rag/query) -- a dev/testing tool, not a chat client: this
API has no LLM completion endpoint (confirmed against its live OpenAPI spec),
so there's no conversational reply here, just ranked evidence chunks for
whatever you type.

Reads RAG_API_BASE_URL / RAG_API_KEY from backend/.env the same way the app
does (via app.config.get_settings()), so it always matches whatever the app
itself would actually call. Run it from the backend/ directory with its venv
active:

    venv\\Scripts\\python.exe scripts\\rag_repl.py

Type a question (Arabic or English), press Enter, see the top results. Type
an empty line, "exit", or Ctrl+C to quit. Optionally restrict content types
for the rest of the session with ":types history,exam" (empty ":types" to
clear the restriction), or change how many results come back with ":topk N".
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Windows' default console codepage (cp1252/cp850/etc, not UTF-8) otherwise
# mangles Arabic input/output here -- reconfigure explicitly rather than
# relying on the caller to set PYTHONIOENCODING=utf-8 themselves. errors=
# "replace" so a genuinely undecodable byte shows as U+FFFD instead of
# crashing the REPL outright.
for _stream in (sys.stdin, sys.stdout):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.domain.exceptions import RagServiceUnavailableError  # noqa: E402
from app.infrastructure.rag_client_adapter import RagClientAdapter  # noqa: E402

DEFAULT_TOP_K = 5


def _print_results(results, query: str) -> None:
    if not results:
        print(f'  (no results for "{query}")\n')
        return

    for e in results:
        snippet = e.text.strip().replace("\n", " ")
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "..."
        print(f"  [{e.rank}] content_type={e.content_type!r} distance={e.distance:.4f}")
        if e.title or e.chapter or e.section:
            print(f"      {e.title or ''} | ch.{e.chapter or '?'} | {e.section or ''}")
        print(f"      {snippet}\n")


async def _main() -> None:
    settings = get_settings()
    if not settings.rag_api_base_url or not settings.rag_api_key:
        print("RAG_API_BASE_URL and/or RAG_API_KEY are not set in backend/.env -- nothing to talk to.")
        return

    adapter = RagClientAdapter(settings)
    top_k = DEFAULT_TOP_K
    content_types: list[str] | None = None

    print(f"Connected to {settings.rag_api_base_url} -- retrieval only, no LLM reply.")
    print("Type a question, or ':types a,b' / ':topk N' / 'exit'. Empty line also exits.\n")

    while True:
        try:
            query = input("query> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query or query.lower() == "exit":
            break

        if query.startswith(":types"):
            arg = query[len(":types") :].strip()
            content_types = [t.strip() for t in arg.split(",") if t.strip()] or None
            print(f"  content_types filter set to: {content_types}\n")
            continue

        if query.startswith(":topk"):
            arg = query[len(":topk") :].strip()
            if arg.isdigit():
                top_k = max(1, min(15, int(arg)))
            print(f"  top_k set to: {top_k}\n")
            continue

        try:
            results = await adapter.retrieve(query=query, top_k=top_k, content_types=content_types)
        except RagServiceUnavailableError as exc:
            print(f"  RAG API unavailable: {exc}\n")
            continue

        _print_results(results, query)


if __name__ == "__main__":
    asyncio.run(_main())
