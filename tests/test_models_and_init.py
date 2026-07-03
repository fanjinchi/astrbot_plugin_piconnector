"""Unit tests for pi_connector models and package exports."""

import unittest

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector import (  # noqa: E402
    PiConnection,
    PiConnectionManager,
    SessionInfo,
    UIRequest,
    __all__,
)
from pi_connector.models import SessionInfo as ModelsSessionInfo  # noqa: E402
from pi_connector.models import UIRequest as ModelsUIRequest  # noqa: E402
# isort: on


class TestSessionInfo(unittest.TestCase):
    """Tests for the SessionInfo dataclass."""

    def test_required_fields(self):
        info = SessionInfo(
            session_id="abc-123",
            session_file="/tmp/session.jsonl",
            cwd="/workspace",
        )
        self.assertEqual(info.session_id, "abc-123")
        self.assertEqual(info.session_file, "/tmp/session.jsonl")
        self.assertEqual(info.cwd, "/workspace")

    def test_defaults(self):
        info = SessionInfo(
            session_id="abc-123",
            session_file="/tmp/session.jsonl",
            cwd="/workspace",
        )
        self.assertIsNone(info.session_name)
        self.assertEqual(info.message_count, 0)
        self.assertIsNone(info.thinking_level)
        self.assertFalse(info.is_streaming)
        self.assertIsNone(info.timestamp)

    def test_custom_values(self):
        info = SessionInfo(
            session_id="abc-123",
            session_file="/tmp/session.jsonl",
            cwd="/workspace",
            session_name="my-session",
            message_count=42,
            thinking_level="deep",
            is_streaming=True,
            timestamp="2026-07-03T12:00:00Z",
        )
        self.assertEqual(info.session_name, "my-session")
        self.assertEqual(info.message_count, 42)
        self.assertEqual(info.thinking_level, "deep")
        self.assertTrue(info.is_streaming)
        self.assertEqual(info.timestamp, "2026-07-03T12:00:00Z")


class TestUIRequest(unittest.TestCase):
    """Tests for the UIRequest dataclass."""

    def test_required_fields(self):
        request = UIRequest(
            local_id=1,
            request_id="req-123",
            method="confirm",
        )
        self.assertEqual(request.local_id, 1)
        self.assertEqual(request.request_id, "req-123")
        self.assertEqual(request.method, "confirm")

    def test_defaults(self):
        request = UIRequest(
            local_id=1,
            request_id="req-123",
            method="confirm",
        )
        self.assertEqual(request.title, "")
        self.assertEqual(request.message, "")
        self.assertEqual(request.options, [])
        self.assertEqual(request.prefill, "")
        self.assertIsNone(request.timeout_ms)
        self.assertEqual(request.created_at, 0.0)

    def test_to_dict(self):
        request = UIRequest(
            local_id=3,
            request_id="req-456",
            method="select",
            title="Choose one",
            message="Pick an option",
            options=["a", "b"],
            prefill="a",
            timeout_ms=1000,
            created_at=123.45,
        )
        expected = {
            "local_id": 3,
            "request_id": "req-456",
            "method": "select",
            "title": "Choose one",
            "message": "Pick an option",
            "options": ["a", "b"],
            "prefill": "a",
            "timeout_ms": 1000,
            "created_at": 123.45,
        }
        self.assertEqual(request.to_dict(), expected)

    def test_to_dict_with_defaults(self):
        request = UIRequest(
            local_id=1,
            request_id="req-123",
            method="confirm",
        )
        expected = {
            "local_id": 1,
            "request_id": "req-123",
            "method": "confirm",
            "title": "",
            "message": "",
            "options": [],
            "prefill": "",
            "timeout_ms": None,
            "created_at": 0.0,
        }
        self.assertEqual(request.to_dict(), expected)

    def test_options_isolation(self):
        request1 = UIRequest(local_id=1, request_id="r1", method="select")
        request2 = UIRequest(local_id=2, request_id="r2", method="select")
        request1.options.append("only")
        self.assertEqual(request1.options, ["only"])
        self.assertEqual(request2.options, [])


class TestPackageExports(unittest.TestCase):
    """Tests for pi_connector.__init__ exports."""

    def test_all_exports(self):
        self.assertEqual(
            set(__all__),
            {
                "PiConnection",
                "PiConnectionManager",
                "PiError",
                "SessionInfo",
                "UIRequest",
            },
        )

    def test_session_info_is_same_class(self):
        self.assertIs(SessionInfo, ModelsSessionInfo)

    def test_ui_request_is_same_class(self):
        self.assertIs(UIRequest, ModelsUIRequest)

    def test_piconnection_is_importable(self):
        self.assertTrue(callable(PiConnection))

    def test_piconnectionmanager_is_importable(self):
        self.assertTrue(callable(PiConnectionManager))


if __name__ == "__main__":
    unittest.main()
