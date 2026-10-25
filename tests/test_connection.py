"""Tests for pi_connector/connection.py (mocked sync/async parts)."""

import asyncio
from unittest.mock import AsyncMock

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector.connection import PiConnection, PiError  # noqa: E402
from pi_connector.models import UIRequest  # noqa: E402
# isort: on

import pytest


class TestPiConnectionId:
    """Tests for the small synchronous ID generators."""

    def test_next_command_id(self):
        conn = PiConnection()
        id1 = conn._next_command_id()
        id2 = conn._next_command_id()
        assert id1 != id2
        assert id1.startswith("pi-cmd-1-")
        assert id2.startswith("pi-cmd-2-")

    def test_next_ui_local_id(self):
        conn = PiConnection()
        assert conn._next_ui_local_id() == 1
        assert conn._next_ui_local_id() == 2
        assert conn._next_ui_local_id() == 3


class TestPiConnectionEvents:
    """Tests for event handling and normalization without a real pi process."""

    @pytest.fixture
    def conn(self):
        return PiConnection()

    @pytest.mark.asyncio
    async def test_handle_event_response(self, conn):
        future = asyncio.get_running_loop().create_future()
        conn._pending_responses["resp-1"] = future
        event = {"type": "response", "id": "resp-1", "data": "ok"}
        await conn._handle_event(event)
        assert future.done()
        assert future.result() == event
        assert "resp-1" not in conn._pending_responses

    @pytest.mark.asyncio
    async def test_handle_event_extension_ui_request(self, conn):
        event = {
            "type": "extension_ui_request",
            "id": "req-1",
            "method": "confirm",
            "title": "Confirm?",
            "message": "Are you sure?",
        }
        await conn._handle_event(event)
        assert "req-1" in conn.pending_ui_requests
        req = conn.pending_ui_requests["req-1"]
        assert req.local_id == 1
        assert req.method == "confirm"
        assert req.title == "Confirm?"

    @pytest.mark.asyncio
    async def test_track_ui_request_ignores_non_interactive(self, conn):
        event = {
            "type": "extension_ui_request",
            "id": "req-2",
            "method": "setStatus",
        }
        conn._track_ui_request(event)
        assert "req-2" not in conn.pending_ui_requests

    @pytest.mark.asyncio
    async def test_get_ui_request_by_local_id(self, conn):
        event = {
            "type": "extension_ui_request",
            "id": "req-3",
            "method": "select",
            "options": ["a", "b"],
        }
        await conn._handle_event(event)
        req = conn.get_ui_request_by_local_id(1)
        assert req is not None
        assert req.request_id == "req-3"
        assert conn.get_ui_request_by_local_id(999) is None

    @pytest.mark.asyncio
    async def test_normalize_event_text_delta(self, conn):
        event = {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "hello"},
        }
        result = conn._normalize_event(event)
        assert result == {"type": "text", "text": "hello"}

    @pytest.mark.asyncio
    async def test_normalize_event_thinking_delta(self, conn):
        event = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "thinking_delta",
                "delta": "thinking",
            },
        }
        result = conn._normalize_event(event)
        assert result == {"type": "thinking", "text": "thinking"}

    @pytest.mark.asyncio
    async def test_normalize_event_ui_request(self, conn):
        conn.pending_ui_requests["req-4"] = UIRequest(
            local_id=7,
            request_id="req-4",
            method="input",
        )
        event = {
            "type": "extension_ui_request",
            "id": "req-4",
        }
        result = conn._normalize_event(event)
        assert result["type"] == "ui_request"
        assert result["request"].local_id == 7
        assert result["request"].method == "input"

    @pytest.mark.asyncio
    async def test_normalize_event_fallback(self, conn):
        event = {"type": "unknown", "data": "x"}
        result = conn._normalize_event(event)
        assert result == {"type": "event", "event": event}

    @pytest.mark.asyncio
    async def test_handle_event_puts_non_response_on_queue(self, conn):
        event = {"type": "agent_end"}
        await conn._handle_event(event)
        queued = conn._event_queue.get_nowait()
        assert queued == event

    @pytest.mark.asyncio
    async def test_handle_event_drops_fire_and_forget_ui_notifications(self, conn):
        """Status pings must not reach the queue.

        While streaming, `setTitle` arrives about every 80ms — enough to reset the
        task idle watchdog forever (a run wedged behind a dialog never tripped it)
        and to fill the unbounded queue with tens of thousands of records.
        """
        for method in (
            "setTitle",
            "setStatus",
            "setWidget",
            "set_editor_text",
            "notify",
        ):
            await conn._handle_event(
                {"type": "extension_ui_request", "id": f"n-{method}", "method": method}
            )
        assert conn._event_queue.empty()

    @pytest.mark.asyncio
    async def test_handle_event_keeps_interactive_ui_requests(self, conn):
        event = {
            "type": "extension_ui_request",
            "id": "req-9",
            "method": "confirm",
            "title": "Confirm?",
        }
        await conn._handle_event(event)
        queued = conn._event_queue.get_nowait()
        assert queued == event
        assert "req-9" in conn.pending_ui_requests

    @pytest.mark.asyncio
    async def test_handle_event_marks_unawaited_handled_prompt(self, conn):
        """A late `prompt` response becomes a sentinel the runner can consume.

        An extension dialog blocks the acknowledgment past the launch window, so
        nobody is waiting for the response any more. If its disposition is
        `handled`, pi started no run and no `agent_settled` will follow
        (pi docs/rpc.md:67) — the runner must be told to stop.
        """
        await conn._handle_event(
            {
                "type": "response",
                "id": "pi-cmd-9",
                "success": True,
                "data": {"disposition": "handled"},
            }
        )
        normalized = []
        while not conn._event_queue.empty():
            normalized.append(conn._normalize_event(conn._event_queue.get_nowait()))
        assert normalized == [{"type": "event", "event": {"type": "prompt_handled"}}]

    @pytest.mark.asyncio
    async def test_handle_event_marks_unawaited_rejected_prompt(self, conn):
        await conn._handle_event(
            {
                "type": "response",
                "id": "pi-cmd-10",
                "success": False,
                "error": "boom",
            }
        )

        async def consume():
            return [ev async for ev in conn.read_events(event_timeout=1)]

        with pytest.raises(PiError, match="boom"):
            await asyncio.wait_for(consume(), timeout=2)

    @pytest.mark.asyncio
    async def test_read_events_stops_on_prompt_handled(self, conn):
        await conn._event_queue.put({"type": "prompt_handled"})
        await conn._event_queue.put({"type": "agent_settled"})

        async def collect():
            return [ev async for ev in conn.read_events(event_timeout=1)]

        result = await asyncio.wait_for(collect(), timeout=2)
        assert result == [{"type": "event", "event": {"type": "prompt_handled"}}]
        assert conn._event_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_read_events_yields_normalized_events_until_agent_settled(self, conn):
        events = [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "hello"},
            },
            {"type": "agent_settled"},
        ]
        for ev in events:
            await conn._event_queue.put(ev)
        result = []
        async for ev in conn.read_events():
            result.append(ev)
        assert result == [
            {"type": "text", "text": "hello"},
            {"type": "event", "event": {"type": "agent_settled"}},
        ]

    @pytest.mark.asyncio
    async def test_read_events_waits_indefinitely_when_timeout_is_none(self, conn):
        async def producer():
            await asyncio.sleep(0.05)
            await conn._event_queue.put({"type": "agent_settled"})

        task = asyncio.create_task(producer())
        result = []
        async for ev in conn.read_events(event_timeout=None):
            result.append(ev)
        await task
        assert result == [{"type": "event", "event": {"type": "agent_settled"}}]

    @pytest.mark.asyncio
    async def test_read_events_raises_on_timeout(self, conn):
        with pytest.raises(PiError, match="Timeout waiting for pi response events"):
            async for _ in conn.read_events(event_timeout=0.01):
                pass

    @pytest.mark.asyncio
    async def test_read_events_raises_on_connection_lost_sentinel(self, conn):
        await conn._event_queue.put({"type": "connection_lost"})
        with pytest.raises(PiError, match="Connection to pi process lost"):
            async for _ in conn.read_events():
                pass

    @pytest.mark.asyncio
    async def test_send_prompt_uses_event_timeout_parameter(self, conn):
        response = {"type": "response", "id": "cmd-1", "success": True}
        conn._pending_responses["cmd-1"] = asyncio.get_running_loop().create_future()
        conn._pending_responses["cmd-1"].set_result(response)
        conn.send_command = AsyncMock(return_value=response)

        async def producer():
            await asyncio.sleep(0.05)
            await conn._event_queue.put({"type": "agent_settled"})

        task = asyncio.create_task(producer())
        result = []
        async for ev in conn.send_prompt("hello", event_timeout=None):
            result.append(ev)
        await task
        assert result == [{"type": "event", "event": {"type": "agent_settled"}}]

    @pytest.mark.asyncio
    async def test_send_prompt_returns_immediately_for_handled_prompt(self, conn):
        """`disposition: handled` starts no run: do not wait for a settle."""
        conn.send_command = AsyncMock(
            return_value={
                "type": "response",
                "id": "cmd-2",
                "success": True,
                "data": {"disposition": "handled"},
            }
        )

        async def consume():
            return [ev async for ev in conn.send_prompt("hello", event_timeout=30)]

        assert await asyncio.wait_for(consume(), timeout=1) == []
