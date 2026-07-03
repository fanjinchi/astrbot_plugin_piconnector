from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path

from pi_connector import PiConnectionManager, PiError
from pi_connector.commands import (
    strip_command_prefix,
    parse_subcommand,
    format_session_info,
    format_session_list,
    format_ui_request,
    format_commands_list,
    resolve_select_option,
    parse_ui_reply_args,
)

USAGE = """Pi Connector usage:
/pi open <absolute path> - Open a new pi session at a directory
/pi sessions [dir] - List pi sessions in a directory (or active session's dir)
/pi resume <id> - Resume an existing pi session
/pi <text> - Send a natural language message to the current pi session
/pi abort - Abort the current pi operation

/pic <command> - Execute a pi slash command
/pic help - List available pi slash commands

When pi asks a question, reply with:
/pi confirm <id> yes|no
/pi select <id> <option or number>
/pi input <id> <value>
/pi edit <id> <text>
/pi cancel <id>
"""


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

    # ------------------------------------------------------------------
    # Command parsing helpers
    # ------------------------------------------------------------------

    async def _get_connection(self, event: AstrMessageEvent) -> object:
        """Return the active connection for the chat or raise PiError."""
        conn = await self.pi_connection_manager.get_connection(event, create=False)
        if conn is None or conn.process is None:
            raise PiError("No active pi session. Use /pi open or /pi resume first.")
        return conn

    async def _stream_events(
        self,
        event: AstrMessageEvent,
        conn: object,
        event_generator,
        buffer_size: int = 100,
    ):
        """Consume a pi event generator and yield plain text results.

        Text deltas are accumulated and yielded once they reach ``buffer_size``
        characters. When a pi extension UI request is encountered, the accumulated
        text is flushed and the UI request is displayed to the user.
        """
        buffer = ""
        async for ev in event_generator:
            ev_type = ev.get("type")
            if ev_type == "text":
                buffer += ev.get("text", "")
                if len(buffer) >= buffer_size:
                    yield event.plain_result(buffer)
                    buffer = ""
            elif ev_type == "thinking":
                # Thinking is intentionally not shown to keep the chat clean.
                pass
            elif ev_type == "ui_request":
                if buffer:
                    yield event.plain_result(buffer)
                    buffer = ""
                ui_request = ev.get("request")
                if ui_request:
                    yield event.plain_result(format_ui_request(ui_request))
                return

        if buffer:
            yield event.plain_result(buffer)

    async def _collect_events(self, generator) -> str:
        """Collect all text events from a pi event generator until agent_end.

        Tool execution events and UI requests are reported inline so the
        caller can see what happened.
        """
        parts = []
        async for ev in generator:
            ev_type = ev.get("type")
            if ev_type == "text":
                parts.append(ev.get("text", ""))
            elif ev_type == "ui_request":
                request = ev.get("request")
                if request:
                    parts.append("\n" + format_ui_request(request) + "\n")
                    return "".join(parts)
            elif ev_type == "event":
                raw = ev.get("event", {})
                if raw.get("type") == "tool_execution_start":
                    parts.append(
                        f"\n[Tool: {raw.get('toolName')}({raw.get('args', {})})]\n"
                    )
                elif raw.get("type") == "tool_execution_end":
                    result = raw.get("result", {})
                    content = result.get("content", [])
                    text = ""
                    for block in content:
                        if block.get("type") == "text":
                            text += block.get("text", "")
                    parts.append(f"[Tool result]: {text}\n")
        return "".join(parts)

    async def _collect_prompt_response(self, conn: object, prompt: str) -> str:
        """Send a prompt and collect all text events until agent_end."""
        generator = conn.send_prompt(prompt)
        return await self._collect_events(generator)

    # ------------------------------------------------------------------
    # /pi command handlers
    # ------------------------------------------------------------------

    @filter.command("pi")
    async def pi_handler(self, event: AstrMessageEvent):
        """Dispatch /pi subcommands or treat the message as a natural language prompt."""
        text = strip_command_prefix(event.message_str, "pi")
        subcommand, rest = parse_subcommand(text)

        if subcommand == "open":
            async for item in self._handle_pi_open(event, rest):
                yield item
        elif subcommand in ("sessions", "session"):
            async for item in self._handle_pi_sessions(event, rest):
                yield item
        elif subcommand == "resume":
            async for item in self._handle_pi_resume(event, rest):
                yield item
        elif subcommand == "confirm":
            async for item in self._handle_pi_confirm(event, rest):
                yield item
        elif subcommand == "select":
            async for item in self._handle_pi_select(event, rest):
                yield item
        elif subcommand == "input":
            async for item in self._handle_pi_input(event, rest):
                yield item
        elif subcommand == "edit":
            async for item in self._handle_pi_edit(event, rest):
                yield item
        elif subcommand == "cancel":
            async for item in self._handle_pi_cancel(event, rest):
                yield item
        elif subcommand == "abort":
            async for item in self._handle_pi_abort(event):
                yield item
        elif subcommand in ("help", ""):
            yield event.plain_result(USAGE)
        else:
            # Treat the entire stripped text as a natural language prompt.
            async for item in self._handle_pi_prompt(event, text):
                yield item

    async def _handle_pi_open(self, event: AstrMessageEvent, rest: str):
        """Handle /pi open <absolute path>."""
        path = rest.strip()
        if not path:
            yield event.plain_result("Usage: /pi open <absolute path>")
            return
        try:
            info = await self.pi_connection_manager.open_session(event, path)
            yield event.plain_result(
                f"Opened new pi session.\n{format_session_info(info)}"
            )
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_sessions(self, event: AstrMessageEvent, rest: str):
        """Handle /pi sessions [dir]."""
        directory = rest.strip() or None
        if not directory:
            directory = await self.pi_connection_manager.get_active_cwd(event)
            if not directory:
                yield event.plain_result(
                    "Usage: /pi sessions <absolute directory>\n"
                    "Or open/resume a session first to list its directory."
                )
                return
        try:
            sessions = self.pi_connection_manager.list_sessions(directory)
            yield event.plain_result(format_session_list(sessions))
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_resume(self, event: AstrMessageEvent, rest: str):
        """Handle /pi resume <id>."""
        session_id = rest.strip()
        if not session_id:
            yield event.plain_result("Usage: /pi resume <session id>")
            return
        try:
            info = await self.pi_connection_manager.resume_session(event, session_id)
            yield event.plain_result(
                f"Resumed pi session.\n{format_session_info(info)}"
            )
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_prompt(self, event: AstrMessageEvent, text: str):
        """Handle /pi <natural language>."""
        if not text.strip():
            yield event.plain_result(USAGE)
            return
        try:
            conn = await self._get_connection(event)
            event_generator = conn.send_prompt(text)
            async for item in self._stream_events(event, conn, event_generator):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_abort(self, event: AstrMessageEvent):
        """Handle /pi abort."""
        try:
            conn = await self._get_connection(event)
            await conn.abort()
            yield event.plain_result("Abort request sent to pi.")
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    # ------------------------------------------------------------------
    # UI reply handlers
    # ------------------------------------------------------------------

    async def _handle_pi_confirm(self, event: AstrMessageEvent, rest: str):
        """Handle /pi confirm <id> yes|no."""
        local_id, value = parse_ui_reply_args(rest)
        if local_id is None:
            yield event.plain_result("Usage: /pi confirm <id> yes|no")
            return
        confirmed = value.strip().lower() in ("yes", "y", "true", "1")
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(local_id)
            if ui_request is None:
                yield event.plain_result(f"No pending request with id {local_id}.")
                return
            await conn.confirm_ui_request(ui_request.request_id, confirmed)
            yield event.plain_result(
                f"{'Confirmed' if confirmed else 'Declined'} request #{local_id}."
            )
            async for item in self._stream_events(event, conn, conn.read_response()):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_select(self, event: AstrMessageEvent, rest: str):
        """Handle /pi select <id> <option or number>."""
        local_id, value = parse_ui_reply_args(rest)
        if local_id is None:
            yield event.plain_result("Usage: /pi select <id> <option or number>")
            return
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(local_id)
            if ui_request is None:
                yield event.plain_result(f"No pending request with id {local_id}.")
                return
            selected = resolve_select_option(ui_request, value)
            if selected is None:
                yield event.plain_result(
                    f"Invalid option '{value}'. Use the option text or a 1-based number."
                )
                return
            await conn.reply_ui_request(ui_request.request_id, selected)
            yield event.plain_result(f"Selected '{selected}' for request #{local_id}.")
            async for item in self._stream_events(event, conn, conn.read_response()):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_input(self, event: AstrMessageEvent, rest: str):
        """Handle /pi input <id> <value>."""
        local_id, value = parse_ui_reply_args(rest)
        if local_id is None:
            yield event.plain_result("Usage: /pi input <id> <value>")
            return
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(local_id)
            if ui_request is None:
                yield event.plain_result(f"No pending request with id {local_id}.")
                return
            await conn.reply_ui_request(ui_request.request_id, value)
            yield event.plain_result(f"Replied to request #{local_id}.")
            async for item in self._stream_events(event, conn, conn.read_response()):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_edit(self, event: AstrMessageEvent, rest: str):
        """Handle /pi edit <id> <text>."""
        local_id, value = parse_ui_reply_args(rest)
        if local_id is None:
            yield event.plain_result("Usage: /pi edit <id> <text>")
            return
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(local_id)
            if ui_request is None:
                yield event.plain_result(f"No pending request with id {local_id}.")
                return
            await conn.reply_ui_request(ui_request.request_id, value)
            yield event.plain_result(f"Edited text for request #{local_id}.")
            async for item in self._stream_events(event, conn, conn.read_response()):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_cancel(self, event: AstrMessageEvent, rest: str):
        """Handle /pi cancel <id>."""
        local_id, _ = parse_ui_reply_args(rest)
        if local_id is None:
            yield event.plain_result("Usage: /pi cancel <id>")
            return
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(local_id)
            if ui_request is None:
                yield event.plain_result(f"No pending request with id {local_id}.")
                return
            await conn.cancel_ui_request(ui_request.request_id)
            yield event.plain_result(f"Cancelled request #{local_id}.")
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    # ------------------------------------------------------------------
    # /pic command handlers
    # ------------------------------------------------------------------

    @filter.command("pic")
    async def pic_handler(self, event: AstrMessageEvent):
        """Handle /pic <command> and /pic help."""
        text = strip_command_prefix(event.message_str, "pic")
        if not text.strip():
            yield event.plain_result("Usage: /pic <command> or /pic help")
            return

        if text.strip().lower() == "help":
            async for item in self._handle_pic_help(event):
                yield item
            return

        command = text.strip()
        if not command.startswith("/"):
            command = f"/{command}"

        try:
            conn = await self._get_connection(event)
            event_generator = conn.send_prompt(command)
            async for item in self._stream_events(event, conn, event_generator):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pic_help(self, event: AstrMessageEvent):
        """Handle /pic help."""
        try:
            conn = await self._get_connection(event)
            commands = await conn.get_commands()
            yield event.plain_result(format_commands_list(commands))
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    # ------------------------------------------------------------------
    # LLM tools
    # ------------------------------------------------------------------

    @filter.llm_tool(name="pi_open_session")
    async def pi_open_session(
        self, event: AstrMessageEvent, path: str, name: str = None
    ) -> str:
        """Open a new pi session at an absolute directory path.

        Args:
            path(string): Absolute directory path for the pi session
            name(string): Optional display name for the session
        """
        try:
            info = await self.pi_connection_manager.open_session(event, path, name=name)
            return f"Opened new pi session.\n{format_session_info(info)}"
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_list_sessions")
    async def pi_list_sessions(self, event: AstrMessageEvent, dir: str = None) -> str:
        """List existing pi sessions in a directory.

        Args:
            dir(string): Absolute directory to list sessions for. Uses the active session's directory if omitted.
        """
        try:
            directory = dir
            if not directory:
                directory = await self.pi_connection_manager.get_active_cwd(event)
            if not directory:
                return (
                    "No directory specified and no active session. "
                    "Provide a directory or open/resume a session first."
                )
            sessions = self.pi_connection_manager.list_sessions(directory)
            return format_session_list(sessions)
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_resume_session")
    async def pi_resume_session(self, event: AstrMessageEvent, session_id: str) -> str:
        """Resume an existing pi session by its id or file path.

        Args:
            session_id(string): Session id or partial id to resume
        """
        try:
            info = await self.pi_connection_manager.resume_session(event, session_id)
            return f"Resumed pi session.\n{format_session_info(info)}"
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_send_message")
    async def pi_send_message(self, event: AstrMessageEvent, message: str) -> str:
        """Send a natural language message to the current pi session.

        Args:
            message(string): The message to send to pi
        """
        try:
            conn = await self._get_connection(event)
            response = await self._collect_prompt_response(conn, message)
            return response or "No response from pi."
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_get_session_info")
    async def pi_get_session_info(self, event: AstrMessageEvent) -> str:
        """Get information about the current pi session."""
        try:
            info = await self.pi_connection_manager.get_session_info(event)
            return format_session_info(info)
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_run_command")
    async def pi_run_command(self, event: AstrMessageEvent, command: str) -> str:
        """Execute a pi slash command in the current session.

        Args:
            command(string): The slash command to execute (without the leading /)
        """
        try:
            conn = await self._get_connection(event)
            slash = command if command.startswith("/") else f"/{command}"
            response = await self._collect_prompt_response(conn, slash)
            return response or "No response from pi."
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_get_available_commands")
    async def pi_get_available_commands(self, event: AstrMessageEvent) -> str:
        """List the slash commands available in the current pi session."""
        try:
            conn = await self._get_connection(event)
            commands = await conn.get_commands()
            return format_commands_list(commands)
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_abort")
    async def pi_abort(self, event: AstrMessageEvent) -> str:
        """Abort the current pi operation."""
        try:
            conn = await self._get_connection(event)
            await conn.abort()
            return "Abort request sent to pi."
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_reply_ui")
    async def pi_reply_ui(
        self, event: AstrMessageEvent, request_id: int, value: str
    ) -> str:
        """Reply to a pending pi extension UI request.

        Args:
            request_id(number): The local request id shown by pi
            value(string): The reply value (yes/no for confirm, option text or number for select, text for input/editor)
        """
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(request_id)
            if ui_request is None:
                return f"No pending request with id {request_id}."

            if ui_request.method == "confirm":
                confirmed = value.strip().lower() in ("yes", "y", "true", "1")
                await conn.confirm_ui_request(ui_request.request_id, confirmed)
                status = "confirmed" if confirmed else "declined"
            elif ui_request.method == "select":
                selected = resolve_select_option(ui_request, value)
                if selected is None:
                    return f"Invalid option '{value}'. Use the option text or a 1-based number."
                await conn.reply_ui_request(ui_request.request_id, selected)
                status = f"selected '{selected}'"
            elif ui_request.method in ("input", "editor"):
                await conn.reply_ui_request(ui_request.request_id, value)
                status = "replied"
            else:
                await conn.reply_ui_request(ui_request.request_id, value)
                status = "replied"

            response = await self._collect_events(conn.read_response())
            return f"{status} request #{request_id}.\n{response}".strip()
        except PiError as exc:
            return f"Error: {exc}"
