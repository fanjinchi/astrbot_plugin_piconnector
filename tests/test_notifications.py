"""Tests for the background task notification helpers in main.py."""

import asyncio

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
# isort: on

from unittest.mock import AsyncMock

import pytest
from conftest import FakeContext
from pi_connector.models import UIRequest
from pi_connector.tasks import PiTask

import main


@pytest.fixture
def plugin():
    """Return a PiConnectorPlugin instance with a real task manager."""
    instance = main.PiConnectorPlugin(context=FakeContext())
    instance.pi_connection_manager = AsyncMock()
    return instance


def _make_task(plugin, task_id=1, status="completed", text="result", **kwargs):
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
        **kwargs,
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

    @pytest.mark.asyncio
    async def test_chat_and_wake_share_the_same_final_answer(self, plugin, monkeypatch):
        """决策 C：两路都带全文，且内容完全一致。

        聊天推送的文本与唤醒载荷必须是同一个字符串（`task.result_text`），
        否则用户看到的和 agent 看到的不一样，agent 就会忍不住再总结一遍。
        """
        calls = []

        async def fake_wake(task, status, text, error):
            calls.append(text)

        monkeypatch.setattr(plugin, "_wake_main_agent_for_task_result", fake_wake)
        task = _make_task(plugin, text="子任务过程日志", final_answer="主线最终回答")
        await plugin._notify_task_finished(task, "completed", task.result_text, None)
        await asyncio.sleep(0)

        sent = [
            call.args[1]._messages[0]
            for call in plugin.context.send_message.call_args_list
        ]
        assert sent[1] == "主线最终回答"
        assert "子任务过程日志" not in "".join(sent)
        assert calls == ["主线最终回答"]

    @pytest.mark.asyncio
    async def test_wake_note_carries_text_and_no_repeat_instruction(self, plugin):
        note = main.format_wake_note(7, "completed", "主线最终回答")
        assert note.startswith("[pi background task #7 finished: completed]")
        assert "主线最终回答" in note
        assert "不要复述全文" in note

    @pytest.mark.asyncio
    async def test_handled_prompt_pushes_no_wake(self, plugin, monkeypatch):
        """`disposition: handled` 没有模型回合：推送说明，不空唤醒 agent。"""
        calls = []

        async def fake_wake(task, status, text, error):
            calls.append(task.task_id)

        monkeypatch.setattr(plugin, "_wake_main_agent_for_task_result", fake_wake)
        task = _make_task(plugin, text="", no_model_run=True)
        await plugin._notify_task_finished(task, "completed", task.result_text, None)
        await asyncio.sleep(0)

        sent = [
            call.args[1]._messages[0]
            for call in plugin.context.send_message.call_args_list
        ]
        assert "没有启动模型回合" in sent[0]
        assert "✅ pi 任务 #1 完成" in sent[0]
        assert calls == []

    @pytest.mark.asyncio
    async def test_ui_timeout_notice_is_pushed(self, plugin):
        """无人在场时必须说一声：该确认请求已取消。"""
        task = _make_task(plugin, text="")
        request = UIRequest(local_id=1, request_id="req-1", method="confirm")

        await plugin._handle_background_ui_timeout(task, request)

        sent = [
            call.args[1]._messages[0]
            for call in plugin.context.send_message.call_args_list
        ]
        assert "该确认请求已取消" in sent[0]
        assert "confirm" in sent[0]


class TestInterruptionNotice:
    """The notification must say *where* a run broke, not just that it did.

    pi emits `agent_settled` from a `finally` block, so an interrupted run still
    settles like a finished one; the header alone cannot distinguish them.
    """

    @staticmethod
    def _messages(plugin):
        return [
            call.args[1]._messages[0]
            for call in plugin.context.send_message.call_args_list
        ]

    @pytest.mark.asyncio
    async def test_subtask_interruption_names_the_phase(self, plugin):
        task = _make_task(
            plugin,
            status="failed",
            text="",
            stop_reason="error",
            interrupt_phase="task",
            retry_final_error="fetch failed",
            partial_answer="正在跑测试",
            error="fetch failed",
        )
        await plugin._notify_task_finished(task, "failed", task.result_text, task.error)

        messages = self._messages(plugin)
        assert "❌ pi 任务 #1 失败" in messages[0]
        assert "❌ 子任务执行中断：fetch failed" in messages[1]
        # The phase line already carries the reason: no duplicate 错误 line.
        assert not any(message.startswith("错误：") for message in messages)
        assert "（以下为中断前已生成的部分）" in messages[2]
        assert messages[-1] == "正在跑测试"

    @pytest.mark.asyncio
    async def test_summary_interruption_says_subtask_finished(self, plugin):
        task = _make_task(
            plugin,
            status="failed",
            text="",
            stop_reason="error",
            interrupt_phase="summary",
            retry_final_error="fetch failed",
            final_answer="主线最终回答",
            partial_answer="补充……",
            error="fetch failed",
        )
        await plugin._notify_task_finished(task, "failed", task.result_text, task.error)

        messages = self._messages(plugin)
        assert "⚠️ 子任务已完成，主线总结中断：fetch failed" in messages[1]
        assert not any(message.startswith("错误：") for message in messages)

    @pytest.mark.asyncio
    async def test_mainline_interruption_before_any_task(self, plugin):
        task = _make_task(
            plugin,
            status="failed",
            text="",
            stop_reason="error",
            interrupt_phase="mainline",
            error_message="connection reset",
            error="connection reset",
        )
        await plugin._notify_task_finished(task, "failed", task.result_text, task.error)

        messages = self._messages(plugin)
        assert "❌ 主线回答中断：connection reset" in messages[1]

    @pytest.mark.asyncio
    async def test_truncated_answer_stays_completed_but_is_flagged(self, plugin):
        task = _make_task(
            plugin,
            status="completed",
            text="被截断的回答",
            stop_reason="length",
            truncated=True,
        )
        await plugin._notify_task_finished(
            task, "completed", task.result_text, task.error
        )

        messages = self._messages(plugin)
        assert "✅ pi 任务 #1 完成" in messages[0]
        assert "⚠️ 回答被长度上限截断" in messages[1]
        assert messages[-1] == "被截断的回答"

    @pytest.mark.asyncio
    async def test_clean_run_has_no_interruption_lines(self, plugin):
        task = _make_task(plugin, status="completed", text="最终回答", stop_reason="stop")
        await plugin._notify_task_finished(
            task, "completed", task.result_text, task.error
        )

        messages = self._messages(plugin)
        assert "✅ pi 任务 #1 完成" in messages[0]
        assert messages[-1] == "最终回答"
        assert not any("中断" in m or "截断" in m for m in messages)
