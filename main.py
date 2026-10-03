# ruff: noqa: E402 — the imports below must run *after* the sys.path fix-up and
# the stale-module purge, so they cannot sit at the top of the file.
import asyncio
import sys
from pathlib import Path

# Ensure the sibling `pi_connector` package is importable when AstrBot loads
# this file directly as a standalone module.
sys.path.insert(0, str(Path(__file__).parent))

# AstrBot's plugin reload only drops modules under its own `data.plugins.*`
# namespace, but `pi_connector` lives at the top level — so a previously imported
# copy lingers in sys.modules and keeps serving stale code after an update
# (symptom: a newly added constructor argument reported as "unexpected keyword
# argument"). Drop our own modules first so every load picks up the current code.
for _stale in [
    k for k in sys.modules if k == "pi_connector" or k.startswith("pi_connector.")
]:
    del sys.modules[_stale]

from pi_connector import (
    PiConnection,
    PiConnectionManager,
    PiError,
    PiTask,
    PiTaskManager,
    UIRequest,
)
from pi_connector.commands import (
    extract_active_branch,
    format_commands_list,
    format_session_all_list,
    format_session_info,
    format_session_list,
    format_tree_entries,
    format_ui_request,
    parse_subcommand,
    parse_ui_reply_args,
    resolve_select_option,
    resolve_tree_entry_id,
    strip_command_prefix,
)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import PermissionType
from astrbot.api.star import Context, Star

USAGE = """Pi Connector 命令帮助

会话管理：
  /pi open <绝对路径>       - 在指定目录打开新的 pi session
  /pi sessions [目录]         - 列出目录下的 session（省略则使用当前 session 目录，默认 10 条/页）
  /pi sessions all          - 列出所有目录下的 session，每个目录最多显示最新 10 条
  /pi next                  - 查看 /pi sessions 的下一页
  /pi session               - 显示当前 session 信息
  /pi info                  - /pi session 的别名
  /pi resume [id|序号]      - 恢复已有 session（省略则恢复最近会话；序号来自 /pi sessions 列表）
  /pi quit                  - 断开当前 pi session 连接
  /pi tree [编号]           - 查看当前会话 user-only 历史并编号；带编号可分叉
  /pi abort                 - 中止当前 pi 操作

对话与命令：
  /pi <自然语言>             - 向当前 session 发送消息
  /pic <command>            - 执行 pi 的 slash 命令（例如 /pic opsx-explore）
  /pic help                 - 列出当前 session 可用的 slash 命令

回复 pi 的 UI 请求：
  /pi confirm <id> yes|no
  /pi select <id> <选项或编号>
  /pi input <id> <内容>
  /pi edit <id> <内容>
  /pi cancel <id>

示例：
  /pi open /home/guigui/project
  /pi 帮我重构 auth 模块
  /pic help
  /pi sessions
"""


