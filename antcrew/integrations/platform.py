"""PlatformChannel — BaseChannel implementation backed by antcrew-platform API.

When a local pipeline run reaches a HITL checkpoint, this channel:
  1. Registers the review on the platform via POST /reviews/
  2. Prints a link so reviewers can act via `antcrew review` CLI or the dashboard
  3. Polls GET /reviews/{review_id} until a decision arrives or the timeout expires
  4. Returns the decision dict, unblocking the pipeline

Configuration (in order of precedence):
  - Constructor args
  - ANTCREW_PLATFORM_URL / ANTCREW_PLATFORM_API_KEY environment variables
  - .antcrew/config.yaml in the working directory (written by `antcrew configure`)

Usage in agentteam.yaml:
    channel:
      type: platform
      # url: https://antcrew.example.com   # optional, defaults to ANTCREW_PLATFORM_URL
      # api_key: sk-...                    # optional, defaults to ANTCREW_PLATFORM_API_KEY
      # timeout_s: 3600
      # poll_interval_s: 2.0

Usage in code:
    from antcrew.integrations.platform import PlatformChannel
    channel = PlatformChannel(url="http://localhost:8000", api_key="sk-...")
    agent = BackendDevAgent(llm=..., channel=channel, approval_required=True)
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Optional

from antcrew.core.channel import BaseChannel

log = logging.getLogger(__name__)


def _serialize_artifact(artifact) -> str:
    """Convert any artifact to a JSON string, falling back to repr on failure."""
    try:
        if hasattr(artifact, "model_dump"):
            return json.dumps(artifact.model_dump(), default=str)
        if hasattr(artifact, "__dict__"):
            return json.dumps(artifact.__dict__, default=str)
        return json.dumps(artifact, default=str)
    except Exception:
        return json.dumps({"repr": repr(artifact)})


class PlatformChannel(BaseChannel):
    """HITL channel that delegates review decisions to antcrew-platform.

    Reviews are persisted in the platform DB, visible in the dashboard, and
    resolvable via the `antcrew review` CLI, the REST API, or Slack buttons
    (when the platform has Slack configured).

    Requires httpx (pip install httpx or pip install antcrew[platform]).
    Falls back to ConsoleChannel if the platform is unreachable.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 3600.0,
        poll_interval_s: float = 2.0,
    ) -> None:
        try:
            from antcrew.cli.configure_cmd import load_config
            cfg = load_config()
        except Exception:
            cfg = {}

        self._url = (
            url
            or os.environ.get("ANTCREW_PLATFORM_URL")
            or str(cfg.get("platform_url", ""))
            or "http://localhost:8000"
        ).rstrip("/")

        from antcrew.cli.configure_cmd import get_platform_api_key as _get_key
        self._api_key = (
            api_key
            or _get_key()
            or str(cfg.get("api_key", ""))
        )

        self._timeout_s = timeout_s
        self._poll_s = poll_interval_s
        self._run_id: Optional[str] = None
        self._workspace_id: Optional[int] = None
        self._headers: dict[str, str] = {}
        if self._api_key:
            self._headers["X-Api-Key"] = self._api_key

    def set_run_id(self, run_id: str) -> None:
        """Bind this channel to a specific run_id."""
        self._run_id = run_id

    def set_workspace_id(self, workspace_id: int) -> None:
        """Bind this channel to a workspace so stub Runs get the correct scope."""
        self._workspace_id = workspace_id

    async def notify(self, message: str, **kwargs) -> None:
        log.debug("platform: %s", message)

    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        response_options: Optional[list[str]] = None,
    ) -> dict:
        """Register the review on the platform and poll for a decision."""
        try:
            import httpx
        except ImportError:
            log.warning(
                "platform: httpx not installed — falling back to ConsoleChannel. "
                "Install with: pip install httpx"
            )
            from antcrew.integrations.console import ConsoleChannel
            return await ConsoleChannel().send_for_review(
                artifact, agent_name, session_id, response_options
            )

        review_id = str(uuid.uuid4())
        run_id = self._run_id or session_id
        options = response_options or ["approve", "edit", "reject"]
        artifact_json = _serialize_artifact(artifact)

        # --- register on platform ---
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=10.0) as client:
                payload: dict = {
                    "run_id": run_id,
                    "review_id": review_id,
                    "agent_name": agent_name,
                    "artifact_json": artifact_json,
                    "options": options,
                }
                if self._workspace_id is not None:
                    payload["workspace_id"] = self._workspace_id
                r = await client.post(f"{self._url}/reviews/", json=payload)
                r.raise_for_status()
        except Exception as exc:
            log.warning("platform: failed to register review (%s) — falling back to console", exc)
            from antcrew.integrations.console import ConsoleChannel
            return await ConsoleChannel().send_for_review(
                artifact, agent_name, session_id, response_options
            )

        print(
            f"\n  [platform] Review registered → {self._url}/reviews\n"
            f"  review_id : {review_id}\n"
            f"  agent     : {agent_name}\n"
            f"  resolve   : antcrew review  "
            f"OR  POST {self._url}/reviews/{review_id}\n"
            f"  waiting up to {int(self._timeout_s)}s …"
        )

        # --- poll for decision ---
        deadline = time.monotonic() + self._timeout_s
        import asyncio
        async with httpx.AsyncClient(headers=self._headers, timeout=10.0) as client:
            while time.monotonic() < deadline:
                await asyncio.sleep(self._poll_s)
                try:
                    r = await client.get(f"{self._url}/reviews/{review_id}")
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("status") not in ("pending", None):
                            decision = data.get("decision") or data["status"]
                            print(f"  [platform] Decision received: {decision}")
                            return {
                                "decision": decision,
                                "edited": data.get("edited_json"),
                                "feedback": data.get("feedback"),
                            }
                except Exception as exc:
                    log.debug("platform: poll error: %s", exc)

        log.warning("platform: HITL timeout (%.0fs) for review %s — auto-approving", self._timeout_s, review_id)
        print(f"  [platform] Timeout — auto-approving")
        return {"decision": "approve", "edited": None, "feedback": None}
