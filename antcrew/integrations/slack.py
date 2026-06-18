"""
SlackChannel — BaseChannel implementation for Slack.

Uses Slack Block Kit for rich artifact display, interactive buttons for
approve / reject / edit / feedback, and Slack modals for text input.
Requires Socket Mode (no public URL needed).

Installation:
    pip install "antcrew[slack]"
    # or: pip install slack-sdk slack-bolt

Slack app setup:
    1. Create a Slack app at https://api.slack.com/apps
    2. Enable Socket Mode → generate an App-Level Token (xapp-…)
    3. Add Bot Token Scopes: chat:write, chat:write.public, im:write
    4. Enable Interactivity & Shortcuts (required for buttons and modals)
    5. Install the app to your workspace → copy Bot Token (xoxb-…)
    6. Invite the bot: /invite @YourBotName

Usage:
    channel = SlackChannel(
        bot_token=os.environ["SLACK_BOT_TOKEN"],   # xoxb-…
        app_token=os.environ["SLACK_APP_TOKEN"],   # xapp-… (Socket Mode)
        channel_id="C0XXXXXXXXX",
    )
    agent = BackendDevAgent(
        llm=AnthropicModel(),
        channel=channel,
        approval_required=True,
    )
    team = DevTeam(agents={"backend_dev": agent})
    state = team.run_interactive("Build auth module")
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from typing import Optional

from antcrew.core.channel import BaseChannel

log = logging.getLogger(__name__)

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    _SLACK_SDK = True
except ImportError:
    WebClient = None  # type: ignore[assignment,misc]
    SlackApiError = Exception  # type: ignore[assignment,misc]
    _SLACK_SDK = False

try:
    from slack_bolt import App as _BoltApp
    from slack_bolt.adapter.socket_mode import SocketModeHandler as _SocketModeHandler
    _SLACK_BOLT = True
except ImportError:
    _BoltApp = None  # type: ignore[assignment,misc]
    _SocketModeHandler = None  # type: ignore[assignment,misc]
    _SLACK_BOLT = False


# ---------------------------------------------------------------------------
# Block Kit builders
# ---------------------------------------------------------------------------

def _artifact_text(artifact) -> str:
    """Format an artifact as mrkdwn text for a Slack section block (≤3000 chars)."""
    from antcrew.core.artifacts import (
        PRD, Ticket, CodeArtifact, TestArtifact,
        CodeReview, ResearchDocument, ContentPiece,
    )

    if artifact is None:
        return "_No artifact produced._"

    if isinstance(artifact, PRD):
        reqs = "\n".join(f"• {r}" for r in (artifact.functional_requirements or [])[:5])
        return f"*PRD: {artifact.title}*\n{artifact.summary}\n\n*Functional Requirements:*\n{reqs}"

    if isinstance(artifact, list) and artifact and isinstance(artifact[0], Ticket):
        lines = "\n".join(
            f"• [{t.priority.value.upper()}] `{t.id}`: {t.title}" for t in artifact[:10]
        )
        return f"*Tickets ({len(artifact)}):*\n{lines}"

    if isinstance(artifact, list) and artifact and isinstance(artifact[0], (CodeArtifact, TestArtifact)):
        lines = "\n".join(f"• `{a.file_path}` ({a.language})" for a in artifact)
        return f"*{type(artifact[0]).__name__} artifacts ({len(artifact)}):*\n{lines}"

    if isinstance(artifact, CodeReview):
        icon = {"approve": "✅", "request_changes": "⚠️", "reject": "❌"}.get(artifact.verdict, "")
        findings = "\n".join(
            f"  [{f.severity}] `{f.file_path}`: {f.message}"
            for f in (artifact.findings or [])[:5]
        )
        return f"*Code Review: {icon} {artifact.verdict.upper()}*\n{artifact.summary}\n\n{findings}"

    if isinstance(artifact, ResearchDocument):
        kf = "\n".join(f"• {f}" for f in (artifact.key_findings or [])[:5])
        return f"*Research: {artifact.title}*\nTopic: {artifact.topic}\n\n*Key Findings:*\n{kf}"

    if isinstance(artifact, ContentPiece):
        preview = (artifact.body or "")[:500]
        if len(artifact.body or "") > 500:
            preview += "…"
        return (
            f"*{artifact.title}*\n"
            f"Audience: {artifact.target_audience}  |  Tone: {artifact.tone}\n\n{preview}"
        )

    return f"```{json.dumps(artifact, default=str)[:1000]}```"


def _review_blocks(artifact, agent_name: str, action_base: str) -> list[dict]:
    """Build Block Kit layout for an artifact review request."""
    text = _artifact_text(artifact)
    if len(text) > 2900:
        text = text[:2897] + "…"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🤖 {agent_name} — output ready for review"},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": action_base,
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": f"{action_base}_approve",
                    "value": "approve",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "action_id": f"{action_base}_reject",
                    "value": "reject",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "💬 Feedback"},
                    "action_id": f"{action_base}_feedback",
                    "value": "feedback",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✏️ Edit JSON"},
                    "action_id": f"{action_base}_edit",
                    "value": "edit",
                },
            ],
        },
    ]


def _feedback_modal(callback_id: str) -> dict:
    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": "Provide Feedback"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "fb_block",
                "label": {"type": "plain_text", "text": "What should the agent improve?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "fb_text",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Describe what needs to change…",
                    },
                },
            }
        ],
    }


def _edit_modal(callback_id: str, artifact_json: str) -> dict:
    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": "Edit Artifact JSON"},
        "submit": {"type": "plain_text", "text": "Apply"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "edit_block",
                "label": {"type": "plain_text", "text": "Edit the artifact JSON:"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "edit_json",
                    "multiline": True,
                    "initial_value": artifact_json[:2900],
                },
            }
        ],
    }


def _serialize_artifact(artifact) -> str:
    if artifact is None:
        return "{}"
    if hasattr(artifact, "model_dump_json"):
        return artifact.model_dump_json(indent=2)
    if isinstance(artifact, list) and artifact and hasattr(artifact[0], "model_dump"):
        return json.dumps([a.model_dump() for a in artifact], indent=2)
    return json.dumps(artifact, default=str)


# ---------------------------------------------------------------------------
# SlackChannel
# ---------------------------------------------------------------------------

class SlackChannel(BaseChannel):
    """
    Slack HITL channel.

    Each call to ``send_for_review`` posts a Block Kit card with four buttons
    (Approve / Reject / Feedback / Edit JSON) to the configured Slack channel,
    then blocks until the reviewer clicks one. Feedback and Edit open Slack
    modals for text input. A single Socket Mode listener thread is shared
    across all instances.
    """

    # Class-level shared state
    _bolt_thread: Optional[threading.Thread] = None
    _queues:    dict[str, asyncio.Queue] = {}   # session_id → Queue
    _artifacts: dict[str, object] = {}           # session_id → raw artifact
    _loop:      Optional[asyncio.AbstractEventLoop] = None

    def __init__(
        self,
        bot_token: Optional[str] = None,
        channel_id: Optional[str] = None,
        app_token: Optional[str] = None,
    ) -> None:
        if not _SLACK_SDK:
            raise ImportError("Run: pip install slack-sdk")
        if not _SLACK_BOLT:
            raise ImportError("Run: pip install slack-bolt")

        self._bot_token  = bot_token  or os.environ["SLACK_BOT_TOKEN"]
        self._channel_id = channel_id or os.environ["SLACK_CHANNEL_ID"]
        self._app_token  = app_token  or os.environ["SLACK_APP_TOKEN"]
        self._client: "WebClient" = WebClient(token=self._bot_token)

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def notify(self, message: str, **kwargs) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.chat_postMessage(
                channel=self._channel_id, text=f"• {message}"
            ),
        )

    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        response_options: Optional[list[str]] = None,
    ) -> dict:
        SlackChannel._loop = asyncio.get_event_loop()

        queue: asyncio.Queue = asyncio.Queue()
        SlackChannel._queues[session_id]    = queue
        SlackChannel._artifacts[session_id] = artifact

        self._ensure_bolt_running()

        action_base = f"ac_{session_id}"   # "ac" prefix keeps IDs ≤ 255 chars
        blocks = _review_blocks(artifact, agent_name, action_base)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.chat_postMessage(
                channel=self._channel_id,
                blocks=blocks,
                text=f"{agent_name} — output ready for review",
            ),
        )

        result = await queue.get()
        SlackChannel._queues.pop(session_id, None)
        SlackChannel._artifacts.pop(session_id, None)
        return result

    # ------------------------------------------------------------------
    # Bolt / Socket Mode
    # ------------------------------------------------------------------

    def _ensure_bolt_running(self) -> None:
        if SlackChannel._bolt_thread and SlackChannel._bolt_thread.is_alive():
            return

        bolt = _BoltApp(token=self._bot_token)

        # ---- Button action handler (approve / reject / feedback / edit) ----
        @bolt.action(re.compile(r"^ac_.*_(approve|reject|feedback|edit)$"))
        def on_button(ack, body, client, action):
            ack()
            action_id: str = action["action_id"]
            verb = action_id.rsplit("_", 1)[-1]               # last segment
            session_id = action_id[3 : -(len(verb) + 1)]      # strip "ac_" prefix and "_verb"

            if verb in ("approve", "reject"):
                self._put(session_id, {"decision": verb, "edited": None, "feedback": None})
                return

            trigger_id = body.get("trigger_id", "")
            if not trigger_id:
                log.warning("SlackChannel: no trigger_id — cannot open modal")
                return

            if verb == "feedback":
                try:
                    client.views_open(
                        trigger_id=trigger_id,
                        view=_feedback_modal(f"ac_{session_id}_fb"),
                    )
                except SlackApiError as exc:
                    log.error("SlackChannel: modal open failed: %s", exc)
                    self._put(session_id, {"decision": "approve", "edited": None, "feedback": None})

            elif verb == "edit":
                artifact = SlackChannel._artifacts.get(session_id)
                artifact_json = _serialize_artifact(artifact)
                try:
                    client.views_open(
                        trigger_id=trigger_id,
                        view=_edit_modal(f"ac_{session_id}_edit", artifact_json),
                    )
                except SlackApiError as exc:
                    log.error("SlackChannel: modal open failed: %s", exc)
                    self._put(session_id, {"decision": "approve", "edited": None, "feedback": None})

        # ---- Modal submission handler (feedback + edit) ----
        @bolt.view(re.compile(r"^ac_.*_(fb|edit)$"))
        def on_modal(ack, view):
            ack()
            cb: str = view["callback_id"]

            if cb.endswith("_fb"):
                session_id = cb[3:-3]   # strip "ac_" and "_fb"
                text = (
                    view["state"]["values"]
                    .get("fb_block", {})
                    .get("fb_text", {})
                    .get("value", "")
                )
                self._put(session_id, {"decision": "feedback", "feedback": text, "edited": None})

            elif cb.endswith("_edit"):
                session_id = cb[3:-5]   # strip "ac_" and "_edit"
                edited = (
                    view["state"]["values"]
                    .get("edit_block", {})
                    .get("edit_json", {})
                    .get("value", "")
                )
                self._put(session_id, {"decision": "edit", "edited": edited, "feedback": None})

        def _run():
            handler = _SocketModeHandler(bolt, self._app_token)
            handler.start()

        t = threading.Thread(target=_run, daemon=True, name="antcrew-slack-bolt")
        SlackChannel._bolt_thread = t
        t.start()
        log.info("SlackChannel: Socket Mode listener started")

    @classmethod
    def _put(cls, session_id: str, result: dict) -> None:
        """Thread-safe: deliver a result to the waiting coroutine."""
        queue = cls._queues.get(session_id)
        loop  = cls._loop
        if queue and loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(queue.put(result), loop)
        else:
            log.warning("SlackChannel: no waiting queue for session_id=%r", session_id)
