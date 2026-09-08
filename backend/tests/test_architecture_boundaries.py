"""Static checks that Clean Architecture's dependency direction actually holds.

These don't just document the rule in prose -- they fail the build the moment
someone imports an ML/HTTP/ORM library into domain/ or application/, which is
exactly the kind of regression that's easy to introduce by accident (e.g.
while wiring a new PatientReplyGenerator implementation) and easy to miss in
review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Anything infrastructure-flavored that domain/application must never import
# directly -- ML/audio stack, web framework, ORM/DB driver.
FORBIDDEN_TOP_LEVEL_IMPORTS = {
    "torch",
    "transformers",
    "accelerate",
    "faster_whisper",
    "gtts",
    "av",
    "ctranslate2",
    "whisper",
    "leva_tts",
    "soundfile",
    "fastapi",
    "starlette",
    "sqlmodel",
    "sqlalchemy",
    "aiosqlite",
    "alembic",
}

# Concrete infrastructure adapters that wrap a heavy ML/audio library --
# imported lazily *inside* their functions (so importing the module itself
# doesn't require the library to already be installed), which is why they
# won't show up in FORBIDDEN_TOP_LEVEL_IMPORTS scans of their own file. What
# LOCAL_ML_ADAPTER_MODULES below checks instead is that these modules stay in
# app/infrastructure/ and never reach sideways into the web framework or ORM.
LOCAL_ML_ADAPTER_MODULES = [
    "infrastructure/colab_stt_adapter.py",
    "infrastructure/colab_tts_adapter.py",
    "infrastructure/legacy/local_whisper_stt_adapter.py",
    "infrastructure/legacy/local_tts_adapter.py",
    "infrastructure/local_gpu_stt_adapter.py",
    "infrastructure/local_gpu_tts_adapter.py",
    "infrastructure/voice_models.py",
]


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _python_files(*subdirs: str) -> list[Path]:
    files: list[Path] = []
    for sub in subdirs:
        files.extend((APP_DIR / sub).rglob("*.py"))
    return files


@pytest.mark.parametrize("path", _python_files("domain", "application"), ids=lambda p: str(p.relative_to(APP_DIR)))
def test_domain_and_application_do_not_import_infrastructure_libs(path: Path) -> None:
    leaked = _top_level_imports(path) & FORBIDDEN_TOP_LEVEL_IMPORTS
    assert not leaked, f"{path.relative_to(APP_DIR)} imports forbidden infrastructure lib(s): {leaked}"


@pytest.mark.parametrize("relative_path", LOCAL_ML_ADAPTER_MODULES)
def test_local_ml_adapter_lives_in_infrastructure_and_stays_web_framework_free(relative_path: str) -> None:
    """The ML/audio stack is only allowed in app/infrastructure/ -- confirm it's placed there."""

    module_path = APP_DIR / relative_path
    assert module_path.exists(), f"expected {module_path} to exist"

    imports = _top_level_imports(module_path)
    assert imports.isdisjoint({"fastapi", "sqlmodel", "sqlalchemy"})
