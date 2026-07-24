"""
TelegramChannel — implements BaseChannel for Telegram bots.

Two modes:

single:     One bot, one chat. All agents send from the same token.
            Entry point: user messages the bot.

per_agent:  Each agent has its own bot (token) and its own reviewer (chat_id).
            E.g. BA bot → PM's Telegram chat for PRD review,
                 PM bot → tech lead's chat for ticket review,
                 Dev bot → CTO's chat for code review.
            Entry point: user messages the first agent's bot.

``TelegramIntegration`` is kept as a backward-compatible alias.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Coroutine, Literal, Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from antcrew.core.artifacts import PRD, CodeArtifact, Ticket
from antcrew.core.channel import BaseChannel
from antcrew.integrations.telegram.formatter import (
    format_code_artifact,
    format_prd,
    format_tickets,
)
from antcrew.integrations.telegram.hitl import HitlManager

log = logging.getLogger(__name__)

BotMode = Literal["single", "per_agent"]

PipelineHandler = Callable[[str, int], Coroutine]

# Button label and callback action per option name
_BUTTON_MAP: dict[str, tuple[str, str]] = {
    "approve":  ("✅ Aprobar",          "approve"),
    "reject":   ("❌ Rechazar",         "reject"),
    "feedback": ("✏️ Sugerir cambios",  "feedback"),
    "edit":     ("📝 Editar JSON",      "edit"),
}
_DEFAULT_OPTIONS = ["approve", "feedback", "reject"]


@dataclass
class AgentBotConfig:
    """
    Configuration for one agent's bot in per_agent mode.

    token:   Bot token (from @BotFather).
    chat_id: Telegram user/group ID of the person who reviews this agent's output.
    notify:  Additional chat_ids that receive status broadcasts (not HITL).
    """
    token: str
    chat_id: int | str
    notify: list[int | str] = field(default_factory=list)


def _build_keyboard(session_id: str, options: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    for opt in options:
        if opt in _BUTTON_MAP:
            label, action = _BUTTON_MAP[opt]
            buttons.append(InlineKeyboardButton(label, callback_data=f"{action}:{session_id}"))
    return InlineKeyboardMarkup([buttons])


class TelegramChannel(BaseChannel):
    """
    Implements BaseChannel for Telegram bots.

    Single mode (one bot for all agents):
        TelegramChannel(token="BOT_TOKEN", chat_id=CHAT_ID)

    Notify multiple recipients (broadcasts, HITL goes to first):
        TelegramChannel(token="BOT_TOKEN", notify=[MARIA_ID, JUAN_ID])

    Per-agent mode (each agent has its own bot + reviewer):
        TelegramChannel(
            mode="per_agent",
            agent_configs={
                "pm":          AgentBotConfig(token="T1", chat_id=MARIA_ID),
                "backend_dev": AgentBotConfig(token="T2", chat_id=JUAN_ID),
            },
        )

    Per-agent on a single agent:
        PMAgent(
            channel=TelegramChannel(token="T1", notify=[MARIA_ID]),
            approval_required=True,
        )
    """

    def __init__(
        self,
        mode: BotMode = "single",
        token: Optional[str] = None,
        chat_id: Optional[int | str] = None,
        notify: Optional[list[int | str]] = None,
        agent_configs: Optional[dict[str, AgentBotConfig]] = None,
    ) -> None:
        if mode == "single" and not token:
            raise ValueError("token is required for single mode")
        if mode == "per_agent" and not agent_configs:
            raise ValueError("agent_configs is required for per_agent mode")

        self.mode = mode
        self._token = token
        # notify sets the primary chat_id and stores all ids for broadcasts
        if notify:
            self._chat_id: Optional[int | str] = notify[0]
            self._notify_all: list[int | str] = notify
        else:
            self._chat_id = chat_id
            self._notify_all = [chat_id] if chat_id else []
        self._agent_configs: dict[str, AgentBotConfig] = agent_configs or {}
        self.hitl = HitlManager()

    # ------------------------------------------------------------------
    # Token / chat_id resolution
    # ------------------------------------------------------------------

    def _get_token(self, agent_name: Optional[str] = None) -> str:
        if self.mode == "single":
            return self._token
        cfg = self._agent_configs.get(agent_name or "")
        return cfg.token if cfg else next(iter(self._agent_configs.values())).token

    def _get_chat_id(self, agent_name: Optional[str] = None) -> int | str:
        if self.mode == "single":
            return self._chat_id
        cfg = self._agent_configs.get(agent_name or "")
        return cfg.chat_id if cfg else next(iter(self._agent_configs.values())).chat_id

    def _get_notify_list(self, agent_name: Optional[str] = None) -> list[int | str]:
        """Return all chat_ids to receive status broadcasts for this agent."""
        if self.mode == "single":
            return self._notify_all
        cfg = self._agent_configs.get(agent_name or "")
        if cfg:
            ids = [cfg.chat_id] + cfg.notify
            return ids
        primary = self._get_chat_id(agent_name)
        return [primary] if primary else []

    def set_chat_id(self, chat_id: int | str) -> None:
        """Set the chat_id dynamically (captured from first incoming message)."""
        self._chat_id = chat_id
        if not self._notify_all:
            self._notify_all = [chat_id]

    # ------------------------------------------------------------------
    # Sending artifacts with HITL
    # ------------------------------------------------------------------

    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        options: Optional[list[str]] = None,
    ) -> dict:
        """
        Format `artifact`, send it to the agent's reviewer with action buttons,
        and wait for their decision.

        Buttons are controlled by `options` (default: approve / sugerir cambios / rechazar).

        If "Sugerir cambios" is pressed, the bot asks for free-text feedback and
        passes it to agent.refine() via {"decision": "feedback", "feedback": text}.

        If "Editar JSON" is pressed (only when "edit" in options), the bot asks for
        the corrected JSON payload.

        Returns:
            {"decision": "approve" | "reject" | "feedback" | "edit",
             "feedback": str | None, "edited": str | None}
        """
        effective_options = options or _DEFAULT_OPTIONS
        bot = Bot(token=self._get_token(agent_name))
        chat_id = self._get_chat_id(agent_name)

        if isinstance(artifact, PRD):
            text = format_prd(artifact)
        elif isinstance(artifact, list) and artifact and isinstance(artifact[0], Ticket):
            text = format_tickets(artifact)
        else:
            text = str(artifact)[:4000]

        self.hitl.create(session_id)

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_build_keyboard(session_id, effective_options),
        )

        result = await self.hitl.wait(session_id)
        decision = result["decision"]

        if decision == "feedback":
            await bot.send_message(
                chat_id=chat_id,
                text="✏️ ¿Cuál es tu sugerencia? (o envía /cancel para rechazar):",
            )
            self.hitl.set_edit_waiting(session_id)
            self.hitl.create(session_id)
            text_result = await self.hitl.wait(session_id)
            feedback_text = text_result.get("edited") or ""
            return {"decision": "feedback", "feedback": feedback_text, "edited": None}

        if decision == "edit":
            await bot.send_message(
                chat_id=chat_id,
                text="📝 Envía el JSON corregido para continuar (o /cancel para rechazar):",
            )
            self.hitl.set_edit_waiting(session_id)
            self.hitl.create(session_id)
            json_result = await self.hitl.wait(session_id)
            return {"decision": "edit", "edited": json_result.get("edited"), "feedback": None}

        return {"decision": decision, "feedback": None, "edited": None}

    async def notify(self, message: str, **kwargs) -> None:
        """BaseChannel.notify — delegates to send_status."""
        agent_name = kwargs.get("agent_name")
        await self.send_status(message, agent_name=agent_name)

    async def send_status(self, text: str, agent_name: Optional[str] = None) -> None:
        """Send a plain status message to all configured recipients for this agent."""
        bot = Bot(token=self._get_token(agent_name))
        for cid in (self._get_notify_list(agent_name) or [self._get_chat_id(agent_name)]):
            try:
                await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
            except Exception:
                log.warning("Failed to send status to chat_id=%s", cid)

    async def send_code_artifacts(
        self, artifacts: list[CodeArtifact], agent_name: str = "backend_dev"
    ) -> None:
        bot = Bot(token=self._get_token(agent_name))
        chat_id = self._get_chat_id(agent_name)
        for artifact in artifacts:
            for chunk in format_code_artifact(artifact):
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")

    # ------------------------------------------------------------------
    # PTB Application builder
    # ------------------------------------------------------------------

    def build_apps(self) -> list[Application]:
        """
        Build and return the PTB Application(s) with handlers wired up.
        Call `set_pipeline_handler()` on the returned apps before starting polling.
        """
        if self.mode == "single":
            return [self._build_app(self._token, entry_point=True)]

        apps = []
        for i, (name, cfg) in enumerate(self._agent_configs.items()):
            app = self._build_app(cfg.token, entry_point=(i == 0))
            apps.append(app)
        return apps

    def _build_app(self, token: str, *, entry_point: bool) -> Application:
        app = Application.builder().token(token).build()
        app.add_handler(CallbackQueryHandler(self._on_callback))
        if entry_point:
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
            )
        return app

    def set_pipeline_handler(
        self, apps: list[Application], handler: PipelineHandler
    ) -> None:
        """Inject the pipeline coroutine into all app bot_data dicts."""
        for app in apps:
            app.bot_data["pipeline_handler"] = handler

    # ------------------------------------------------------------------
    # Telegram handlers
    # ------------------------------------------------------------------

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming text — either a new request or a feedback / edit reply."""
        chat_id = update.effective_chat.id
        text = update.message.text
        session_id = str(chat_id)

        if text.startswith("/cancel"):
            if self.hitl.pop_edit_waiting(session_id):
                self.hitl.create(session_id)
                await self.hitl.resolve(session_id, "reject")
            return

        # Waiting for feedback/edit text reply
        if self.hitl.pop_edit_waiting(session_id):
            await self.hitl.resolve(session_id, "edit", text)
            return

        # New pipeline request
        if self.mode == "single":
            self._chat_id = chat_id
            if not self._notify_all:
                self._notify_all = [chat_id]

        handler: Optional[PipelineHandler] = context.bot_data.get("pipeline_handler")
        if handler:
            asyncio.create_task(handler(text, chat_id))
        else:
            await update.message.reply_text("⚠️ No pipeline handler configured.")

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle inline button presses."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)

        if ":" not in (query.data or ""):
            return

        action, session_id = query.data.split(":", 1)

        if not self.hitl.is_pending(session_id):
            return

        if action in ("approve", "reject"):
            await self.hitl.resolve(session_id, action)
        elif action in ("edit", "feedback"):
            # resolve first wait; send_for_review handles the follow-up prompt
            await self.hitl.resolve(session_id, action)


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

TelegramIntegration = TelegramChannel
