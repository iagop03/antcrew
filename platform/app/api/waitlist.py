"""Public waitlist endpoint — captures early-access signups.

Entries are appended as JSON-lines to DATA_DIR/waitlist.jsonl.
No auth required; no CSRF (public endpoint, no authenticated session to forge).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["waitlist"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent.parent))
_WAITLIST_PATH = _DATA_DIR / "waitlist.jsonl"


class _WLPayload(BaseModel):
    email: str
    team_size: Optional[str] = ""
    use_case: Optional[str] = ""


@router.post("/api/waitlist", status_code=200)
async def join_waitlist(payload: _WLPayload) -> dict:
    email = payload.email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=422, detail="Invalid email address")

    entry = {
        "email": email,
        "team_size": (payload.team_size or "")[:30],
        "use_case": (payload.use_case or "")[:500],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _WAITLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _WAITLIST_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        log.info("waitlist: signup email=%s", email)
    except OSError as exc:
        log.error("waitlist: failed to persist entry: %s", exc)
    return {"ok": True}
