"""Tests for pi_connector/tasks.py."""

import asyncio
from unittest.mock import AsyncMock

import pytest

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector import PiError  # noqa: E402
from pi_connector.models import UIRequest  # noqa: E402
from pi_connector.tasks import PiTaskManager  # noqa: E402
# isort: on


def _session_key(event) -> str:
    """Return a deterministic key for fake events."""
    return "test-key"


@pytest.fixture
def manager():
    """Return a PiTaskManager with mocked callbacks."""
    return PiTaskManager(
        session_key_fn=_session_key,
        on_task_finished=AsyncMock(),
        on_ui_request=AsyncMock(),
    )


class FakeConnection:
    """Scriptable fake pi RPC connection for task tests."""

    def __init__(self, events=None, command_response=None):
        self.events = events or []
        self.command_response = command_response or {"success": True}
        self.sent_commands = []
        self._event_queue = asyncio.Queue()
        for ev in self.events:
            self._event_queue.put_nowait(ev)
        self.pending_ui_requests = {}

    async def send_command(self, command, timeout=30):
        self.sent_commands.append(command)
        return self.command_response

    async def read_events(self, event_timeout=None):
        while True:
            event = await self._event_queue.get()
            yield event
            if event.get("type") == "agent_end":
                break

    async def drain_events(self, timeout=0.5):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break


class TestLaunch:
    @pytest.mark.asyncio
    async def test_launch_creates_task_and_sends_prompt(self, manager):
        conn = FakeConnection(events=[{"type": "agent_end"}])
        task = await manager.launch(conn, "test-key", "hello", "umo:1")
        assert task.task_id == 1
        assert task.status == "running"
        assert task.prompt == "hello"
        assert task.umo == "umo:1"
        assert conn.sent_commands == [{"type": "prompt", "message": "hello"}]

    @pytest.mark.asyncio
    async def test_launch_rejects_second_task(self, manager):
        conn = FakeConnection(events=[])
        await manager.launch(conn, "test-key", "first", "umo:1")
        with pytest.raises(RuntimeError, match="already running"):
            await manager.launch(conn, "test-key", "second", "umo:1")

    @pytest.mark.asyncio
    async def test_launch_propagates_command_failure(self, manager):
        conn = FakeConnection(
            events=[], command_response={"success": False, "error": "rejected"}
        )
        with pytest.raises(RuntimeError, match="rejected"):
            await manager.launch(conn, "test-key", "hello", "umo:1")
        assert manager.get_active("test-key") is None


class TestCompletion:
    @pytest.mark.asyncio
    async def test_completion_callback_receives_result(self, manager):
        conn = FakeConnection(
            events=[
                {"type": "text", "text": "hello "},
                {"type": "text", "text": "world"},
                {"type": "agent_end"},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "completed"
        assert task.result_text == "hello world"
        manager._on_task_finished.assert_awaited()
        call_args = manager._on_task_finished.await_args
        assert call_args.args[1] == "completed"
        assert call_args.args[2] == "hello world"

    @pytest.mark.asyncio
    async def test_failure_path(self, manager):
        async def failing_read_events(event_timeout=None):
            raise PiError("connection lost")
            yield  # noqa: PIE790

        conn = FakeConnection()
        conn.read_events = failing_read_events
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "failed"
        assert "connection lost" in task.error
        manager._on_task_finished.assert_awaited()


class TestAbort:
    @pytest.mark.asyncio
    async def test_cancel_aborts_running_task(self, manager):
        conn = FakeConnection(events=[])
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        cancelled = await manager.cancel("test-key", notify=True)
        assert cancelled is task
        assert task.status == "aborted"

    @pytest.mark.asyncio
    async def test_cancel_all_aborts_all_tasks(self, manager):
        conn = FakeConnection(events=[])
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await manager.cancel_all()
        assert task.status == "aborted"


class TestUiCoordination:
    @pytest.mark.asyncio
    async def test_ui_request_pauses_until_resume(self, manager):
        ui_request = UIRequest(
            local_id=1,
            request_id="req-1",
            method="confirm",
            timeout_ms=1000,
        )
        conn = FakeConnection(
            events=[
                {"type": "ui_request", "request": ui_request},
                {"type": "text", "text": "done"},
                {"type": "agent_end"},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        # Wait until the runner blocks on the UI request; we cannot await the
        # runner directly until we resume it, so poll briefly with a timeout.
        deadline = asyncio.get_running_loop().time() + 1
        while task.status != "awaiting_ui":
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("runner did not reach awaiting_ui")
            await asyncio.sleep(0)
        assert task.status == "awaiting_ui"
        manager._on_ui_request.assert_awaited_once()

        manager.resume_ui("test-key")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "completed"
        assert task.result_text == "done"

    @pytest.mark.asyncio
    async def test_resume_ui_returns_false_when_not_awaiting(self, manager):
        conn = FakeConnection(events=[{"type": "agent_end"}])
        await manager.launch(conn, "test-key", "prompt", "umo:1")
        assert manager.resume_ui("test-key") is False

    @pytest.mark.asyncio
    async def test_tool_execution_events_are_counted(self, manager):
        conn = FakeConnection(
            events=[
                {
                    "type": "event",
                    "event": {
                        "type": "tool_execution_start",
                        "toolName": "x",
                        "args": {},
                    },
                },
                {
                    "type": "event",
                    "event": {
                        "type": "tool_execution_start",
                        "toolName": "y",
                        "args": {},
                    },
                },
                {"type": "agent_end"},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.tool_call_count == 2