class PiConnectorPlugin(Star):
    """Connect AstrBot to a local pi agent for session management, chat, and code tasks."""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.plugin_config = config or {}
        # Use pi's native session directory so sessions are shared with the
        # pi CLI and any other pi clients. This can be made configurable later
        # if per-plugin isolation is desired.
        self.pi_connection_manager = PiConnectionManager(
            session_dir=None,
            executable="pi",
            # Lazy: read per connection so config edits apply without a reload.
            astrbot_root_provider=self._get_astrbot_root_setting,
        )
        self._task_manager = PiTaskManager(
            session_key_fn=self.pi_connection_manager._session_key,
            on_task_finished=self._notify_task_finished,
            on_ui_request=self._handle_background_ui_request,
            # Read lazily: the plugin config is not ready this early, and reading
            # it per task also lets config edits apply without a reload.
            idle_timeout_provider=self._get_idle_timeout,
        )
        # Fire-and-forget wakes of the main agent after a background task
        # finishes; holding references keeps them from being GC'd mid-flight.
        self._wake_tasks: set[asyncio.Task] = set()
        logger.info("PiConnector initialized")

    async def initialize(self):
        """Async initialization hook called after the Star is instantiated."""
        logger.info("PiConnector plugin initialized.")

    async def terminate(self):
        """Terminate background tasks and all managed pi connections."""
        await self._task_manager.cancel_all()
        await self.pi_connection_manager.terminate_all()

    # ------------------------------------------------------------------
    # Permission check
    # ------------------------------------------------------------------

    def _require_admin(self, event: AstrMessageEvent) -> str | None:
        """Return a permission-denied message if the sender is not an AstrBot admin."""
        if not event.is_admin():
            return "Permission denied. Pi Connector is only available to AstrBot administrators."
        return None

    def _get_astrbot_root_setting(self) -> str | None:
        """Configured ASTRBOT_ROOT pin for spawned pi processes.

        Empty means "detect automatically"; the literal ``off`` disables the pin.
        Read lazily because the plugin config is not ready at construction time.
        """
        try:
            cfg = getattr(self, "plugin_config", None) or {}
            value = str(cfg.get("astrbot_root", "") or "").strip()
            return value or None
        except Exception:  # noqa: BLE001 — bad config must not break the plugin
            return None

    def _get_idle_timeout(self) -> float:
        """Idle timeout in seconds for background tasks, read from config.

        Kept lazy on purpose: at Star construction time the plugin config may
        not be populated yet, and touching it there can fail the whole import.
        """
        try:
            cfg = getattr(self, "plugin_config", None) or {}
            return float(cfg.get("idle_timeout_seconds", 1200))
        except Exception:  # noqa: BLE001 — missing/odd config must not break init
            return 1200.0

    def _get_per_dir_limit(self) -> int:
        """Return the configured per-directory limit for /pi sessions all."""
        try:
            limit = self.plugin_config.get("sessions_all_per_dir_limit", 10)
            limit = int(limit)
        except (TypeError, ValueError):
            return 10
        if limit < 1:
            return 10
        return limit

    # ------------------------------------------------------------------
    # Command parsing helpers
    # ------------------------------------------------------------------

    async def _get_connection(self, event: AstrMessageEvent) -> PiConnection:
        """Return the active connection for the chat or raise PiError."""
        conn = await self.pi_connection_manager.get_connection(event, create=False)
        if conn is None or conn.process is None:
            raise PiError("No active pi session. Use /pi open or /pi resume first.")
        return conn

    async def _stream_events(
        self,
        event: AstrMessageEvent,
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
        """Collect all text events from a pi event generator until it settles.

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

    # ------------------------------------------------------------------
    # Background task helpers
    # ------------------------------------------------------------------

    def _active_task_message(self, task: PiTask) -> str:
        """Return a standard rejection message when a task is already running."""
        elapsed = round(task.elapsed)
        return (
            f"A pi background task #{task.task_id} is already running "
            f"(elapsed {elapsed}s). Do not launch another prompt, do not poll, "
            "and do not abort. The result will be delivered automatically."
        )

    async def _launch_background_task(
        self, event: AstrMessageEvent, prompt: str
    ) -> str:
        """Launch a background pi prompt task and return guidance for the agent."""
        key = self.pi_connection_manager._session_key(event)
        active = self._task_manager.get_active(key)
        if active is not None:
            return self._active_task_message(active)

        try:
            conn = await self._get_connection(event)
            task = await self._task_manager.launch(
                conn, key, prompt, event.unified_msg_origin
            )
        except RuntimeError as exc:
            if "already running" in str(exc):
                active = self._task_manager.get_active(key)
                return self._active_task_message(active)
            return f"Error: {exc}"
        except PiError as exc:
            # e.g. no active session, or the connection broke while sending.
            # Other llm_tools return an "Error: ..." string; stay consistent so
            # the agent gets a clean actionable message instead of a raw
            # exception traceback.
            return f"Error: {exc}"
        return (
            f"Started pi background task #{task.task_id}. "
            "Do not poll for status, do not re-send the prompt, and do not abort. "
            "pi may stay silent for minutes while running tools/subagents. "
            "The complete result will be pushed to the chat automatically."
        )

    async def _send_ui_reply(
        self, conn: PiConnection, ui_request: UIRequest, value: str
    ) -> str:
        """Forward a UI reply to pi and return a short status description."""
        method = ui_request.method
        if method == "confirm":
            confirmed = value.strip().lower() in ("yes", "y", "true", "1")
            await conn.confirm_ui_request(ui_request.request_id, confirmed)
            return "confirmed" if confirmed else "declined"
        if method == "select":
            selected = resolve_select_option(ui_request, value)
            if selected is None:
                raise PiError(
                    f"Invalid option '{value}'. "
                    "Use the option text or a 1-based number."
                )
            await conn.reply_ui_request(ui_request.request_id, selected)
            return f"selected '{selected}'"
        await conn.reply_ui_request(ui_request.request_id, value)
        return "replied"

    async def _notify_task_finished(
        self,
        task: PiTask,
        status: str,
        text: str,
        error: str | None,
    ) -> None:
        """Push the task outcome to the chat, then wake the main agent to act on it."""
        umo = task.umo
        if not umo:
            return

        elapsed = round(task.elapsed)
        if status == "completed":
            header = (
                f"✅ pi 任务 #{task.task_id} 完成 "
                f"（用时 {elapsed}s，{task.tool_call_count} 次工具调用）"
            )
        elif status == "failed":
            header = (
                f"❌ pi 任务 #{task.task_id} 失败 "
                f"（用时 {elapsed}s，{task.tool_call_count} 次工具调用）"
            )
        else:
            header = (
                f"⛔ pi 任务 #{task.task_id} 已中止 "
                f"（用时 {elapsed}s，{task.tool_call_count} 次工具调用）"
            )

        messages = [header]
        if error:
            messages.append(f"错误：{error}")
        for start in range(0, len(text), 1500):
            messages.append(text[start : start + 1500])

        for message in messages:
            try:
                chain = MessageChain().message(message)
                await self.context.send_message(umo, chain)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to send task notification: %s", exc)


        # Wake the main agent so it reads the result and decides what to do.
        # Background tasks exist *for* the LLM: without this wake the outcome
        # just sits unread in history until the user happens to speak again.
        wake = asyncio.create_task(
            self._wake_main_agent_for_task_result(task, status, text, error)
        )
        self._wake_tasks.add(wake)
        wake.add_done_callback(self._wake_tasks.discard)

    async def _wake_main_agent_for_task_result(
        self,
        task: PiTask,
        status: str,
        text: str,
        error: str | None,
    ) -> None:
        """把后台任务结果作为一条唤醒事件投回 AstrBot 的事件队列。

        只用公开 API：``Context.get_platform_inst`` / ``Platform.create_event``
        / ``Context.get_event_queue``，并显式置 ``is_wake = True`` —— 等价于
        用户 @ 了机器人或私聊发来消息，主 agent 便会像收到用户消息那样被唤醒去读
        结果。后台任务是跑给 LLM 看的：pi 干完活，就该把 agent 叫起来。
        """
        umo = task.umo
        if not umo:
            return
        try:
            from astrbot.api.message_components import Plain
            from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
        except Exception as exc:  # noqa: BLE001
            logger.warning("pi task wake: imports unavailable: %s", exc)
            return

        try:
            # umo 形如 "platform_id:MessageType:session_id"，自行解析，
            # 免得依赖未公开导出的 MessageSession。
            parts = umo.split(":", 2)
            if len(parts) != 3:
                logger.warning("pi task wake: malformed umo %r", umo)
                return
            platform_id, message_type_str, session_id = parts
            try:
                message_type = MessageType(message_type_str)
            except ValueError:
                logger.warning(
                    "pi task wake: unknown message_type %r", message_type_str
                )
                return
            platform = self.context.get_platform_inst(platform_id)
            if platform is None:
                logger.warning(
                    "pi task wake: platform %r not found for %s", platform_id, umo
                )
                return

            summary = (text or "").strip()
            if not summary and error:
                summary = f"(failed) {error}"
            note = (
                f"[pi background task #{task.task_id} finished: {status}]\n{summary}"
            ).strip()

            msg = AstrBotMessage()
            msg.type = message_type
            msg.self_id = str(getattr(platform, "self_id", "") or "")
            msg.session_id = session_id
            msg.message_id = f"pi-task-{task.task_id}"
            msg.sender = MessageMember(user_id=session_id, nickname="pi")
            msg.message_str = note
            msg.message = [Plain(note)]

            event = platform.create_event(msg)
            event.is_wake = True
            self.context.get_event_queue().put_nowait(event)
            logger.info("Woke main agent via event queue for pi task #%s", task.task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to wake main agent for pi task #%s: %s", task.task_id, exc
            )
    async def _handle_background_ui_request(
        self, task: PiTask, request: UIRequest
    ) -> None:
        """Push a pi UI request to the chat while a background task is paused."""
        umo = task.umo
        if not umo:
            return
        try:
            chain = MessageChain().message(format_ui_request(request))
            await self.context.send_message(umo, chain)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send background UI request notification: %s", exc)

    # ------------------------------------------------------------------
    # /pi command handlers
    # ------------------------------------------------------------------

    @filter.command("pi")
    @filter.permission_type(PermissionType.ADMIN)
    async def pi_handler(self, event: AstrMessageEvent):
        """Dispatch /pi subcommands or treat the message as a natural language prompt."""
        text = strip_command_prefix(event.message_str, "pi")
        subcommand, rest = parse_subcommand(text)

        if subcommand == "open":
            async for item in self._handle_pi_open(event, rest):
                yield item
        elif subcommand == "sessions":
            async for item in self._handle_pi_sessions(event, rest):
                yield item
        elif subcommand == "next":
            async for item in self._handle_pi_next(event):
                yield item
        elif subcommand == "session":
            async for item in self._handle_pi_session_info(event):
                yield item
        elif subcommand == "resume":
            async for item in self._handle_pi_resume(event, rest):
                yield item
        elif subcommand == "quit":
            async for item in self._handle_pi_quit(event):
                yield item
        elif subcommand == "tree":
            async for item in self._handle_pi_tree(event, rest):
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
        elif subcommand == "info":
            async for item in self._handle_pi_session_info(event):
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
        """Handle /pi sessions [dir] and /pi sessions all."""
        stripped = rest.strip()
        if stripped.lower() == "all":
            per_dir_limit = self._get_per_dir_limit()
            try:
                groups = self.pi_connection_manager.list_all_sessions(
                    per_dir_limit=per_dir_limit
                )
                output, selection = format_session_all_list(
                    groups, per_dir_limit=per_dir_limit
                )
                self.pi_connection_manager.set_last_session_selection(event, selection)
                yield event.plain_result(output)
            except PiError as exc:
                yield event.plain_result(f"Error: {exc}")
            return

        directory = stripped or None
        if directory:
            self.pi_connection_manager.set_active_cwd(event, directory)
        else:
            directory = await self.pi_connection_manager.get_active_cwd(event)
            if not directory:
                yield event.plain_result(
                    "Usage: /pi sessions <absolute directory>\n"
                    "Open or resume a session first to list its directory."
                )
                return

        page_size = 10
        try:
            sessions, total = self.pi_connection_manager.list_sessions(
                directory, offset=0, limit=page_size
            )
            self.pi_connection_manager.set_last_sessions_query(
                event,
                directory=directory,
                page=1,
                page_size=page_size,
                total=total,
            )
            selection = {i: info.session_id for i, info in enumerate(sessions, start=1)}
            self.pi_connection_manager.set_last_session_selection(event, selection)
            yield event.plain_result(
                format_session_list(
                    sessions,
                    directory=directory,
                    page=1,
                    page_size=page_size,
                    total=total,
                )
            )
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_next(self, event: AstrMessageEvent):
        """Handle /pi next: show the next page of the last sessions query."""
        query = self.pi_connection_manager.get_last_sessions_query(event)
        if not query:
            yield event.plain_result(
                "No previous sessions list. Use /pi sessions <directory> first."
            )
            return

        directory = query["directory"]
        page = query["page"] + 1
        page_size = query["page_size"]
        offset = (page - 1) * page_size
        start_index = offset + 1
        try:
            sessions, total = self.pi_connection_manager.list_sessions(
                directory, offset=offset, limit=page_size
            )
            if not sessions:
                yield event.plain_result("No more sessions.")
                return
            self.pi_connection_manager.set_last_sessions_query(
                event,
                directory=directory,
                page=page,
                page_size=page_size,
                total=total,
            )
            selection = {
                i: info.session_id for i, info in enumerate(sessions, start=start_index)
            }
            self.pi_connection_manager.set_last_session_selection(event, selection)
            yield event.plain_result(
                format_session_list(
                    sessions,
                    directory=directory,
                    page=page,
                    page_size=page_size,
                    total=total,
                )
            )
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_session_info(self, event: AstrMessageEvent):
        """Handle /pi session (show current session info)."""
        try:
            info = await self.pi_connection_manager.get_session_info(event)
            lines = [format_session_info(info)]
            key = self.pi_connection_manager._session_key(event)
            active = self._task_manager.get_active(key)
            if active is not None:
                elapsed = round(active.elapsed)
                lines.append(
                    f"\nActive background task #{active.task_id} "
                    f"(status: {active.status}, elapsed {elapsed}s). "
                    "pi may stay silent for minutes while running tools or subagents; "
                    "this is normal. Do not poll repeatedly or abort unless the user asks."
                )
            yield event.plain_result("Current pi session:\n" + "\n".join(lines))
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_resume(self, event: AstrMessageEvent, rest: str):
        """Handle /pi resume [id|number]. Without id, resume the most recent session."""
        value = rest.strip()
        session_id: str | None = None

        if value.isdigit():
            number = int(value)
            selection = self.pi_connection_manager.get_last_session_selection(event)
            if selection is not None:
                if number in selection:
                    session_id = selection[number]
                else:
                    yield event.plain_result(
                        f"Selection {number} not found. "
                        "Use /pi sessions to list available sessions."
                    )
                    return

        if session_id is None:
            session_id = value or None

        try:
            info = await self.pi_connection_manager.resume_session(event, session_id)
            source = f"session {session_id}" if session_id else "most recent session"
            yield event.plain_result(f"Resumed {source}.\n{format_session_info(info)}")
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_quit(self, event: AstrMessageEvent):
        """Handle /pi quit: disconnect from the current pi session."""
        conn = await self.pi_connection_manager.get_connection(event, create=False)
        if conn is None:
            yield event.plain_result("No active pi session to disconnect.")
            return
        key = self.pi_connection_manager._session_key(event)
        await self._task_manager.cancel(key, notify=False)
        await self.pi_connection_manager.close_connection(event)
        yield event.plain_result("Disconnected from current pi session.")

    async def _handle_pi_tree(self, event: AstrMessageEvent, rest: str):
        """Handle /pi tree and /pi tree <number>."""
        rest = rest.strip()
        try:
            conn = await self._get_connection(event)
            tree_data = await conn.get_tree()
            tree = tree_data.get("tree", [])
            leaf_id = tree_data.get("leafId")
            branch_entries = extract_active_branch(tree, leaf_id)

            if not rest:
                yield event.plain_result(format_tree_entries(branch_entries))
                return

            try:
                number = int(rest)
            except ValueError:
                yield event.plain_result(
                    "Usage: /pi tree <number>\nPlease provide a valid number."
                )
                return

            entry_id = resolve_tree_entry_id(branch_entries, number)
            if entry_id is None:
                yield event.plain_result(
                    f"Invalid number {number}. Use `/pi tree` to see available entries."
                )
                return

            result = await conn.fork(entry_id)
            if result.get("cancelled"):
                yield event.plain_result("Fork was cancelled by an extension.")
                return

            info = await self.pi_connection_manager.get_session_info(event)
            yield event.plain_result(
                f"Forked from entry {number}.\n{format_session_info(info)}\n"
                "You can now continue chatting from this point."
            )
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_prompt(self, event: AstrMessageEvent, text: str):
        """Handle /pi <natural language>."""
        if not text.strip():
            yield event.plain_result(USAGE)
            return
        key = self.pi_connection_manager._session_key(event)
        active = self._task_manager.get_active(key)
        if active is not None:
            yield event.plain_result(self._active_task_message(active))
            return
        try:
            conn = await self._get_connection(event)
            event_generator = conn.send_prompt(text)
            async for item in self._stream_events(event, event_generator):
                yield item
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    async def _handle_pi_abort(self, event: AstrMessageEvent):
        """Handle /pi abort."""
        try:
            conn = await self._get_connection(event)
            await conn.abort()
            key = self.pi_connection_manager._session_key(event)
            await self._task_manager.cancel(key, notify=True)
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
        if not value.strip():
            yield event.plain_result(
                "Usage: /pi confirm <id> yes|no\nPlease specify yes or no."
            )
            return
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(local_id)
            if ui_request is None:
                yield event.plain_result(f"No pending request with id {local_id}.")
                return
            status = await self._send_ui_reply(conn, ui_request, value)
            reply = f"{status.capitalize()} request #{local_id}."
            key = self.pi_connection_manager._session_key(event)
            if self._task_manager.is_running(key):
                self._task_manager.resume_ui(key)
                yield event.plain_result(
                    f"{reply} The background task will continue and deliver the result automatically."
                )
                return
            yield event.plain_result(reply)
            async for item in self._stream_events(event, conn.read_response()):
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
            status = await self._send_ui_reply(conn, ui_request, value)
            reply = f"{status.capitalize()} for request #{local_id}."
            key = self.pi_connection_manager._session_key(event)
            if self._task_manager.is_running(key):
                self._task_manager.resume_ui(key)
                yield event.plain_result(
                    f"{reply} The background task will continue and deliver the result automatically."
                )
                return
            yield event.plain_result(reply)
            async for item in self._stream_events(event, conn.read_response()):
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
            await self._send_ui_reply(conn, ui_request, value)
            reply = f"Replied to request #{local_id}."
            key = self.pi_connection_manager._session_key(event)
            if self._task_manager.is_running(key):
                self._task_manager.resume_ui(key)
                yield event.plain_result(
                    f"{reply} The background task will continue and deliver the result automatically."
                )
                return
            yield event.plain_result(reply)
            async for item in self._stream_events(event, conn.read_response()):
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
            await self._send_ui_reply(conn, ui_request, value)
            reply = f"Edited text for request #{local_id}."
            key = self.pi_connection_manager._session_key(event)
            if self._task_manager.is_running(key):
                self._task_manager.resume_ui(key)
                yield event.plain_result(
                    f"{reply} The background task will continue and deliver the result automatically."
                )
                return
            yield event.plain_result(reply)
            async for item in self._stream_events(event, conn.read_response()):
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
            reply = f"Cancelled request #{local_id}."
            key = self.pi_connection_manager._session_key(event)
            if self._task_manager.is_running(key):
                self._task_manager.resume_ui(key)
                yield event.plain_result(
                    f"{reply} The background task will continue and deliver the result automatically."
                )
                return
            yield event.plain_result(reply)
        except PiError as exc:
            yield event.plain_result(f"Error: {exc}")

    # ------------------------------------------------------------------
    # /pic command handlers
    # ------------------------------------------------------------------

    @filter.command("pic")
    @filter.permission_type(PermissionType.ADMIN)
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

        key = self.pi_connection_manager._session_key(event)
        active = self._task_manager.get_active(key)
        if active is not None:
            yield event.plain_result(self._active_task_message(active))
            return

        try:
            conn = await self._get_connection(event)
            event_generator = conn.send_prompt(command)
            async for item in self._stream_events(event, event_generator):
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
        if denied := self._require_admin(event):
            return denied
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
        if denied := self._require_admin(event):
            return denied
        try:
            directory = dir
            if not directory:
                directory = await self.pi_connection_manager.get_active_cwd(event)
            if not directory:
                return (
                    "No directory specified and no active session. "
                    "Provide a directory or open/resume a session first."
                )
            sessions, total = self.pi_connection_manager.list_sessions(directory)
            return format_session_list(
                sessions,
                directory=directory,
                page=1,
                page_size=len(sessions),
                total=total,
            )
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_resume_session")
    async def pi_resume_session(
        self, event: AstrMessageEvent, session_id: str = ""
    ) -> str:
        """Resume an existing pi session by its id, file path, or the most recent.

        Args:
            session_id(string): Session id or partial id to resume. Omit to resume the most recent session.
        """
        if denied := self._require_admin(event):
            return denied
        try:
            info = await self.pi_connection_manager.resume_session(
                event, session_id or None
            )
            source = f"session {session_id}" if session_id else "most recent session"
            return f"Resumed {source}.\n{format_session_info(info)}"
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_send_message")
    async def pi_send_message(self, event: AstrMessageEvent, message: str) -> str:
        """Send a natural language message to the current pi session.

        This tool launches a background task and returns immediately. Do not
        poll for status, do not re-send the message, and do not abort. The
        complete result will be pushed to the chat automatically; it will also
        be injected into the conversation history so you see it on the next turn.

        Args:
            message(string): The message to send to pi
        """
        if denied := self._require_admin(event):
            return denied
        return await self._launch_background_task(event, message)

    @filter.llm_tool(name="pi_get_session_info")
    async def pi_get_session_info(self, event: AstrMessageEvent) -> str:
        """Get information about the current pi session.

        If a background task is running, the response includes the task id and
        elapsed time. pi may stay silent for minutes while running tools or
        subagents; this is normal. Do not poll repeatedly or abort unless the
        user explicitly asks.
        """
        if denied := self._require_admin(event):
            return denied
        try:
            info = await self.pi_connection_manager.get_session_info(event)
            result = format_session_info(info)
            key = self.pi_connection_manager._session_key(event)
            active = self._task_manager.get_active(key)
            if active is not None:
                elapsed = round(active.elapsed)
                result += (
                    f"\n\nActive background task #{active.task_id} "
                    f"(status: {active.status}, elapsed {elapsed}s). "
                    "pi may stay silent for minutes while running tools or subagents; "
                    "this is normal. Do NOT poll repeatedly or abort unless the user asks."
                )
            return result
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_run_command")
    async def pi_run_command(self, event: AstrMessageEvent, command: str) -> str:
        """Execute a pi slash command in the current session.

        This tool launches a background task and returns immediately. Do not
        poll for status, do not re-send the command, and do not abort. The
        complete result will be pushed to the chat automatically; it will also
        be injected into the conversation history so you see it on the next turn.

        Args:
            command(string): The slash command to execute (without the leading /)
        """
        if denied := self._require_admin(event):
            return denied
        slash = command if command.startswith("/") else f"/{command}"
        return await self._launch_background_task(event, slash)

    @filter.llm_tool(name="pi_get_available_commands")
    async def pi_get_available_commands(self, event: AstrMessageEvent) -> str:
        """List the slash commands available in the current pi session."""
        if denied := self._require_admin(event):
            return denied
        try:
            conn = await self._get_connection(event)
            commands = await conn.get_commands()
            return format_commands_list(commands)
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_abort")
    async def pi_abort(self, event: AstrMessageEvent, user_confirmed: bool) -> str:
        """Abort the current pi operation.

        Aborting requires explicit user confirmation. Only call this tool when
        the user has explicitly asked to cancel the running task.

        Args:
            user_confirmed(boolean): Must be true to confirm the abort request.
        """
        if denied := self._require_admin(event):
            return denied
        if not user_confirmed:
            return (
                "Abort refused: user_confirmed must be true. "
                "Only abort when the user explicitly asks to cancel."
            )
        try:
            conn = await self._get_connection(event)
            await conn.abort()
            key = self.pi_connection_manager._session_key(event)
            await self._task_manager.cancel(key, notify=True)
            return "Abort request sent to pi."
        except PiError as exc:
            return f"Error: {exc}"

    @filter.llm_tool(name="pi_reply_ui")
    async def pi_reply_ui(
        self, event: AstrMessageEvent, request_id: int, value: str
    ) -> str:
        """Reply to a pending pi extension UI request.

        If a background task is awaiting this UI reply, the reply is forwarded
        to pi and the task resumes. The final result is delivered through the
        background task notification. If no background task is active, the
        resumed event stream is consumed and returned directly.

        Args:
            request_id(number): The local request id shown by pi
            value(string): The reply value (yes/no for confirm, option text or number for select, text for input/editor)
        """
        if denied := self._require_admin(event):
            return denied
        try:
            conn = await self._get_connection(event)
            ui_request = conn.get_ui_request_by_local_id(request_id)
            if ui_request is None:
                return f"No pending request with id {request_id}."

            status = await self._send_ui_reply(conn, ui_request, value)
            key = self.pi_connection_manager._session_key(event)
            if self._task_manager.is_running(key):
                self._task_manager.resume_ui(key)
                return (
                    f"{status.capitalize()} request #{request_id}. "
                    "The background task will continue and deliver the result automatically."
                )

            response = await self._collect_events(conn.read_response())
            return f"{status.capitalize()} request #{request_id}.\n{response}".strip()
        except PiError as exc:
            return f"Error: {exc}"
