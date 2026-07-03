from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path

from pi_connector import PiConnectionManager


@register("astrbot_plugin_piconnector", "AstrBot", "Connect AstrBot to a local pi agent for session management, chat, and code tasks.", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        session_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_piconnector" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        self.pi_connection_manager = PiConnectionManager(
            session_dir=str(session_dir),
            executable="pi",
        )
        logger.info("PiConnector initialized with session_dir=%s", session_dir)

    async def initialize(self):
        """Async initialization hook called after the Star is instantiated."""

    async def terminate(self):
        """Terminate all managed pi connections when the plugin is unloaded."""
        await self.pi_connection_manager.terminate_all()
