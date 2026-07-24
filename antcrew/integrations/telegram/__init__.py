try:
    from antcrew.integrations.telegram.integration import (
        AgentBotConfig,
        TelegramChannel,
        TelegramIntegration,  # backward-compat alias
    )
except ImportError:
    TelegramChannel = None  # type: ignore[assignment,misc]
    TelegramIntegration = None  # type: ignore[assignment,misc]
    AgentBotConfig = None  # type: ignore[assignment,misc]

__all__ = ["TelegramChannel", "TelegramIntegration", "AgentBotConfig"]
