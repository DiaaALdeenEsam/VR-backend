"""Tests for the RAG-evidence curation step in qwen_patient_generator.py
(_curate_evidence / _format_evidence_block).

Deliberately separate from tests/test_qwen_patient_generator.py: these two
functions are pure and take no dependency on torch/transformers or the model
weights, so -- unlike the rest of that file -- these run everywhere, with no
pytest.importorskip / model-load skip needed. This is what
tests/test_qwen_patient_generator.py's own docstring means by tests that
should run "everywhere" for logic that doesn't actually need the model; the
curation/filtering logic itself qualifies, so it gets its own always-on
module instead of being gated behind the heavy-model skip.
"""

from __future__ import annotations

from app.domain.entities import Evidence
from app.infrastructure.qwen_patient_generator import (
    _MAX_EVIDENCE_CHARS_PER_ITEM,
    _MAX_EVIDENCE_ITEMS,
    _curate_evidence,
    _format_evidence_block,
)


def _evidence(
    id: str, rank: int, content_type: str | None, text: str = "نص تجريبي", distance: float = 0.1
) -> Evidence:
    return Evidence(id=id, rank=rank, text=text, distance=distance, content_type=content_type)


def test_allowed_content_types_pass_through_unchanged_in_type() -> None:
    items = [
        _evidence("e1", 1, "symptoms"),
        _evidence("e2", 2, "history"),
        _evidence("e3", 3, "exam"),
    ]

    curated = _curate_evidence(items)

    assert [e.id for e in curated] == ["e1", "e2", "e3"]
    assert {e.content_type for e in curated} == {"symptoms", "history", "exam"}


def test_disallowed_content_types_are_excluded() -> None:
    items = [
        _evidence("e1", 1, "diagnosis", text="التشخيص المرجّح هو التهاب الزائدة الدودية"),
        _evidence("e2", 2, "management", text="يوصى بالاستئصال الجراحي خلال 24 ساعة"),
        _evidence("e3", 3, "investigations", text="نتيجة تحليل الدم تظهر ارتفاعا في كريات الدم البيضاء"),
        _evidence("e4", 4, "symptoms", text="يشعر المريض بألم في البطن"),
    ]

    curated = _curate_evidence(items)

    assert [e.id for e in curated] == ["e4"]


def test_disallowed_content_never_reaches_the_formatted_block_text() -> None:
    """The excluded item's text must not leak into the prompt block at all --
    not just be excluded from the curated list, but genuinely absent from the
    string that gets folded into the model prompt."""

    diagnostic_text = "التشخيص المؤكد هو التهاب الكبد الفيروسي من النوع ب"
    items = [
        _evidence("e1", 1, "diagnosis", text=diagnostic_text),
        _evidence("e2", 2, "symptoms", text="يشعر المريض بتعب عام"),
    ]

    block = _format_evidence_block(items)

    assert diagnostic_text not in block
    assert "يشعر المريض بتعب عام" in block


def test_unrecognized_or_missing_content_type_is_excluded_by_default_deny() -> None:
    items = [
        _evidence("e1", 1, "other"),
        _evidence("e2", 2, None),
        _evidence("e3", 3, "symptoms"),
    ]

    curated = _curate_evidence(items)

    assert [e.id for e in curated] == ["e3"]


def test_curation_sorts_by_rank_and_caps_item_count() -> None:
    items = [_evidence(f"e{i}", rank=i, content_type="history") for i in range(10, 0, -1)]

    curated = _curate_evidence(items)

    assert len(curated) == _MAX_EVIDENCE_ITEMS
    assert [e.rank for e in curated] == sorted(e.rank for e in curated)
    assert [e.rank for e in curated] == list(range(1, _MAX_EVIDENCE_ITEMS + 1))


def test_formatted_block_truncates_each_item_to_the_char_cap() -> None:
    long_text = "أ" * (_MAX_EVIDENCE_CHARS_PER_ITEM + 200)
    items = [_evidence("e1", 1, "symptoms", text=long_text)]

    block = _format_evidence_block(items)

    assert long_text not in block
    assert ("أ" * _MAX_EVIDENCE_CHARS_PER_ITEM) in block


def test_empty_evidence_list_produces_no_block() -> None:
    """No evidence at all -- the prompt must be identical to the pre-RAG
    prompt (no evidence section appended)."""

    assert _format_evidence_block([]) == ""


def test_all_items_filtered_out_falls_back_to_no_block_same_as_empty_input() -> None:
    """Nothing usable survives curation (e.g. RAG only returned diagnosis/
    management/investigations) -- must degrade exactly like "no evidence
    retrieved at all", not error or half-fill the prompt."""

    items = [
        _evidence("e1", 1, "diagnosis"),
        _evidence("e2", 2, "management"),
        _evidence("e3", 3, "investigations"),
    ]

    assert _format_evidence_block(items) == _format_evidence_block([]) == ""
