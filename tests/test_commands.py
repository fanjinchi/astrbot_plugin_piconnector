"""Tests for pi_connector/commands.py."""

import asyncio

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector import PiError, PiTaskManager  # noqa: E402
from pi_connector.commands import (  # noqa: E402
    _extract_user_text,
    _filter_user_entries,
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
from pi_connector.connection import HANDLED_PROMPT_NOTICE  # noqa: E402
from pi_connector.models import SessionInfo, UIRequest  # noqa: E402
from pi_connector.tasks import PiTask  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402
# isort: on

import pytest


class TestStripCommandPrefix:
    def test_removes_slash_prefix(self):
        assert strip_command_prefix("/pi open", "pi") == "open"

    def test_removes_plain_prefix(self):
        assert strip_command_prefix("pi open", "pi") == "open"

    def test_no_match_returns_unchanged(self):
        assert strip_command_prefix("hello", "pi") == "hello"

    def test_strips_surrounding_whitespace(self):
        assert strip_command_prefix("  /pi open  ", "pi") == "open"


class TestParseSubcommand:
    def test_empty_returns_empty(self):
        assert parse_subcommand("") == ("", "")

    def test_single_word(self):
        assert parse_subcommand("open") == ("open", "")

    def test_lowercase_subcommand(self):
        assert parse_subcommand("OPEN path") == ("open", "path")

    def test_with_rest(self):
        assert parse_subcommand("resume abc123 extra") == ("resume", "abc123 extra")


class TestFormatSessionInfo:
    def test_basic_format(self):
        info = SessionInfo(
            session_id="sid-1",
            session_file="/tmp/session.jsonl",
            cwd="/home/user/project",
            session_name="my session",
            message_count=42,
        )
        output = format_session_info(info)
        expected = (
            "Session: sid-1\n"
            "Name: my session\n"
            "Working directory: /home/user/project\n"
            "File: /tmp/session.jsonl\n"
            "Messages: 42"
        )
        assert output == expected

    def test_unnamed_placeholder(self):
        info = SessionInfo(
            session_id="sid-2",
            session_file="/tmp/session.jsonl",
            cwd="/home/user/project",
            message_count=0,
        )
        assert "Name: (unnamed)" in format_session_info(info)

    def test_thinking_level_appended(self):
        info = SessionInfo(
            session_id="sid-3",
            session_file="/tmp/session.jsonl",
            cwd="/home/user/project",
            thinking_level="high",
        )
        assert "Thinking level: high" in format_session_info(info)


class TestFormatSessionList:
    def test_empty_list(self):
        assert format_session_list([]) == "No sessions found."

    def test_multiple_sessions(self):
        sessions = [
            SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
                first_message_snippet="hello world",
            ),
            SessionInfo(
                session_id="sid-2",
                session_file="/tmp/b.jsonl",
                cwd="/home/b",
                message_count=2,
            ),
        ]
        output = format_session_list(sessions, total=2)
        assert "Available sessions:" in output
        assert "1. alpha" in output
        assert "2. (unnamed)" in output
        assert "ID: sid-2" in output
        assert "Created: unknown" in output
        assert "First message: hello world" in output
        assert "First message: (no preview)" in output
        assert "CWD: /home/a" in output
        assert "CWD: /home/b" in output

    def test_hides_cwd_when_directory_given(self):
        sessions = [
            SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        ]
        output = format_session_list(sessions, directory="/home/a", total=1)
        assert "CWD:" not in output
        assert "First message:" in output

    def test_single_page_no_footer(self):
        sessions = [
            SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        ]
        output = format_session_list(sessions, total=1)
        assert "Page" not in output
        assert "Use /pi next" not in output
        assert "End of list" not in output

    def test_pagination_footer(self):
        sessions = [
            SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        ]
        output = format_session_list(sessions, page=1, page_size=10, total=25)
        assert "Page 1/3" in output
        assert "Use /pi next to view more" in output

    def test_last_page_footer(self):
        sessions = [
            SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        ]
        output = format_session_list(sessions, page=3, page_size=10, total=25)
        assert "Page 3/3" in output
        assert "End of list" in output

    def test_start_index_respects_page(self):
        sessions = [
            SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        ]
        output = format_session_list(sessions, page=2, page_size=10, total=11)
        assert "11. alpha" in output


