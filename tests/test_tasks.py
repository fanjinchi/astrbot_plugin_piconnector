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
        # Mirrors the real connection: only agent_settled ends the run (pi may
        # keep working after agent_end), and exhausting the queue ends it too.
        while True:
            if self._event_queue.empty():
                return
            event = await self._event_queue.get()
            yield event
            etype = event.get("type")
            if etype == "event":
                etype = event.get("event", {}).get("type")
            if etype == "agent_settled":
                break

    async def drain_events(self, timeout=0.5):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def terminate(self):
        self.terminate_called = True


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


class _WatchdogConnection(FakeConnection):
    """Fake whose read_events raises an idle error when the queue runs out.

    Mirrors the real connection: events drain from the queue, and when no
    events remain the configured idle error (normally the PiError raised after
    event_timeout elapses) is raised instead of waiting forever.
    """

    def __init__(self, idle_error=None):
        super().__init__()
        self._idle_error = idle_error

    async def read_events(self, event_timeout=None):
        while True:
            try:
                event = self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                if self._idle_error is not None:
                    raise self._idle_error
                event = await self._event_queue.get()
            yield event


class TestIdleWatchdog:
    @pytest.mark.asyncio
    async def test_timeout_pierror_fails_task_and_drops_connection(self, manager):
        """read_events() turns its idle timeout into PiError; the watchdog must
        fail the task with the wedged message and drop the connection."""
        conn = _WatchdogConnection(
            idle_error=PiError("Timeout waiting for pi response events")
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "failed"
        assert "无任何输出" in task.error
        assert conn.terminate_called is True
        assert manager.get_active("test-key") is None

    @pytest.mark.asyncio
    async def test_other_pierror_does_not_terminate_connection(self, manager):
        """Connection-lost style PiErrors must not terminate the (already
        broken) connection or trample the original error message."""
        conn = _WatchdogConnection(idle_error=PiError("Connection to pi process lost"))
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "failed"
        assert task.error == "Connection to pi process lost"
        assert getattr(conn, "terminate_called", False) is False
        assert manager.get_active("test-key") is None

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
    async def test_completion_accepts_normalized_agent_end(self, manager):
        """agent_end as production actually delivers it.

        The real PiConnection normalizes raw events, so agent_end reaches the
        task runner wrapped as {"type": "event", "event": {...}}. Ignoring that
        shape once made every successful run report
        "pi event stream ended without agent_end".
        """
        conn = FakeConnection(
            events=[
                {"type": "text", "text": "done"},
                {"type": "event", "event": {"type": "agent_end"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "completed"
        assert task.result_text == "done"

    @pytest.mark.asyncio
    async def test_completion_accepts_agent_settled(self, manager):
        """agent_settled is honoured as a terminal event, not just agent_end.

        pi builds differ in which terminal event they emit. An earlier attempt
        to wait *exclusively* for agent_settled hung every background task in
        production (finished turn, then 8+ minutes of silence), so both events
        end the run now.
        """
        conn = FakeConnection(
            events=[
                {"type": "text", "text": "done"},
                {"type": "event", "event": {"type": "agent_settled"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "completed"
        assert task.result_text == "done"

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
