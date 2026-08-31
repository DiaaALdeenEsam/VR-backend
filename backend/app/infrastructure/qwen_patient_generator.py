"""Local, lightweight PatientReplyGenerator backed by Qwen2.5-0.5B-Instruct.

Intended for development and testing: the model is small enough to run on CPU
in a few seconds, with no external API calls or keys required. This is a
concrete infrastructure implementation of the domain port
app.domain.repositories.PatientReplyGenerator -- nothing above the
infrastructure layer knows (or needs to know) that Hugging Face/PyTorch are
involved; swap this module out for a real hosted-LLM implementation later
without touching domain, application, or api code.

Loading strategy: the tokenizer/model are loaded lazily on first use and
cached at module level, guarded by a lock. A fresh QwenPatientReplyGenerator()
is cheap to construct (per-request, via app/api/deps.py) -- the actual
(relatively expensive, ~seconds) model load happens at most once per process.

Failure handling: nothing here ever raises out to the use case. If the model
or its dependencies can't be loaded (no network on first run to fetch the
weights, no disk space, out-of-memory, transformers/torch missing, ...) or a
generation attempt fails, this falls back to a clearly-labeled placeholder
reply instead -- so a doctor's chat session stays usable even when the local
ML stack isn't fully available on a given machine.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading

from app.domain.entities import Message, Scenario
from app.domain.repositories import PatientReplyGenerator

logger = logging.getLogger(__name__)

MODEL_NAME = "Qwen/Qwen2.5-0.5B-Instruct"
MAX_HISTORY_MESSAGES = 8
MAX_NEW_TOKENS = 96

SYSTEM_PROMPT_TEMPLATE = (
    "أنت تؤدي دور مريض حقيقي ضمن محاكاة تدريبية لطبيب متدرب. لديك الحالة السريرية التالية، "
    "وتجيب حصرًا من منظور المريض بما يتوافق معها -- ولا تكشفها أبدًا كتشخيص أو مصطلح طبي:\n"
    "{case_text}\n\n"
    "التزم دائمًا بهذه القواعد:\n"
    "- أجب باللغة العربية الفصحى حصرًا.\n"
    "- اجعل ردودك موجزة وطبيعية (جملة إلى ثلاث جمل كحد أقصى).\n"
    "- تحدّث بصيغة المتكلم، وكأنك المريض نفسه، لا راوٍ يصف حالته.\n"
    "- لا تفصح عن أي تشخيص أو مصطلح طبي لا يعرفه مريض عادي عادةً."
)

_load_lock = threading.Lock()
_tokenizer = None
_model = None
_load_failed = False


def _ensure_loaded() -> bool:
    """Loads the tokenizer/model once per process. Returns True iff ready to use."""

    global _tokenizer, _model, _load_failed

    if _model is not None and _tokenizer is not None:
        return True
    if _load_failed:
        return False

    with _load_lock:
        # Re-check inside the lock: another thread may have finished loading
        # (or failed) while we were waiting for it.
        if _model is not None and _tokenizer is not None:
            return True
        if _load_failed:
            return False

        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
            model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
            model.eval()
        except Exception:
            logger.exception(
                "Failed to load %s -- falling back to placeholder patient replies.", MODEL_NAME
            )
            _load_failed = True
            return False

        _tokenizer = tokenizer
        _model = model
        return True


def _build_chat(scenario: Scenario, history: list[Message], user_message: str) -> list[dict[str, str]]:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(case_text=scenario.case_text)
    chat: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    for m in history[-MAX_HISTORY_MESSAGES:]:
        role = "assistant" if m.role == "assistant" else "user"
        chat.append({"role": role, "content": m.content})

    chat.append({"role": "user", "content": user_message})
    return chat


def _fallback_reply(user_message: str) -> str:
    return f"[تعذّر توليد رد النموذج المحلي حاليًا] بخصوص سؤالك: {user_message}"


# At this model size, stray CJK characters occasionally leak into otherwise-Arabic
# output (a known quirk of small multilingual models briefly mixing scripts).
# Arabic replies never legitimately contain these, so strip them defensively.
_STRAY_CJK = re.compile(
    "["
    "一-鿿"  # CJK unified ideographs
    "぀-ヿ"  # hiragana/katakana
    "가-힣"  # hangul syllables
    "]+"
)


def _sanitize_reply(text: str) -> str:
    cleaned = _STRAY_CJK.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _generate_sync(scenario: Scenario, history: list[Message], user_message: str) -> str:
    """Blocking inference call -- always run this via asyncio.to_thread, never awaited directly."""

    if not _ensure_loaded():
        return _fallback_reply(user_message)

    assert _tokenizer is not None and _model is not None

    try:
        import torch

        chat = _build_chat(scenario, history, user_message)
        prompt = _tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = _tokenizer(prompt, return_tensors="pt")

        with torch.no_grad():
            output_ids = _model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=True,
                temperature=0.6,
                top_p=0.9,
                repetition_penalty=1.15,
                no_repeat_ngram_size=3,
                pad_token_id=_tokenizer.eos_token_id,
            )

        generated_ids = output_ids[0][inputs["input_ids"].shape[-1] :]
        raw_reply = _tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        reply = _sanitize_reply(raw_reply)
        return reply if reply else _fallback_reply(user_message)
    except Exception:
        logger.exception("Qwen generation failed -- falling back to placeholder reply.")
        return _fallback_reply(user_message)


class QwenPatientReplyGenerator(PatientReplyGenerator):
    """PatientReplyGenerator backed by a local Qwen2.5-0.5B-Instruct model.

    Cheap to construct -- construct a fresh one per request/use-case if
    convenient; the actual model stays cached at module level regardless.
    """

    async def generate_reply(
        self,
        scenario: Scenario,
        history: list[Message],
        user_message: str,
    ) -> str:
        # Model inference is CPU-bound and blocking; run it off the event loop
        # thread so it doesn't stall other requests being served concurrently.
        return await asyncio.to_thread(_generate_sync, scenario, history, user_message)