class TestFormatUiRequest:
    def test_confirm(self):
        req = UIRequest(local_id=1, request_id="r1", method="confirm", title="Ok?")
        output = format_ui_request(req)
        assert "[Request #1]" in output
        assert "Title: Ok?" in output
        assert "/pi confirm 1 yes" in output
        assert "/pi confirm 1 no" in output

    def test_select(self):
        req = UIRequest(
            local_id=2,
            request_id="r2",
            method="select",
            message="Choose",
            options=["a", "b"],
        )
        output = format_ui_request(req)
        assert "Options:" in output
        assert "1. a" in output
        assert "2. b" in output
        assert "/pi select 2 <option>" in output

    def test_input(self):
        req = UIRequest(local_id=3, request_id="r3", method="input")
        assert "/pi input 3 <value>" in format_ui_request(req)

    def test_editor_with_prefill(self):
        req = UIRequest(
            local_id=4,
            request_id="r4",
            method="editor",
            prefill="hello",
        )
        output = format_ui_request(req)
        assert "Prefill: hello" in output
        assert "/pi edit 4 <text>" in output

    def test_unknown_method_defaults_to_input(self):
        req = UIRequest(local_id=5, request_id="r5", method="unknown")
        assert "/pi input 5 <value>" in format_ui_request(req)


class TestFormatCommandsList:
    def test_empty(self):
        assert format_commands_list([]) == "No slash commands available."

    def test_with_descriptions(self):
        commands = [
            {"name": "explore", "description": "Explore", "source": "pi"},
            {"name": "ask", "description": "Ask", "source": "pi"},
        ]
        output = format_commands_list(commands)
        assert output.startswith("Available pi commands:\n\n")
        assert "/explore (pi) ➡️ Explore" in output
        assert "/ask (pi) ➡️ Ask" in output
        # Two command lines separated by a blank line -> a single blank line between them.
        assert "Explore\n\n/ask" in output

    def test_without_description(self):
        commands = [{"name": "custom", "description": "", "source": "builtin"}]
        assert "/custom (builtin)" in format_commands_list(commands)


class TestResolveSelectOption:
    @pytest.fixture
    def select_request(self):
        return UIRequest(
            local_id=1,
            request_id="r1",
            method="select",
            options=["apple", "banana"],
        )

    def test_no_options_returns_value(self):
        req = UIRequest(local_id=1, request_id="r1", method="select")
        assert resolve_select_option(req, "anything") == "anything"

    def test_exact_match(self, select_request):
        assert resolve_select_option(select_request, "banana") == "banana"

    def test_index_match(self, select_request):
        assert resolve_select_option(select_request, "2") == "banana"

    def test_invalid_returns_none(self, select_request):
        assert resolve_select_option(select_request, "3") is None
        assert resolve_select_option(select_request, "grape") is None


class TestParseUiReplyArgs:
    def test_empty_returns_none(self):
        assert parse_ui_reply_args("") == (None, "")

    def test_only_id(self):
        assert parse_ui_reply_args("7") == (7, "")

    def test_id_and_value(self):
        assert parse_ui_reply_args("7 yes") == (7, "yes")

    def test_id_and_value_with_spaces(self):
        assert parse_ui_reply_args("7 yes please") == (7, "yes please")

    def test_invalid_id_returns_raw(self):
        assert parse_ui_reply_args("abc yes") == (None, "abc yes")


