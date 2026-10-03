"""EE integration hooks — called by core app/, implemented by app/ee/.

All callables default to no-ops so the platform runs without ee/ installed.
EE package registers its implementations at startup via register_*().
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

_analytics_handler: Optional[Callable] = None


def register_analytics(fn: Callable) -> None:
    global _analytics_handler
    _analytics_handler = fn


def get_analytics_handler() -> Optional[Callable]:
    return _analytics_handler


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_audit_handler: Optional[Callable] = None


def register_audit(fn: Callable) -> None:
    global _audit_handler
    _audit_handler = fn


def get_audit_handler() -> Optional[Callable]:
    return _audit_handler


# ---------------------------------------------------------------------------
# Compliance export
# ---------------------------------------------------------------------------

_compliance_export_handler: Optional[Callable] = None


def register_compliance_export(fn: Callable) -> None:
    global _compliance_export_handler
    _compliance_export_handler = fn


def get_compliance_export_handler() -> Optional[Callable]:
    return _compliance_export_handler
