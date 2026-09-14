"""DESTRUCTIVE one-off script: deletes every row from every table in the
database except a single, explicitly-named survivor scenario row.

Data-only. Never touches table structure, migrations, or `alembic_version`
-- `alembic current`/`alembic upgrade head` must report the exact same head
revision before and after running this (checked by the caller, not this
script, since checking it requires shelling out to `alembic`, which this
script deliberately keeps out of scope -- see the module docstring's "what
this does NOT do" note below).

Survivor: the one row in `scenarios` where name == SURVIVOR_NAME (expected
id == SURVIVOR_EXPECTED_ID). Aborts with no changes made if that row isn't
found, isn't unique, or has an unexpected id -- this script refuses to guess
which row you meant to keep.

What this does NOT do (by design, out of scope for a data-only wipe):
  - Does not run `alembic upgrade`/`downgrade` -- schema stays exactly as-is.
  - Does not touch the `alembic_version` table.
  - Does not re-seed anything afterward.
  - Does not assume the 16 case_* tables (migration 0006) exist -- if this
    database is still on an earlier revision (checked live, not hardcoded),
    those tables are reported as "does not exist yet", not silently skipped
    as if already empty.

Safety model:
  - Dry run by default: prints exactly what WOULD be deleted, touches
    nothing. Pass --yes to actually execute.
  - Deletes in explicit dependency order (children before parents) with the
    connection's `PRAGMA foreign_keys = ON` for the duration of the delete
    transaction -- if the ordering below were ever wrong, this makes SQLite
    raise an IntegrityError immediately instead of silently succeeding in a
    way that would only be safe by accident.
  - Runs inside a single transaction; any failure rolls back everything.
  - Captures the survivor row's full content (id, name, case_text,
    gold_standard) before touching anything, and re-reads it after, so the
    "untouched byte-for-byte" claim is verified programmatically, not
    asserted from memory.

Usage:
    python scripts/wipe_all_except_asthma.py            # dry run (default)
    python scripts/wipe_all_except_asthma.py --yes       # actually execute
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import get_settings

SURVIVOR_NAME = "Asthma"
SURVIVOR_EXPECTED_ID = 6

# --- table inventory ---------------------------------------------------
#
# Deletion order matters: every table listed here is deleted only after
# every table that references it (as a foreign key) has already been
# cleared above it in this same list. `scenarios` is handled specially
# (last, partial delete) and is not in this list.
#
# Grouped/commented to match the project's own documented layers (see
# docs/scenario-clinical-schema-mapping.md) purely for readability -- the
# actual delete loop just walks this one flat list top to bottom.
DELETE_ORDER: list[str] = [
    # --- core quiz/session tables (migration 0001), deepest dependents first
    "answers",  # -> sessions, questions, choices
    "ordered_tests",  # -> sessions, tests
    "messages",  # -> sessions
    "choices",  # -> questions
    "questions",  # -> scenarios
    "sessions",  # -> scenarios
    "scenario_test_results",  # -> scenarios AND tests (migration 0004) -- must precede `tests`
    "tests",  # -> test_categories
    "test_categories",
    # --- 21 disease-reference tables (migration 0002) -- all -> scenarios only,
    # no inter-dependencies among themselves, so relative order doesn't matter
    "scenario_pathophysiology",
    "scenario_risk_factors",
    "scenario_risk_factor_items",
    "scenario_symptoms_signs",
    "scenario_symptom_items",
    "scenario_clinical_sign_items",
    "scenario_atypical_presentation_items",
    "scenario_lab_investigations",
    "scenario_radiological_investigations",
    "scenario_differential_diagnoses",
    "scenario_diagnostic_criteria",
    "scenario_diagnostic_criteria_items",
    "scenario_complications",
    "scenario_prevention",
    "scenario_prevention_items",
    "scenario_non_pharma_treatment",
    "scenario_non_pharma_items",
    "scenario_pharma_treatment",
    "scenario_pharma_drug_items",
    "scenario_drugs_to_avoid_items",
    "scenario_interventional_treatments",
    # --- derived patient profile (migration 0003) -- also -> scenarios only
    "scenario_patient_profile",
    # --- 16 patient-case simulation tables (migration 0006) -- may not exist
    # yet on this database (checked live below, not assumed) -- all -> scenarios only
    "case_patient_identity",
    "case_presenting_complaint",
    "case_associated_symptoms",
    "case_past_medical_history",
    "case_current_medications",
    "case_triggers",
    "case_family_social_history",
    "case_vital_signs",
    "case_physical_exam_findings",
    "case_investigations",
    "case_severity_criteria",
    "case_warning_signs",
    "case_management_phases",
    "case_disposition_criteria",
    "case_discharge_plan",
    "case_learning_objectives",
]

# Never touched by this script under any circumstances.
NEVER_TOUCH = {"alembic_version", "sqlite_sequence", "sqlite_master"}


def _sqlite_path_from_url(url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        raise SystemExit(
            f"This script only supports sqlite+aiosqlite:// database URLs (data-only, direct-sqlite3 "
            f"deletion) -- got {url!r}. Refusing to guess how to connect to anything else."
        )
    return Path(url[len(prefix) :])


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _all_tables(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _row_count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 -- table names are our own fixed list, not user input


def _find_survivor(con: sqlite3.Connection) -> tuple[int, str, str, str | None]:
    rows = con.execute(
        "SELECT id, name, case_text, gold_standard FROM scenarios WHERE name = ?", (SURVIVOR_NAME,)
    ).fetchall()
    if not rows:
        raise SystemExit(f"No scenario with name={SURVIVOR_NAME!r} found -- aborting, nothing touched.")
    if len(rows) > 1:
        ids = [r[0] for r in rows]
        raise SystemExit(
            f"Found {len(rows)} scenarios with name={SURVIVOR_NAME!r} (ids={ids}) -- ambiguous, "
            "aborting, nothing touched."
        )
    survivor_id, name, case_text, gold_standard = rows[0]
    if survivor_id != SURVIVOR_EXPECTED_ID:
        raise SystemExit(
            f"Scenario named {SURVIVOR_NAME!r} has id={survivor_id}, expected {SURVIVOR_EXPECTED_ID} -- "
            "aborting rather than deleting around an id I wasn't told to expect."
        )
    return survivor_id, name, case_text, gold_standard


def _print_table(rows: list[tuple], headers: tuple[str, ...]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def _snapshot(con: sqlite3.Connection, survivor_id: int) -> list[dict]:
    """One row per known table: whether it exists, its current count, and
    (for `scenarios` and any FK'd-to-scenarios table) how many of those rows
    belong to scenarios OTHER than the survivor -- purely informational,
    matches the task's request to confirm what references what before
    deleting it regardless."""

    snapshot = []
    existing = set(_all_tables(con))

    # scenarios itself, handled first/specially
    total = _row_count(con, "scenarios")
    to_delete = total - 1  # the survivor always stays
    snapshot.append(
        {"table": "scenarios", "exists": True, "total_rows": total, "rows_to_delete": to_delete, "survives": 1}
    )

    for table in DELETE_ORDER:
        if table not in existing:
            snapshot.append(
                {"table": table, "exists": False, "total_rows": None, "rows_to_delete": None, "survives": None}
            )
            continue
        total = _row_count(con, table)
        # Informational only (per the task): how many of this table's rows
        # reference the survivor scenario, where that's even meaningful.
        referencing_survivor = None
        if table in ("questions", "sessions") :
            referencing_survivor = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE scenario_id = ?", (survivor_id,)  # noqa: S608
            ).fetchone()[0]
        elif table == "scenario_test_results" or table.startswith("scenario_") or table.startswith("case_"):
            if _table_exists(con, table):
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
                if "scenario_id" in cols:
                    referencing_survivor = con.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE scenario_id = ?", (survivor_id,)  # noqa: S608
                    ).fetchone()[0]
        snapshot.append(
            {
                "table": table,
                "exists": True,
                "total_rows": total,
                "rows_to_delete": total,  # every row in every non-scenarios table is deleted, unconditionally
                "referencing_survivor": referencing_survivor,
                "survives": 0,
            }
        )
    return snapshot


def _print_snapshot(snapshot: list[dict], title: str) -> None:
    print(f"\n{title}")
    rows = []
    for entry in snapshot:
        if not entry["exists"]:
            rows.append((entry["table"], "N/A (table does not exist -- migration not applied)", "-", "-"))
            continue
        ref = entry.get("referencing_survivor")
        ref_str = str(ref) if ref is not None else "-"
        rows.append((entry["table"], str(entry["total_rows"]), str(entry["rows_to_delete"]), ref_str))
    _print_table(rows, ("table", "current_rows", "rows_to_delete", "of_which_reference_survivor"))


def run(execute: bool) -> None:
    settings = get_settings()
    db_path = _sqlite_path_from_url(settings.database_url)
    if not db_path.is_file():
        raise SystemExit(f"Database file not found: {db_path}")

    print(f"Database: {db_path} (from DATABASE_URL={settings.database_url!r})")

    con = sqlite3.connect(str(db_path))
    try:
        actual_tables = set(_all_tables(con))
        expected_known = set(DELETE_ORDER) | {"scenarios"} | NEVER_TOUCH
        unexpected = actual_tables - expected_known
        if unexpected:
            raise SystemExit(
                f"Unexpected table(s) found that this script doesn't know about: {sorted(unexpected)} -- "
                "aborting rather than silently leaving them untouched or guessing whether to wipe them. "
                "Update DELETE_ORDER (or NEVER_TOUCH) in this script first."
            )

        survivor_id, survivor_name, survivor_case_text, survivor_gold_standard = _find_survivor(con)
        print(f"\nSurvivor confirmed: scenarios.id={survivor_id}, name={survivor_name!r}")
        print("This row -- and only this row -- will remain in `scenarios`. Every other table's rows,")
        print("in every table listed below, will be deleted in full. `alembic_version` is never touched.")

        before = _snapshot(con, survivor_id)
        _print_snapshot(before, "=== BEFORE (dry run -- nothing has been touched yet) ===" if not execute else "=== BEFORE ===")

        total_to_delete = sum(e["rows_to_delete"] for e in before if e["exists"])
        print(f"\nTotal rows that would be deleted across all tables: {total_to_delete}")

        if not execute:
            print("\nDRY RUN ONLY -- no changes made. Re-run with --yes to actually execute this deletion.")
            return

        print("\n--yes given -- executing deletion now.")

        con.execute("PRAGMA foreign_keys = ON")
        con.execute("BEGIN")
        try:
            for table in DELETE_ORDER:
                if not _table_exists(con, table):
                    continue
                con.execute(f"DELETE FROM {table}")  # noqa: S608 -- fixed table list, not user input

            con.execute("DELETE FROM scenarios WHERE id != ?", (survivor_id,))

            # Reset sqlite_sequence for every table just cleared, EXCEPT scenarios
            # (so the survivor keeps id=6 and the next insert continues from 7).
            # This project's tables don't declare AUTOINCREMENT (confirmed: no
            # sqlite_sequence table exists as of migration 0005), so this is a
            # no-op today -- kept for correctness if that ever changes, per the
            # explicit instruction to reset it for every cleared table.
            if _table_exists(con, "sqlite_sequence"):
                for table in DELETE_ORDER:
                    con.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
                print("sqlite_sequence entries reset for all cleared tables (scenarios' entry left untouched).")
            else:
                print("No sqlite_sequence table exists in this database (no AUTOINCREMENT columns) -- ")
                print("nothing to reset; SQLite's own rowid allocation already continues from max(id)+1="
                      f"{survivor_id + 1} for `scenarios` and from 1 for every emptied table.")

            con.commit()
        except Exception:
            con.rollback()
            print("\nERROR during deletion -- transaction rolled back, database unchanged.", file=sys.stderr)
            raise

        print("\nDeletion committed.")

        after = _snapshot(con, survivor_id)
        _print_snapshot(after, "=== AFTER ===")

        # --- verification -------------------------------------------------
        problems = []
        for entry in after:
            if not entry["exists"]:
                continue
            if entry["table"] == "scenarios":
                if entry["total_rows"] != 1:
                    problems.append(f"scenarios has {entry['total_rows']} rows, expected exactly 1")
            elif entry["total_rows"] != 0:
                problems.append(f"{entry['table']} has {entry['total_rows']} rows, expected 0")

        row = con.execute(
            "SELECT id, name, case_text, gold_standard FROM scenarios WHERE id = ?", (survivor_id,)
        ).fetchone()
        if row is None:
            problems.append("survivor row is GONE after deletion")
        else:
            after_id, after_name, after_case_text, after_gold_standard = row
            unchanged = (
                after_id == survivor_id
                and after_name == survivor_name
                and after_case_text == survivor_case_text
                and after_gold_standard == survivor_gold_standard
            )
            print(f"\nSurvivor row byte-for-byte unchanged vs. before deletion: {unchanged}")
            if not unchanged:
                problems.append("survivor row's content changed during deletion")

            print(f"\n=== Survivor scenario (id={after_id}) -- full content, post-deletion ===")
            print(f"name: {after_name!r}")
            print("\n--- case_text (full) ---")
            print(after_case_text)
            print("\n--- gold_standard (full) ---")
            print(after_gold_standard)

        alembic_version_row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        print(f"\nalembic_version table (untouched by this script): {alembic_version_row}")

        if problems:
            print("\n*** VERIFICATION PROBLEMS FOUND: ***")
            for p in problems:
                print(f"  - {p}")
            raise SystemExit(1)

        print("\nVerification passed: every table is empty except `scenarios`, which has exactly 1 row")
        print(f"(id={survivor_id}, name={survivor_name!r}), content byte-for-byte unchanged.")

    finally:
        con.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--yes", action="store_true", help="Actually execute the deletion. Without this, only a dry run is printed."
    )
    args = parser.parse_args()
    run(execute=args.yes)


if __name__ == "__main__":
    main()
