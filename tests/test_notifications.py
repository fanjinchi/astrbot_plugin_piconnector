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
    async def test_wakes_main_agent(self, plugin, monkeypatch):
        """任务完成后应当唤醒主 agent 去读结果，而不是只把结果写进历史。"""
        calls = []

        async def fake_wake(task, status, text, error):
            calls.append((task.task_id, status, text))

        monkeypatch.setattr(plugin, "_wake_main_agent_for_task_result", fake_wake)
        task = _make_task(plugin, text="final result")
        await plugin._notify_task_finished(task, "completed", task.result_text, None)
        await asyncio.sleep(0)  # 让 create_task 排的协程跑起来

        assert calls == [(1, "completed", "final result")]

    @pytest.mark.asyncio
    async def test_wakes_even_without_conversation(self, plugin, monkeypatch):
        """移除历史注入后，没有会话也不该挡住唤醒。"""
        calls = []

        async def fake_wake(task, status, text, error):
            calls.append(task.task_id)

        monkeypatch.setattr(plugin, "_wake_main_agent_for_task_result", fake_wake)
        plugin.context.conversation_manager._cid = None
        task = _make_task(plugin, text="result")
        await plugin._notify_task_finished(task, "completed", task.result_text, None)
        await asyncio.sleep(0)

        assert calls == [1]
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
