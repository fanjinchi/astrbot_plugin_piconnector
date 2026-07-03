"""Unit tests for pi_connector/manager.py."""

import json
import os
import tempfile
import time
import types
import unittest

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector.connection import PiError  # noqa: E402
from pi_connector.manager import PiConnectionManager  # noqa: E402
# isort: on


class TestSessionKey(unittest.TestCase):
    """Tests for PiConnectionManager._session_key."""

    def test_session_key_with_all_attrs(self):
        event = types.SimpleNamespace(
            get_platform_name=lambda: "qq",
            get_group_id=lambda: "123",
            get_sender_id=lambda: "456",
        )
        mgr = PiConnectionManager()
        self.assertEqual(mgr._session_key(event), "qq:123:456")

    def test_session_key_missing_attrs(self):
        event = object()
        mgr = PiConnectionManager()
        self.assertEqual(mgr._session_key(event), "unknown::")

    def test_session_key_partial(self):
        event = types.SimpleNamespace(get_platform_name=lambda: "telegram")
        mgr = PiConnectionManager()
        self.assertEqual(mgr._session_key(event), "telegram::")


class TestSessionDirForCwd(unittest.TestCase):
    """Tests for PiConnectionManager._session_dir_for_cwd."""

    def test_simple_path(self):
        mgr = PiConnectionManager()
        self.assertEqual(
            mgr._session_dir_for_cwd("/home/user/project"),
            "---home-user-project--",
        )

    def test_root_path(self):
        mgr = PiConnectionManager()
        self.assertEqual(mgr._session_dir_for_cwd("/"), "-----")


class TestExtractSessionId(unittest.TestCase):
    """Tests for PiConnectionManager._extract_session_id."""

    def test_with_uuid_prefix(self):
        mgr = PiConnectionManager()
        self.assertEqual(mgr._extract_session_id("session_abc-123.jsonl"), "abc-123")

    def test_without_prefix(self):
        mgr = PiConnectionManager()
        self.assertEqual(mgr._extract_session_id("abc-123.jsonl"), "abc-123")


class TestFindSessionFile(unittest.TestCase):
    """Tests for PiConnectionManager._find_session_file."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mgr = PiConnectionManager(session_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_session(self, relative_path, sid, mtime=None):
        full_path = os.path.join(self.tmpdir.name, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        header = {
            "type": "session",
            "id": sid,
            "cwd": "/tmp",
            "messageCount": 0,
        }
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
        if mtime is not None:
            os.utime(full_path, (mtime, mtime))
        return full_path

    def test_absolute_path(self):
        path = self._write_session("abc.jsonl", "abc-123")
        result = self.mgr._find_session_file(path)
        self.assertEqual(result, path)

    def test_exact_match(self):
        self._write_session("s_abc-123.jsonl", "abc-123")
        result = self.mgr._find_session_file("abc-123")
        self.assertTrue(result.endswith("s_abc-123.jsonl"))

    def test_prefix_match_unique(self):
        self._write_session("s_abc-123.jsonl", "abc-123")
        result = self.mgr._find_session_file("abc")
        self.assertTrue(result.endswith("s_abc-123.jsonl"))

    def test_prefix_match_multiple_raises(self):
        self._write_session("s_abc-123.jsonl", "abc-123")
        self._write_session("s_abc-456.jsonl", "abc-456")
        with self.assertRaises(PiError) as ctx:
            self.mgr._find_session_file("abc")
        self.assertIn("Multiple sessions match", str(ctx.exception))

    def test_no_match(self):
        self._write_session("s_abc-123.jsonl", "abc-123")
        result = self.mgr._find_session_file("xyz")
        self.assertIsNone(result)


class TestFindMostRecentSession(unittest.TestCase):
    """Tests for PiConnectionManager._find_most_recent_session."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mgr = PiConnectionManager(session_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_session(self, relative_path, sid, mtime=None):
        full_path = os.path.join(self.tmpdir.name, relative_path)
        header = {
            "type": "session",
            "id": sid,
            "cwd": "/tmp",
            "messageCount": 0,
        }
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
        if mtime is not None:
            os.utime(full_path, (mtime, mtime))
        return full_path

    def test_finds_most_recent(self):
        now = time.time()
        older = self._write_session("a.jsonl", "a", mtime=now - 10)
        newer = self._write_session("b.jsonl", "b", mtime=now)
        result = self.mgr._find_most_recent_session()
        self.assertEqual(result, newer)
        self.assertNotEqual(result, older)

    def test_no_sessions(self):
        result = self.mgr._find_most_recent_session()
        self.assertIsNone(result)


