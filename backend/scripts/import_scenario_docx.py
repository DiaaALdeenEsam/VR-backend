"""Import clinical-case .docx files straight into the `scenarios` table.

This is the "drop the Word file in, don't type anything by hand" import path.
Point it at one file, several files, or a whole folder, and it will:

  1. Extract the paragraph text from each .docx (no python-docx dependency --
     a .docx is just a zip of word/document.xml, and we parse the <w:t> runs
     directly, which is all we need for plain paragraph text).
  2. Split each document into:
       - name          -> the specific case title (see _guess_name)
       - gold_standard -> the "Scoring / Success Criteria" section, if present
       - case_text     -> everything else (the full clinical case body)
  3. Insert a ScenarioModel row, unless a scenario with the same name already
     exists (then it's skipped, or updated in place with --update-existing).

Usage:
    python scripts/import_scenario_docx.py path/to/case.docx
    python scripts/import_scenario_docx.py path/to/folder_of_docs/
    python scripts/import_scenario_docx.py case1.docx case2.docx --dry-run
    python scripts/import_scenario_docx.py folder/ --update-existing

--dry-run prints exactly what would be written (name / gold_standard / a
preview of case_text) without touching the database -- use it the first time
on a new document template to sanity-check the split before trusting it.

Once this works for your case-file template, dropping new cases in becomes:
    python scripts/import_scenario_docx.py seed_data/incoming/
"""

from __future__ import annotations

import asyncio
import re
import sys
import zipfile
from pathlib import Path

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.models import ScenarioModel

# Headings (Arabic or English) that mark the start of the scoring/success-criteria
# section. Everything from the first matching paragraph to the end of the document
# is treated as gold_standard instead of case_text.
_GOLD_STANDARD_HEADINGS = (
    "معايير النجاح",
    "التقييم التلقائي",
    "Scoring",
    "Success Criteria",
)

_XML_TAG_TEXT = re.compile(r"<w:t(?:\s[^>]*)?(?<!/)>(.*?)</w:t>", re.S)
_XML_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_XML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def _unescape(text: str) -> str:
    for entity, char in _XML_ENTITIES.items():
        text = text.replace(entity, char)
    return text


def extract_paragraphs(docx_path: Path) -> list[str]:
    """Return the document's non-empty paragraphs, in order, as plain text."""
    with zipfile.ZipFile(docx_path) as z:
        xml = z.read("word/document.xml").decode("utf-8")

    paragraphs: list[str] = []
    for para_xml in _XML_PARAGRAPH.findall(xml):
        runs = _XML_TAG_TEXT.findall(para_xml)
        text = _unescape("".join(runs)).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _looks_like_generic_banner(paragraph: str) -> bool:
    """True for a generic cover line like 'سيناريو حالة سريرية لبرنامج المحاكاة
    الطبي التفاعلي' that repeats across every case file and isn't a real title."""
    generic_markers = ("سيناريو حالة سريرية", "برنامج المحاكاة")
    has_marker = any(m in paragraph for m in generic_markers)
    # A real title usually names the specific condition/case too (parenthetical
    # English name, a number, ...). A pure banner won't.
    return has_marker and "(" not in paragraph


def _guess_name(paragraphs: list[str]) -> str:
    if len(paragraphs) >= 2 and _looks_like_generic_banner(paragraphs[0]):
        return paragraphs[1]
    return paragraphs[0] if paragraphs else "Untitled scenario"


def split_scenario(paragraphs: list[str]) -> tuple[str, str, str | None]:
    """-> (name, case_text, gold_standard)"""
    name = _guess_name(paragraphs)

    split_at = next(
        (
            i
            for i, p in enumerate(paragraphs)
            if any(h.lower() in p.lower() for h in _GOLD_STANDARD_HEADINGS)
        ),
        None,
    )

    if split_at is None:
        return name, "\n".join(paragraphs), None

    case_text = "\n".join(paragraphs[:split_at])
    gold_standard = "\n".join(paragraphs[split_at:])
    return name, case_text, gold_standard


def collect_docx_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.docx")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"  ! not found, skipping: {p}")
    return paths


async def import_one(
    session: AsyncSession, path: Path, *, dry_run: bool, update_existing: bool
) -> None:
    paragraphs = extract_paragraphs(path)
    if not paragraphs:
        print(f"  ! {path.name}: no text found, skipping")
        return

    name, case_text, gold_standard = split_scenario(paragraphs)

    print(f"\n{path.name}")
    print(f"  name:          {name}")
    print(f"  gold_standard: {'(none found)' if gold_standard is None else gold_standard.splitlines()[0] + ' ...'}")
    preview = case_text[:160].replace("\n", " ")
    print(f"  case_text:     {preview}{'...' if len(case_text) > 160 else ''}")

    if dry_run:
        print("  (dry run -- not written)")
        return

    existing = (
        await session.exec(select(ScenarioModel).where(ScenarioModel.name == name))
    ).one_or_none()

    if existing is not None:
        if not update_existing:
            print(f"  = scenario {existing.id!r} already exists with this name, skipping "
                  f"(pass --update-existing to overwrite its text)")
            return
        existing.case_text = case_text
        existing.gold_standard = gold_standard
        session.add(existing)
        print(f"  -> updated scenario id={existing.id}")
        return

    row = ScenarioModel(name=name, case_text=case_text, gold_standard=gold_standard)
    session.add(row)
    await session.flush()
    print(f"  -> inserted scenario id={row.id}")


async def run(paths: list[Path], *, dry_run: bool, update_existing: bool) -> None:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        for path in paths:
            await import_one(session, path, dry_run=dry_run, update_existing=update_existing)
        if not dry_run:
            await session.commit()

    await engine.dispose()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic text prints correctly on Windows consoles

    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    update_existing = "--update-existing" in argv
    file_args = [a for a in argv if not a.startswith("--")]

    if not file_args:
        print(__doc__)
        raise SystemExit(1)

    paths = collect_docx_paths(file_args)
    if not paths:
        print("No .docx files found.")
        raise SystemExit(1)

    print(f"Found {len(paths)} .docx file(s){' [dry run]' if dry_run else ''}")
    asyncio.run(run(paths, dry_run=dry_run, update_existing=update_existing))
    print("\nDone." if not dry_run else "\nDry run complete -- nothing written.")


if __name__ == "__main__":
    main()