class TestExtractActiveBranch:
    def test_empty_tree(self):
        assert extract_active_branch([], "leaf") == []

    def test_no_leaf_id(self):
        tree = [{"entry": {"id": "root"}, "children": []}]
        assert extract_active_branch(tree, None) == []

    def test_single_root(self):
        tree = [{"entry": {"id": "root"}, "children": []}]
        assert extract_active_branch(tree, "root") == [{"id": "root"}]

    def test_branch_from_root_to_leaf(self):
        tree = [
            {
                "entry": {"id": "root"},
                "children": [
                    {
                        "entry": {"id": "child", "parentId": "root"},
                        "children": [
                            {
                                "entry": {"id": "leaf", "parentId": "child"},
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ]
        branch = extract_active_branch(tree, "leaf")
        assert [e["id"] for e in branch] == ["root", "child", "leaf"]

    def test_missing_node_breaks_loop(self):
        tree = [{"entry": {"id": "root"}, "children": []}]
        assert extract_active_branch(tree, "missing") == []

    def test_cycle_avoided(self):
        tree = [
            {
                "entry": {"id": "a", "parentId": "c"},
                "children": [
                    {
                        "entry": {"id": "b", "parentId": "a"},
                        "children": [
                            {
                                "entry": {"id": "c", "parentId": "b"},
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ]
        branch = extract_active_branch(tree, "c")
        assert [e["id"] for e in branch] == ["a", "b", "c"]


class TestFilterUserEntries:
    def test_filters_non_message(self):
        entries = [
            {"type": "action"},
            {"type": "message", "message": {"role": "user"}},
        ]
        assert _filter_user_entries(entries) == [entries[1]]

    def test_excludes_assistant(self):
        entries = [{"type": "message", "message": {"role": "assistant"}}]
        assert _filter_user_entries(entries) == []


class TestExtractUserText:
    def test_string_content(self):
        entry = {"message": {"content": "Hello\nworld"}}
        assert _extract_user_text(entry) == "Hello world"

    def test_text_blocks(self):
        entry = {
            "message": {
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ]
            }
        }
        assert _extract_user_text(entry) == "Hello world"

    def test_empty_returns_placeholder(self):
        assert _extract_user_text({"message": {}}) == "(empty)"

    def test_truncation(self):
        entry = {"message": {"content": "a" * 100}}
        assert _extract_user_text(entry, max_length=20).endswith("…")


class TestFormatTreeEntries:
    def test_no_user_messages(self):
        assert format_tree_entries([]) == "No user messages on the active branch."

    def test_user_messages_formatted(self):
        entries = [
            {
                "type": "message",
                "message": {"role": "user", "content": "hello"},
                "id": "e1",
                "timestamp": "2026-07-01T12:00:00",
            }
        ]
        output = format_tree_entries(entries)
        assert "User messages on the active branch:" in output
        assert "1. 2026-07-01 hello" in output
        assert "/pi tree <number>" in output


class TestResolveTreeEntryId:
    def test_valid_number(self):
        entries = [
            {"type": "message", "message": {"role": "user"}, "id": "e1"},
            {"type": "message", "message": {"role": "user"}, "id": "e2"},
        ]
        assert resolve_tree_entry_id(entries, 2) == "e2"

    def test_invalid_number(self):
        entries = [
            {"type": "message", "message": {"role": "user"}, "id": "e1"},
        ]
        assert resolve_tree_entry_id(entries, 0) is None
        assert resolve_tree_entry_id(entries, 5) is None


class TestSessionHandlers:
    """Tests for /pi sessions and /pi next handler logic."""

    @pytest.mark.asyncio
    async def test_sessions_handler_lists_first_page(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.list_sessions.return_value = (
            [
                SessionInfo(
                    session_id="sid-1",
                    session_file="/tmp/a.jsonl",
                    cwd="/home/a",
                    session_name="alpha",
                    message_count=1,
                    timestamp="2026-07-01",
                )
            ],
            1,
        )
        results = [r async for r in plugin._handle_pi_sessions(admin_event, "/home/a")]
        assert len(results) == 1
        assert "Available sessions" in results[0]
        plugin.pi_connection_manager.set_active_cwd.assert_called_once_with(
            admin_event, "/home/a"
        )
        plugin.pi_connection_manager.set_last_sessions_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_sessions_handler_uses_active_cwd(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_active_cwd = AsyncMock(return_value="/home/a")
        plugin.pi_connection_manager.list_sessions.return_value = ([], 0)
        results = [r async for r in plugin._handle_pi_sessions(admin_event, "")]
        assert len(results) == 1
        assert "No sessions found" in results[0]
        plugin.pi_connection_manager.list_sessions.assert_called_once_with(
            "/home/a", offset=0, limit=10
        )

    @pytest.mark.asyncio
    async def test_next_handler_returns_next_page(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_last_sessions_query.return_value = {
            "directory": "/home/a",
            "page": 1,
            "page_size": 10,
            "total": 25,
        }
        plugin.pi_connection_manager.list_sessions.return_value = (
            [
                SessionInfo(
                    session_id="sid-11",
                    session_file="/tmp/a.jsonl",
                    cwd="/home/a",
                    session_name="kappa",
                    message_count=1,
                    timestamp="2026-07-01",
                )
            ],
            25,
        )
        results = [r async for r in plugin._handle_pi_next(admin_event)]
        assert len(results) == 1
        assert "Available sessions" in results[0]
        assert "Page 2/3" in results[0]
        plugin.pi_connection_manager.list_sessions.assert_called_once_with(
            "/home/a", offset=10, limit=10
        )

    @pytest.mark.asyncio
    async def test_sessions_all_handler_lists_grouped_sessions(
        self, plugin, admin_event
    ):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.list_all_sessions.return_value = [
            (
                "/home/a",
                [
                    SessionInfo(
                        session_id="sid-1",
                        session_file="/tmp/a.jsonl",
                        cwd="/home/a",
                        session_name="alpha",
                        message_count=1,
                        timestamp="2026-07-01",
                    )
                ],
                1,
            )
        ]
        results = [r async for r in plugin._handle_pi_sessions(admin_event, "all")]
        assert len(results) == 1
        assert "All sessions" in results[0]
        assert "/home/a" in results[0]
        assert "alpha" in results[0]
        plugin.pi_connection_manager.list_all_sessions.assert_called_once_with(
            per_dir_limit=10
        )

    @pytest.mark.asyncio
    async def test_sessions_all_handler_uses_config_limit(self, plugin, admin_event):
        plugin.plugin_config = {"sessions_all_per_dir_limit": 5}
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.list_all_sessions.return_value = []
        results = [r async for r in plugin._handle_pi_sessions(admin_event, "all")]
        assert len(results) == 1
        assert "No sessions found" in results[0]
        plugin.pi_connection_manager.list_all_sessions.assert_called_once_with(
            per_dir_limit=5
        )

    @pytest.mark.asyncio
    async def test_sessions_handler_stores_selection(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.list_sessions.return_value = (
            [
                SessionInfo(
                    session_id="sid-1",
                    session_file="/tmp/a.jsonl",
                    cwd="/home/a",
                    session_name="alpha",
                    message_count=1,
                    timestamp="2026-07-01",
                ),
                SessionInfo(
                    session_id="sid-2",
                    session_file="/tmp/b.jsonl",
                    cwd="/home/a",
                    session_name="beta",
                    message_count=2,
                    timestamp="2026-07-02",
                ),
            ],
            2,
        )
        results = [r async for r in plugin._handle_pi_sessions(admin_event, "/home/a")]
        assert len(results) == 1
        plugin.pi_connection_manager.set_last_session_selection.assert_called_once_with(
            admin_event, {1: "sid-1", 2: "sid-2"}
        )

    @pytest.mark.asyncio
    async def test_sessions_all_handler_stores_selection(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.list_all_sessions.return_value = [
            (
                "/home/a",
                [
                    SessionInfo(
                        session_id="sid-1",
                        session_file="/tmp/a.jsonl",
                        cwd="/home/a",
                        session_name="alpha",
                        message_count=1,
                        timestamp="2026-07-01",
                    )
                ],
                1,
            ),
            (
                "/home/b",
                [
                    SessionInfo(
                        session_id="sid-2",
                        session_file="/tmp/b.jsonl",
                        cwd="/home/b",
                        session_name="beta",
                        message_count=1,
                        timestamp="2026-07-02",
                    )
                ],
                1,
            ),
        ]
        results = [r async for r in plugin._handle_pi_sessions(admin_event, "all")]
        assert len(results) == 1
        plugin.pi_connection_manager.set_last_session_selection.assert_called_once_with(
            admin_event, {1: "sid-1", 2: "sid-2"}
        )

    @pytest.mark.asyncio
    async def test_resume_handler_uses_numeric_selection(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_last_session_selection.return_value = {
            1: "sid-1"
        }
        plugin.pi_connection_manager.resume_session = AsyncMock(
            return_value=SessionInfo(
                session_id="sid-1",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        )
        results = [r async for r in plugin._handle_pi_resume(admin_event, "1")]
        assert len(results) == 1
        assert "sid-1" in results[0]
        plugin.pi_connection_manager.resume_session.assert_called_once_with(
            admin_event, "sid-1"
        )

    @pytest.mark.asyncio
    async def test_resume_handler_invalid_number_returns_error(
        self, plugin, admin_event
    ):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_last_session_selection.return_value = {
            1: "sid-1"
        }
        plugin.pi_connection_manager.resume_session = AsyncMock()
        results = [r async for r in plugin._handle_pi_resume(admin_event, "99")]
        assert len(results) == 1
        assert "not found" in results[0].lower()
        plugin.pi_connection_manager.resume_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_handler_fallback_to_id_without_selection(
        self, plugin, admin_event
    ):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_last_session_selection.return_value = None
        plugin.pi_connection_manager.resume_session = AsyncMock(
            return_value=SessionInfo(
                session_id="abc",
                session_file="/tmp/a.jsonl",
                cwd="/home/a",
                session_name="alpha",
                message_count=1,
                timestamp="2026-07-01",
            )
        )
        results = [r async for r in plugin._handle_pi_resume(admin_event, "abc")]
        assert len(results) == 1
        plugin.pi_connection_manager.resume_session.assert_called_once_with(
            admin_event, "abc"
        )

    @pytest.mark.asyncio
    async def test_resume_handler_refuses_while_task_runs(self, plugin, admin_event):
        """切会话会换掉连接与事件队列，运行中的任务就丢了输出。"""
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.resume_session = AsyncMock()
        active = PiTask(
            task_id=3,
            status="running",
            prompt="hello",
            started_at=0.0,
            umo="test:umo",
            ui_resolved=asyncio.Event(),
        )
        plugin._task_manager.get_active.return_value = active

        results = [r async for r in plugin._handle_pi_resume(admin_event, "abc")]

        assert len(results) == 1
        assert "#3" in results[0]
        assert "/pi abort" in results[0]
        plugin.pi_connection_manager.resume_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_handler_refuses_while_task_runs(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.open_session = AsyncMock()
        plugin._task_manager.get_active.return_value = PiTask(
            task_id=4,
            status="running",
            prompt="hello",
            started_at=0.0,
            umo="test:umo",
            ui_resolved=asyncio.Event(),
        )

        results = [r async for r in plugin._handle_pi_open(admin_event, "/tmp/x")]

        assert "#4" in results[0]
        plugin.pi_connection_manager.open_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_quit_handler_disconnects_active_session(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_connection = AsyncMock(
            return_value=MagicMock()
        )
        plugin.pi_connection_manager.close_connection = AsyncMock()
        plugin._task_manager = MagicMock()
        plugin._task_manager.cancel = AsyncMock(return_value=None)
        results = [r async for r in plugin._handle_pi_quit(admin_event)]
        assert len(results) == 1
        assert "Disconnected" in results[0]
        plugin.pi_connection_manager.close_connection.assert_called_once_with(
            admin_event
        )
        plugin._task_manager.cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_quit_handler_without_active_session(self, plugin, admin_event):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=None)
        plugin.pi_connection_manager.close_connection = AsyncMock()
        results = [r async for r in plugin._handle_pi_quit(admin_event)]
        assert len(results) == 1
        assert "No active" in results[0]
        plugin.pi_connection_manager.close_connection.assert_not_called()


class _AbortFakeConnection:
    """Minimal pi connection that acknowledges a prompt and then stays silent."""

    def __init__(self, abort_error: Exception | None = None):
        self.process = object()
        self.send_command = AsyncMock(
            return_value={"success": True, "data": {"disposition": "started"}}
        )
        self.abort = AsyncMock(side_effect=abort_error)
        self.drain_events = AsyncMock()
        self.streaming = asyncio.Event()

    async def read_events(self, event_timeout=None):
        self.streaming.set()
        await asyncio.Event().wait()  # nothing ever arrives; cancel() ends it
        yield  # noqa: PIE790


class TestPiPromptHandler:
    """A prompt pi handled itself must still answer the user."""

    @pytest.mark.asyncio
    async def test_handled_prompt_replies_with_local_notice(
        self, plugin, admin_event
    ):
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager._session_key = MagicMock(return_value="test:umo")
        conn = MagicMock()
        conn.process = object()

        async def send_prompt(
            text, images=None, streaming_behavior=None, event_timeout=300
        ):
            yield {"type": "text", "text": HANDLED_PROMPT_NOTICE}

        conn.send_prompt = send_prompt
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=conn)

        results = [r async for r in plugin._handle_pi_prompt(admin_event, "/auto")]

        assert results == [HANDLED_PROMPT_NOTICE]


class TestAbortHandler:
    """`/pi abort` must settle the local task even when pi is unreachable."""

    def _wire(self, plugin):
        finished = AsyncMock()
        manager = PiTaskManager(
            session_key_fn=lambda event: event.unified_msg_origin,
            on_task_finished=finished,
            on_ui_request=AsyncMock(),
        )
        plugin._task_manager = manager
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager._session_key = MagicMock(return_value="test:umo")
        return manager, finished

    async def _launch_running(self, manager, conn):
        task = await manager.launch(conn, "test:umo", "do work", "test:umo")
        # Let the runner enter read_events so cancel() goes through its
        # `finally` (that is where the single notification is sent).
        await asyncio.wait_for(conn.streaming.wait(), timeout=1)
        return task

    @pytest.mark.asyncio
    async def test_abort_failure_still_settles_task_once(self, plugin, admin_event):
        """pi already gone: abort() raises, yet the task leaves _active."""
        manager, finished = self._wire(plugin)
        conn = _AbortFakeConnection(abort_error=PiError("pi process is gone"))
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=conn)
        task = await self._launch_running(manager, conn)
        assert plugin._active_task_block(admin_event) is not None

        results = [r async for r in plugin._handle_pi_abort(admin_event)]

        assert task.status == "aborted"
        assert manager.get_active("test:umo") is None
        assert plugin._active_task_block(admin_event) is None
        assert len(results) == 1
        assert "pi process is gone" in results[0]
        assert "cancelled locally" in results[0]
        conn.abort.assert_awaited_once()
        finished.assert_awaited_once()
        assert finished.await_args.args[1] == "aborted"

    @pytest.mark.asyncio
    async def test_abort_without_connection_settles_task_too(self, plugin, admin_event):
        """No usable connection at all: same teardown, same single notice."""
        manager, finished = self._wire(plugin)
        conn = _AbortFakeConnection()
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=None)
        task = await self._launch_running(manager, conn)

        results = [r async for r in plugin._handle_pi_abort(admin_event)]

        assert task.status == "aborted"
        assert plugin._active_task_block(admin_event) is None
        assert "No active pi session" in results[0]
        finished.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_abort_success_reports_acknowledgement(self, plugin, admin_event):
        manager, finished = self._wire(plugin)
        conn = _AbortFakeConnection()
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=conn)
        task = await self._launch_running(manager, conn)

        results = [r async for r in plugin._handle_pi_abort(admin_event)]

        assert results == ["Abort request sent to pi."]
        assert task.status == "aborted"
        conn.abort.assert_awaited_once()
        finished.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_abort_notice_states_uncertainty_only_after_failed_ack(
        self, plugin, admin_event
    ):
        """abort ack 超时/失败时 pi 可能仍在跑，推送必须说明；成功路径文案不变。"""
        manager = PiTaskManager(
            session_key_fn=lambda event: event.unified_msg_origin,
            on_task_finished=plugin._notify_task_finished,
            on_ui_request=AsyncMock(),
        )
        plugin._task_manager = manager
        plugin.pi_connection_manager = MagicMock()
        plugin.pi_connection_manager._session_key = MagicMock(return_value="test:umo")

        def pushes():
            return [
                call.args[1]._messages[0]
                for call in plugin.context.send_message.call_args_list
            ]

        # Failed ack: pi never confirmed, so its turn may still be running.
        failing = _AbortFakeConnection(
            abort_error=PiError("Timeout waiting for pi to abort")
        )
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=failing)
        failed_task = await self._launch_running(manager, failing)
        [r async for r in plugin._handle_pi_abort(admin_event)]
        await asyncio.sleep(0)

        failed = pushes()
        headers = [m for m in failed if m.startswith("⛔")]
        caveats = [m for m in failed if "pi 侧可能仍在执行该回合" in m]
        assert len(headers) == 1
        assert len(caveats) == 1
        assert failed_task.abort_uncertain is True

        # Acknowledged abort: same header, no caveat, still exactly one push.
        plugin.context.send_message.reset_mock()
        ok = _AbortFakeConnection()
        plugin.pi_connection_manager.get_connection = AsyncMock(return_value=ok)
        ok_task = await self._launch_running(manager, ok)
        [r async for r in plugin._handle_pi_abort(admin_event)]
        await asyncio.sleep(0)

        acknowledged = pushes()
        assert len([m for m in acknowledged if m.startswith("⛔")]) == 1
        assert not [m for m in acknowledged if "pi 侧可能仍在执行该回合" in m]
        assert ok_task.abort_uncertain is False
        assert acknowledged != failed


class TestFormatSessionAllList:
    """Tests for format_session_all_list."""

    def test_empty_groups(self):
        output, mapping = format_session_all_list([])
        assert output == "No sessions found."
        assert mapping == {}

    def test_multiple_directories(self):
        groups = [
            (
                "/home/project-a",
                [
                    SessionInfo(
                        session_id="a-3",
                        session_file="/tmp/a-3.jsonl",
                        cwd="/home/project-a",
                        session_name="alpha-latest",
                        message_count=3,
                        timestamp="2026-01-03",
                        first_message_snippet="a-three",
                    )
                ],
                1,
            ),
            (
                "/home/project-b",
                [
                    SessionInfo(
                        session_id="b-2",
                        session_file="/tmp/b-2.jsonl",
                        cwd="/home/project-b",
                        session_name="beta-latest",
                        message_count=5,
                        timestamp="2026-02-02",
                        first_message_snippet="b-two",
                    )
                ],
                2,
            ),
        ]
        output, mapping = format_session_all_list(groups, per_dir_limit=10)
        assert mapping == {1: "a-3", 2: "b-2"}
        assert "All sessions" in output
        assert "per directory" in output
        assert "/home/project-a" in output
        assert "/home/project-b" in output
        assert "1. alpha-latest" in output
        assert "2. beta-latest" in output
        assert "a-three" in output
        assert "b-two" in output

    def test_limit_truncation(self):
        sessions = [
            SessionInfo(
                session_id=f"s{i}",
                session_file=f"/tmp/s{i}.jsonl",
                cwd="/home/project",
                session_name=f"session{i}",
                message_count=i,
                timestamp=f"2026-03-0{i}",
                first_message_snippet=f"msg{i}",
            )
            for i in range(1, 4)
        ]
        groups = [("/home/project", sessions, 5)]
        output, mapping = format_session_all_list(groups, per_dir_limit=3)
        assert mapping == {1: "s3", 2: "s2", 3: "s1"}
        assert "latest 3 per directory" in output
        assert "5" in output
        assert "s3" in output
        assert "s1" in output
        assert output.count("/home/project") == 1
        assert output.count("session1") == 1
        assert output.count("session2") == 1
        assert output.count("session3") == 1

    def test_zero_limit_returns_empty(self):
        groups = [
            (
                "/home/project",
                [
                    SessionInfo(
                        session_id="x",
                        session_file="/tmp/x.jsonl",
                        cwd="/home/project",
                        session_name="x",
                        message_count=1,
                        timestamp="2026-01-01",
                    )
                ],
                1,
            )
        ]
        output, mapping = format_session_all_list(groups, per_dir_limit=0)
        assert mapping == {}
        assert "/home/project" in output
        assert "ID: x" not in output