class TestReadSessionHeader(unittest.TestCase):
    """Tests for PiConnectionManager._read_session_header."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_valid_header(self):
        mgr = PiConnectionManager()
        path = os.path.join(self.tmpdir.name, "test.jsonl")
        header = {
            "type": "session",
            "id": "sid-123",
            "cwd": "/home/user",
            "name": "test",
            "messageCount": 5,
            "timestamp": "2026-01-01",
        }
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
        info = mgr._read_session_header(path)
        self.assertIsNotNone(info)
        self.assertEqual(info.session_id, "sid-123")
        self.assertEqual(info.cwd, "/home/user")
        self.assertEqual(info.session_name, "test")
        self.assertEqual(info.message_count, 5)
        self.assertEqual(info.timestamp, "2026-01-01")

    def test_invalid_type(self):
        mgr = PiConnectionManager()
        path = os.path.join(self.tmpdir.name, "test.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "message"}) + "\n")
        info = mgr._read_session_header(path)
        self.assertIsNone(info)

    def test_empty_file(self):
        mgr = PiConnectionManager()
        path = os.path.join(self.tmpdir.name, "test.jsonl")
        with open(path, "w", encoding="utf-8"):
            pass
        info = mgr._read_session_header(path)
        self.assertIsNone(info)


class TestFormatSessionInfo(unittest.TestCase):
    """Tests for PiConnectionManager._format_session_info."""

    def test_format(self):
        mgr = PiConnectionManager()
        state = {
            "sessionId": "sid",
            "sessionFile": "/tmp/s.jsonl",
            "sessionName": "my-session",
            "messageCount": 3,
            "thinkingLevel": "high",
            "isStreaming": True,
            "timestamp": "2026-01-01",
        }
        conn = types.SimpleNamespace(cwd="/home/user")
        info = mgr._format_session_info(state, conn)
        self.assertEqual(info.session_id, "sid")
        self.assertEqual(info.session_file, "/tmp/s.jsonl")
        self.assertEqual(info.cwd, "/home/user")
        self.assertEqual(info.session_name, "my-session")
        self.assertEqual(info.message_count, 3)
        self.assertEqual(info.thinking_level, "high")
        self.assertTrue(info.is_streaming)
        self.assertEqual(info.timestamp, "2026-01-01")

    def test_format_defaults(self):
        mgr = PiConnectionManager()
        conn = types.SimpleNamespace(cwd=None)
        info = mgr._format_session_info({}, conn)
        self.assertEqual(info.cwd, "")
        self.assertEqual(info.session_id, "")
        self.assertIsNone(info.session_name)


class TestListSessions(unittest.TestCase):
    """Tests for PiConnectionManager.list_sessions."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mgr = PiConnectionManager(session_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_session(self, relative_path, sid, cwd="/tmp"):
        full_path = os.path.join(self.tmpdir.name, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        header = {
            "type": "session",
            "id": sid,
            "cwd": cwd,
            "messageCount": 0,
        }
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
        return full_path

    def test_list_all_sessions(self):
        self._write_session("a.jsonl", "a")
        self._write_session("nested/b.jsonl", "b")
        sessions = self.mgr.list_sessions()
        self.assertEqual(len(sessions), 2)
        ids = {s.session_id for s in sessions}
        self.assertEqual(ids, {"a", "b"})

    def test_list_by_directory(self):
        cwd = "/home/user/project"
        target_dir = self.mgr._session_dir_for_cwd(cwd)
        self._write_session(f"{target_dir}/a.jsonl", "a", cwd=cwd)
        self._write_session("other/b.jsonl", "b", cwd="/other")
        sessions = self.mgr.list_sessions(directory=cwd)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, "a")

    def test_list_by_directory_relative_raises(self):
        with self.assertRaises(PiError) as ctx:
            self.mgr.list_sessions(directory="relative/path")
        self.assertIn("Directory must be absolute", str(ctx.exception))

    def test_list_no_session_root(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            mgr = PiConnectionManager(
                session_dir=os.path.join(empty_dir, "nonexistent")
            )
            self.assertEqual(mgr.list_sessions(), [])


if __name__ == "__main__":
    unittest.main()
