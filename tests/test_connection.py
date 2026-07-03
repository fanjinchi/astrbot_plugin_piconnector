"""Unit tests for pi_connector/connection.py (mocked sync/async parts)."""

import asyncio
import unittest

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector.connection import PiConnection  # noqa: E402
from pi_connector.models import UIRequest  # noqa: E402
# isort: on


class TestPiConnectionId(unittest.TestCase):
    """Tests for the small synchronous ID generators."""

    def test_next_command_id(self):
        conn = PiConnection()
        id1 = conn._next_command_id()
        id2 = conn._next_command_id()
        self.assertNotEqual(id1, id2)
        self.assertTrue(id1.startswith("pi-cmd-1-"))
        self.assertTrue(id2.startswith("pi-cmd-2-"))

    def test_next_ui_local_id(self):
        conn = PiConnection()
        self.assertEqual(conn._next_ui_local_id(), 1)
        self.assertEqual(conn._next_ui_local_id(), 2)
        self.assertEqual(conn._next_ui_local_id(), 3)


class TestPiConnectionEvents(unittest.IsolatedAsyncioTestCase):
    """Tests for event handling and normalization without a real pi process."""

    async def asyncSetUp(self):
        self.conn = PiConnection()

    async def test_handle_event_response(self):
        future = asyncio.get_running_loop().create_future()
        self.conn._pending_responses["resp-1"] = future
        event = {"type": "response", "id": "resp-1", "data": "ok"}
        await self.conn._handle_event(event)
        self.assertTrue(future.done())
        self.assertEqual(future.result(), event)
        self.assertNotIn("resp-1", self.conn._pending_responses)

    async def test_handle_event_extension_ui_request(self):
        event = {
            "type": "extension_ui_request",
            "id": "req-1",
            "method": "confirm",
            "title": "Confirm?",
            "message": "Are you sure?",
        }
        await self.conn._handle_event(event)
        self.assertIn("req-1", self.conn.pending_ui_requests)
        req = self.conn.pending_ui_requests["req-1"]
        self.assertEqual(req.local_id, 1)
        self.assertEqual(req.method, "confirm")
        self.assertEqual(req.title, "Confirm?")

    async def test_track_ui_request_ignores_non_interactive(self):
        event = {
            "type": "extension_ui_request",
            "id": "req-2",
            "method": "setStatus",
        }
        self.conn._track_ui_request(event)
        self.assertNotIn("req-2", self.conn.pending_ui_requests)

    async def test_get_ui_request_by_local_id(self):
        event = {
            "type": "extension_ui_request",
            "id": "req-3",
            "method": "select",
            "options": ["a", "b"],
        }
        await self.conn._handle_event(event)
        req = self.conn.get_ui_request_by_local_id(1)
        self.assertIsNotNone(req)
        self.assertEqual(req.request_id, "req-3")
        self.assertEqual(self.conn.get_ui_request_by_local_id(999), None)

    async def test_normalize_event_text_delta(self):
        event = {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "hello"},
        }
        result = self.conn._normalize_event(event)
        self.assertEqual(result, {"type": "text", "text": "hello"})

    async def test_normalize_event_thinking_delta(self):
        event = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "thinking_delta",
                "delta": "thinking",
            },
        }
        result = self.conn._normalize_event(event)
        self.assertEqual(result, {"type": "thinking", "text": "thinking"})

    async def test_normalize_event_ui_request(self):
        self.conn.pending_ui_requests["req-4"] = UIRequest(
            local_id=7,
            request_id="req-4",
            method="input",
        )
        event = {
            "type": "extension_ui_request",
            "id": "req-4",
        }
        result = self.conn._normalize_event(event)
        self.assertEqual(result["type"], "ui_request")
        self.assertEqual(result["request"].local_id, 7)
        self.assertEqual(result["request"].method, "input")

    async def test_normalize_event_fallback(self):
        event = {"type": "unknown", "data": "x"}
        result = self.conn._normalize_event(event)
        self.assertEqual(result, {"type": "event", "event": event})

    async def test_handle_event_puts_non_response_on_queue(self):
        event = {"type": "agent_end"}
        await self.conn._handle_event(event)
        queued = self.conn._event_queue.get_nowait()
        self.assertEqual(queued, event)


if __name__ == "__main__":
    unittest.main()
