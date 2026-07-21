"""Contract tests: antcrew shim modules re-export every public symbol from antcrew_engine.

Failure here means antcrew-engine changed its public API (added, renamed, or removed
a symbol) without the corresponding shim being updated. Caught before users who import
via antcrew.capabilities.* or antcrew.engine.* notice the breakage.

Two assertions per module pair:
  1. No missing symbols  — engine public API is fully re-exported by the shim.
  2. No shadowed symbols — shim objects are identical (is) to engine objects, not copies.
"""
from __future__ import annotations

import importlib
import importlib.util
import types

import pytest


def _public_names(mod: types.ModuleType) -> list[str]:
    """Return names that `import *` would bring, restricted to those defined in antcrew_engine.

    If the module defines __all__ we use that directly (explicit contract).
    Otherwise we infer: public names whose __module__ traces back to antcrew_engine,
    which excludes transitive stdlib/typing imports (Any, List, Optional, …).
    """
    if hasattr(mod, "__all__"):
        return list(mod.__all__)
    return [
        name for name in dir(mod)
        if not name.startswith("_")
        and getattr(getattr(mod, name, None), "__module__", "").startswith("antcrew_engine")
    ]


def _shim_exists(path: str) -> bool:
    return importlib.util.find_spec(path) is not None


# ---------------------------------------------------------------------------
# Module pairs: (antcrew shim path, antcrew_engine source path)
# ---------------------------------------------------------------------------

_CAPABILITIES = [
    "architect", "base", "bug_fixer", "code_generator", "code_regenerator",
    "code_reviewer", "dependency_installer", "doc_generator", "hitl_reviewer",
    "review_fixer", "spec_extractor", "task_planner", "team_executor",
    "test_generator", "test_runner", "validators",
]

_ENGINE = [
    "artifact", "bus_bridge", "capability", "events", "goal",
    "operator", "registry", "selector", "state", "store", "validator",
]

_PAIRS: list[tuple[str, str]] = [
    (f"antcrew.capabilities.{m}", f"antcrew_engine.capabilities.{m}")
    for m in _CAPABILITIES
    if _shim_exists(f"antcrew.capabilities.{m}") and _shim_exists(f"antcrew_engine.capabilities.{m}")
] + [
    (f"antcrew.engine.{m}", f"antcrew_engine.engine.{m}")
    for m in _ENGINE
    if _shim_exists(f"antcrew.engine.{m}") and _shim_exists(f"antcrew_engine.engine.{m}")
]

_IDS = [f"{s.split('.')[-1]}({s.split('.')[1]})" for s, _ in _PAIRS]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shim_path,engine_path", _PAIRS, ids=_IDS)
def test_shim_exports_all_engine_symbols(shim_path: str, engine_path: str) -> None:
    """Shim re-exports every public symbol defined in the engine module."""
    engine_mod = importlib.import_module(engine_path)
    shim_mod = importlib.import_module(shim_path)

    native = _public_names(engine_mod)
    if not native:
        pytest.skip(f"{engine_path} has no antcrew_engine-native public symbols")

    missing = [name for name in native if not hasattr(shim_mod, name)]
    assert not missing, (
        f"Shim {shim_path!r} is missing {len(missing)} symbol(s) from {engine_path!r}:\n"
        + "\n".join(f"  - {name}" for name in sorted(missing))
        + "\nThis usually means antcrew-engine added symbols without updating the shim."
    )


@pytest.mark.parametrize("shim_path,engine_path", _PAIRS, ids=_IDS)
def test_shim_symbols_are_same_objects(shim_path: str, engine_path: str) -> None:
    """Shim symbols are identical objects to engine symbols — not shadowed re-definitions."""
    engine_mod = importlib.import_module(engine_path)
    shim_mod = importlib.import_module(shim_path)

    native = _public_names(engine_mod)
    shadowed = [
        name for name in native
        if hasattr(shim_mod, name)
        and getattr(shim_mod, name) is not getattr(engine_mod, name)
    ]
    assert not shadowed, (
        f"Shim {shim_path!r} shadows {len(shadowed)} symbol(s) from {engine_path!r} "
        f"with different objects:\n"
        + "\n".join(f"  - {name}" for name in sorted(shadowed))
        + "\nThe shim defines its own version instead of re-exporting from the engine."
    )
