"""Tests for the background task notification helpers in main.py."""

import asyncio

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
# isort: on

from unittest.mock import AsyncMock

import pytest
from conftest import FakeContext
from pi_connector.tasks import PiTask

import main


@pytest.fixture
def plugin():
    """Return a PiConnectorPlugin instance with a real task manager."""
    instance = main.PiConnectorPlugin(context=FakeContext())
    instance.pi_connection_manager = AsyncMock()
    return instance


def _make_task(plugin, task_id=1, status="completed", text="result"):
    """Create a PiTask with the plugin's notification callback bound."""
    return PiTask(
        task_id=task_id,
        status=status,
        prompt="hello",
        started_at=0.0,
        umo="test:umo",
        ui_resolved=asyncio.Event(),
        text_parts=[text],
        tool_call_count=2,
    )


class TestNotifyTaskFinished:
    @pytest.mark.asyncio
    async def test_sends_header_and_result_chunks(self, plugin):
        task = _make_task(plugin, text="a" * 2000)
        await plugin._notify_task_finished(task, "completed", task.result_text, None)

        send_mock = plugin.context.send_message
        assert send_mock.call_count == 3  # header + 2 chunks
        messages = [call.args[1]._messages[0] for call in send_mock.call_args_list]
        assert "✅ pi 任务 #1 完成" in messages[0]
        assert messages[1] == "a" * 1500
        assert messages[2] == "a" * 500

    @pytest.mark.asyncio
    async def test_injects_history_pair(self, plugin):
        task = _make_task(plugin, text="final result")
        await plugin._notify_task_finished(task, "completed", task.result_text, None)

        pairs = plugin.context.conversation_manager._pairs
        assert len(pairs) == 1
        user, assistant = pairs[0]
        assert user["role"] == "user"
        assert "pi background task #1 finished: completed" in user["content"]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == "final result"

    @pytest.mark.asyncio
    async def test_truncates_oversized_assistant_text(self, plugin):
        task = _make_task(plugin, text="x" * 5000)
        await plugin._notify_task_finished(task, "completed", task.result_text, None)

        pairs = plugin.context.conversation_manager._pairs
        assert len(pairs) == 1
        content = pairs[0][1]["content"]
        assert len(content) == 4000
        assert content.endswith("…")

    @pytest.mark.asyncio
    async def test_skips_history_when_no_conversation(self, plugin):
        plugin.context.conversation_manager._cid = None
        task = _make_task(plugin, text="result")
        await plugin._notify_task_finished(task, "completed", task.result_text, None)
        assert plugin.context.conversation_manager._pairs == []
        assert plugin.context.send_message.call_count >= 1

    @pytest.mark.asyncio
    async def test_failed_status_prefix(self, plugin):
        task = _make_task(plugin, status="failed", text="")
        await plugin._notify_task_finished(
            task, "failed", task.result_text, "something broke"
        )

        send_mock = plugin.context.send_message
        messages = [call.args[1]._messages[0] for call in send_mock.call_args_list]
        assert "❌ pi 任务 #1 失败" in messages[0]
        assert "错误：something broke" in messages[1]

    @pytest.mark.asyncio
    async def test_aborted_status_prefix(self, plugin):
        task = _make_task(plugin, status="aborted", text="")
        await plugin._notify_task_finished(task, "aborted", task.result_text, None)

        send_mock = plugin.context.send_message
        messages = [call.args[1]._messages[0] for call in send_mock.call_args_list]
        assert "⛔ pi 任务 #1 已中止" in messages[0]
