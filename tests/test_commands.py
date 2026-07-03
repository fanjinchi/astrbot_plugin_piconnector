"""Unit tests for pi_connector/commands.py."""

import unittest

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector.commands import (  # noqa: E402
    _extract_user_text,
    _filter_user_entries,
    extract_active_branch,
    format_commands_list,
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
from pi_connector.models import SessionInfo, UIRequest  # noqa: E402
# isort: on


class TestStripCommandPrefix(unittest.TestCase):
    def test_removes_slash_prefix(self):
        self.assertEqual(strip_command_prefix("/pi open", "pi"), "open")

    def test_removes_plain_prefix(self):
        self.assertEqual(strip_command_prefix("pi open", "pi"), "open")

    def test_no_match_returns_unchanged(self):
        self.assertEqual(strip_command_prefix("hello", "pi"), "hello")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(strip_command_prefix("  /pi open  ", "pi"), "open")


class TestParseSubcommand(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(parse_subcommand(""), ("", ""))

    def test_single_word(self):
        self.assertEqual(parse_subcommand("open"), ("open", ""))

    def test_lowercase_subcommand(self):
        self.assertEqual(parse_subcommand("OPEN path"), ("open", "path"))

    def test_with_rest(self):
        self.assertEqual(
            parse_subcommand("resume abc123 extra"), ("resume", "abc123 extra")
        )


class TestFormatSessionInfo(unittest.TestCase):
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
        self.assertEqual(output, expected)

    def test_unnamed_placeholder(self):
        info = SessionInfo(
            session_id="sid-2",
            session_file="/tmp/session.jsonl",
            cwd="/home/user/project",
            message_count=0,
        )
        self.assertIn("Name: (unnamed)", format_session_info(info))

    def test_thinking_level_appended(self):
        info = SessionInfo(
            session_id="sid-3",
            session_file="/tmp/session.jsonl",
            cwd="/home/user/project",
            thinking_level="high",
        )
        self.assertIn("Thinking level: high", format_session_info(info))


class TestFormatSessionList(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(format_session_list([]), "No sessions found.")

    def test_multiple_sessions(self):
        sessions = [
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
                cwd="/home/b",
                message_count=2,
            ),
        ]
        output = format_session_list(sessions)
        self.assertIn("Available sessions:", output)
        self.assertIn("1. alpha", output)
        self.assertIn("2. (unnamed)", output)
        self.assertIn("ID: sid-2", output)
        self.assertIn("Created: unknown", output)


class TestFormatUiRequest(unittest.TestCase):
    def test_confirm(self):
        req = UIRequest(local_id=1, request_id="r1", method="confirm", title="Ok?")
        output = format_ui_request(req)
        self.assertIn("[Request #1]", output)
        self.assertIn("Title: Ok?", output)
        self.assertIn("/pi confirm 1 yes", output)
        self.assertIn("/pi confirm 1 no", output)

    def test_select(self):
        req = UIRequest(
            local_id=2,
            request_id="r2",
            method="select",
            message="Choose",
            options=["a", "b"],
        )
        output = format_ui_request(req)
        self.assertIn("Options:", output)
        self.assertIn("1. a", output)
        self.assertIn("2. b", output)
        self.assertIn("/pi select 2 <option>", output)

    def test_input(self):
        req = UIRequest(local_id=3, request_id="r3", method="input")
        self.assertIn("/pi input 3 <value>", format_ui_request(req))

    def test_editor_with_prefill(self):
        req = UIRequest(
            local_id=4,
            request_id="r4",
            method="editor",
            prefill="hello",
        )
        output = format_ui_request(req)
        self.assertIn("Prefill: hello", output)
        self.assertIn("/pi edit 4 <text>", output)

    def test_unknown_method_defaults_to_input(self):
        req = UIRequest(local_id=5, request_id="r5", method="unknown")
        self.assertIn("/pi input 5 <value>", format_ui_request(req))


class TestFormatCommandsList(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(format_commands_list([]), "No slash commands available.")

    def test_with_descriptions(self):
        commands = [
            {"name": "explore", "description": "Explore", "source": "pi"},
            {"name": "ask", "description": "Ask", "source": "pi"},
        ]
        output = format_commands_list(commands)
        self.assertTrue(output.startswith("Available pi commands:\n\n"))
        self.assertIn("/explore (pi) ➡️ Explore", output)
        self.assertIn("/ask (pi) ➡️ Ask", output)
        # Two command lines separated by a blank line -> a single blank line between them.
        self.assertIn("Explore\n\n/ask", output)

    def test_without_description(self):
        commands = [{"name": "custom", "description": "", "source": "builtin"}]
        self.assertIn("/custom (builtin)", format_commands_list(commands))


class TestResolveSelectOption(unittest.TestCase):
    def setUp(self):
        self.request = UIRequest(
            local_id=1,
            request_id="r1",
            method="select",
            options=["apple", "banana"],
        )

    def test_no_options_returns_value(self):
        req = UIRequest(local_id=1, request_id="r1", method="select")
        self.assertEqual(resolve_select_option(req, "anything"), "anything")

    def test_exact_match(self):
        self.assertEqual(resolve_select_option(self.request, "banana"), "banana")

    def test_index_match(self):
        self.assertEqual(resolve_select_option(self.request, "2"), "banana")

    def test_invalid_returns_none(self):
        self.assertIsNone(resolve_select_option(self.request, "3"))
        self.assertIsNone(resolve_select_option(self.request, "grape"))


class TestParseUiReplyArgs(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertEqual(parse_ui_reply_args(""), (None, ""))

    def test_only_id(self):
        self.assertEqual(parse_ui_reply_args("7"), (7, ""))

    def test_id_and_value(self):
        self.assertEqual(parse_ui_reply_args("7 yes"), (7, "yes"))

    def test_id_and_value_with_spaces(self):
        self.assertEqual(parse_ui_reply_args("7 yes please"), (7, "yes please"))

    def test_invalid_id_returns_raw(self):
        self.assertEqual(parse_ui_reply_args("abc yes"), (None, "abc yes"))


class TestExtractActiveBranch(unittest.TestCase):
    def test_empty_tree(self):
        self.assertEqual(extract_active_branch([], "leaf"), [])

    def test_no_leaf_id(self):
        tree = [{"entry": {"id": "root"}, "children": []}]
        self.assertEqual(extract_active_branch(tree, None), [])

    def test_single_root(self):
        tree = [{"entry": {"id": "root"}, "children": []}]
        self.assertEqual(extract_active_branch(tree, "root"), [{"id": "root"}])

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
        self.assertEqual([e["id"] for e in branch], ["root", "child", "leaf"])

    def test_missing_node_breaks_loop(self):
        tree = [{"entry": {"id": "root"}, "children": []}]
        self.assertEqual(extract_active_branch(tree, "missing"), [])

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
        self.assertEqual([e["id"] for e in branch], ["a", "b", "c"])


class TestFilterUserEntries(unittest.TestCase):
    def test_filters_non_message(self):
        entries = [{"type": "action"}, {"type": "message", "message": {"role": "user"}}]
        self.assertEqual(_filter_user_entries(entries), [entries[1]])

    def test_excludes_assistant(self):
        entries = [{"type": "message", "message": {"role": "assistant"}}]
        self.assertEqual(_filter_user_entries(entries), [])


class TestExtractUserText(unittest.TestCase):
    def test_string_content(self):
        entry = {"message": {"content": "Hello\nworld"}}
        self.assertEqual(_extract_user_text(entry), "Hello world")

    def test_text_blocks(self):
        entry = {
            "message": {
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ]
            }
        }
        self.assertEqual(_extract_user_text(entry), "Hello world")

    def test_empty_returns_placeholder(self):
        self.assertEqual(_extract_user_text({"message": {}}), "(empty)")

    def test_truncation(self):
        entry = {"message": {"content": "a" * 100}}
        self.assertTrue(_extract_user_text(entry, max_length=20).endswith("…"))


class TestFormatTreeEntries(unittest.TestCase):
    def test_no_user_messages(self):
        self.assertEqual(
            format_tree_entries([]),
            "No user messages on the active branch.",
        )

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
        self.assertIn("User messages on the active branch:", output)
        self.assertIn("1. 2026-07-01 hello", output)
        self.assertIn("/pi tree <number>", output)


class TestResolveTreeEntryId(unittest.TestCase):
    def test_valid_number(self):
        entries = [
            {"type": "message", "message": {"role": "user"}, "id": "e1"},
            {"type": "message", "message": {"role": "user"}, "id": "e2"},
        ]
        self.assertEqual(resolve_tree_entry_id(entries, 2), "e2")

    def test_invalid_number(self):
        entries = [
            {"type": "message", "message": {"role": "user"}, "id": "e1"},
        ]
        self.assertIsNone(resolve_tree_entry_id(entries, 0))
        self.assertIsNone(resolve_tree_entry_id(entries, 5))


if __name__ == "__main__":
    unittest.main()
