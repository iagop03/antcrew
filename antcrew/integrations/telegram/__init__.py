try:
    from antcrew.integrations.telegram.integration import (
        TelegramChannel,
        TelegramIntegration,  # backward-compat alias
        AgentBotConfig,
    )
except ImportError:
    TelegramChannel = None  # type: ignore[assignment,misc]
    TelegramIntegration = None  # type: ignore[assignment,misc]
    AgentBotConfig = None  # type: ignore[assignment,misc]

__all__ = ["TelegramChannel", "TelegramIntegration", "AgentBotConfig"]
