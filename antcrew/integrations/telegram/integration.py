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
from dataclasses import dataclass
from typing import Callable, Coroutine, Literal, Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from antcrew.core.artifacts import PRD, Ticket, CodeArtifact
from antcrew.core.channel import BaseChannel
from antcrew.integrations.telegram.formatter import (
    format_prd,
    format_tickets,
    format_code_artifact,
)
from antcrew.integrations.telegram.hitl import HitlManager

log = logging.getLogger(__name__)

BotMode = Literal["single", "per_agent"]

PipelineHandler = Callable[[str, int], Coroutine]


@dataclass
class AgentBotConfig:
    """
    Configuration for one agent's bot in per_agent mode.

    token:   Bot token (from @BotFather).
    chat_id: Telegram user/group ID of the person who reviews this agent's output.
    """
    token: str
    chat_id: int | str


def _approval_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{session_id}"),
        InlineKeyboardButton("✏️ Edit",    callback_data=f"edit:{session_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{session_id}"),
    ]])


class TelegramChannel(BaseChannel):
    """
    Implements BaseChannel for Telegram bots.

    Single mode (one bot for all agents):
        TelegramChannel(token="BOT_TOKEN", chat_id=CHAT_ID)

    Per-agent mode (each agent has its own bot + reviewer):
        TelegramChannel(
            mode="per_agent",
            agent_configs={
                "business_analyst": AgentBotConfig(token="T1", chat_id=PM_CHAT_ID),
                "pm":               AgentBotConfig(token="T2", chat_id=TECH_LEAD_ID),
                "backend_dev":      AgentBotConfig(token="T3", chat_id=CTO_CHAT_ID),
            },
        )

    Per-agent on a single agent:
        BackendDevAgent(
            llm=AnthropicModel(),
            channel=TelegramChannel(token="T3", chat_id=CTO_CHAT_ID),
            approval_required=True,
        )
    """

    def __init__(
        self,
        mode: BotMode = "single",
        token: Optional[str] = None,
        chat_id: Optional[int | str] = None,
        agent_configs: Optional[dict[str, AgentBotConfig]] = None,
    ) -> None:
        if mode == "single" and not token:
            raise ValueError("token is required for single mode")
        if mode == "per_agent" and not agent_configs:
            raise ValueError("agent_configs is required for per_agent mode")

        self.mode = mode
        self._token = token
        self._chat_id = chat_id
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

    def set_chat_id(self, chat_id: int | str) -> None:
        """Set the chat_id dynamically (captured from first incoming message)."""
        self._chat_id = chat_id

    # ------------------------------------------------------------------
    # Sending artifacts with HITL
    # ------------------------------------------------------------------

    async def send_for_review(
        self, artifact, agent_name: str, session_id: str
    ) -> dict:
        """
        Format `artifact`, send it to the agent's configured reviewer with
        Approve / Edit / Reject buttons, and wait for their decision.

        If "Edit" is pressed, sends a follow-up prompt and waits for a JSON reply.

        Returns:
            {"decision": "approve" | "reject" | "edit", "edited": str | None}
        """
        bot = Bot(token=self._get_token(agent_name))
        chat_id = self._get_chat_id(agent_name)

        if isinstance(artifact, PRD):
            text = format_prd(artifact)
        elif isinstance(artifact, list) and artifact and isinstance(artifact[0], Ticket):
            text = format_tickets(artifact)
        else:
            text = str(artifact)[:4000]

        # Create checkpoint BEFORE sending to avoid race condition
        self.hitl.create(session_id)

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_approval_keyboard(session_id),
        )

        result = await self.hitl.wait(session_id)

        # If user chose Edit, prompt for the JSON and wait for the reply
        if result["decision"] == "edit":
            await bot.send_message(
                chat_id=chat_id,
                text="✏️ Send the edited JSON to continue (or send /cancel to reject):",
            )
            self.hitl.set_edit_waiting(session_id)
            self.hitl.create(session_id)
            result = await self.hitl.wait(session_id)

        return result

    async def send_status(self, text: str, agent_name: Optional[str] = None) -> None:
        """Send a plain status message to the appropriate chat."""
        bot = Bot(token=self._get_token(agent_name))
        await bot.send_message(
            chat_id=self._get_chat_id(agent_name), text=text, parse_mode="HTML"
        )

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
        """Handle incoming text — either a new request or an edit JSON reply."""
        chat_id = update.effective_chat.id
        text = update.message.text
        session_id = str(chat_id)

        if text.startswith("/cancel"):
            if self.hitl.pop_edit_waiting(session_id):
                self.hitl.create(session_id)
                await self.hitl.resolve(session_id, "reject")
            return

        # Waiting for edit JSON reply
        if self.hitl.pop_edit_waiting(session_id):
            await self.hitl.resolve(session_id, "edit", text)
            return

        # New pipeline request
        if self.mode == "single":
            self._chat_id = chat_id

        handler: Optional[PipelineHandler] = context.bot_data.get("pipeline_handler")
        if handler:
            asyncio.create_task(handler(text, chat_id))
        else:
            await update.message.reply_text("⚠️ No pipeline handler configured.")

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle inline button presses (Approve / Edit / Reject)."""
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
        elif action == "edit":
            # resolve first wait; send_for_review handles the follow-up prompt
            await self.hitl.resolve(session_id, "edit")


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

TelegramIntegration = TelegramChannel
