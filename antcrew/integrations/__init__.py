try:
    from antcrew.integrations.telegram.integration import (
        AgentBotConfig,
        TelegramChannel,
        TelegramIntegration,
    )
except ImportError:
    TelegramChannel = None  # type: ignore[assignment,misc]
    TelegramIntegration = None  # type: ignore[assignment,misc]
    AgentBotConfig = None  # type: ignore[assignment,misc]

from antcrew.integrations.console import ConsoleChannel

__all__ = ["TelegramChannel", "TelegramIntegration", "AgentBotConfig", "ConsoleChannel"]
