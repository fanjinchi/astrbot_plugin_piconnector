"""Tests for pi_connector/tasks.py."""

import asyncio
from unittest.mock import AsyncMock

import pytest

# Shared test setup: must run before any pi_connector import.
# isort: off
import _helpers  # noqa: F401
from pi_connector import PiError  # noqa: E402
from pi_connector import tasks as tasks_module  # noqa: E402
from pi_connector.connection import PiResponseTimeout  # noqa: E402
from pi_connector.models import UIRequest  # noqa: E402
from pi_connector.tasks import PiTaskManager  # noqa: E402
# isort: on


def _session_key(event) -> str:
    """Return a deterministic key for fake events."""
    return "test-key"


def _raw(event_type: str, **fields):
    """Wrap one pi session event in the `event` frame the connection emits."""
    return {"type": "event", "event": {"type": event_type, **fields}}


def _assistant_end(
    text: str,
    stop_reason: str | None = None,
    error_message: str | None = None,
):
    """Build an assistant `message_end` frame (pi's authoritative final message)."""
    message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    if stop_reason is not None:
        message["stopReason"] = stop_reason
    if error_message is not None:
        message["errorMessage"] = error_message
    return _raw("message_end", message=message)


def _entry_appended(entry_type: str, custom_type: str):
    """Build an `entry_appended` frame for one task-bookkeeping entry."""
    return _raw(
        "entry_appended",
        entry={"type": entry_type, "customType": custom_type, "display": True},
    )


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

    async def send_command(self, command, timeout=30, drain_on_timeout=True):
        self.sent_commands.append(command)
        return self.command_response

    async def read_events(self, event_timeout=None):
        # Mirrors the real connection: only agent_settled ends the run (agent_end
        # only closes one low-level run), a handled prompt ends it without a run,
        # and exhausting the queue ends it too.
        while True:
            if self._event_queue.empty():
                return
            event = await self._event_queue.get()
            yield event
            etype = event.get("type")
            if etype == "event":
                etype = event.get("event", {}).get("type")
            if etype in ("agent_settled", "prompt_handled"):
                break

    async def cancel_ui_request(self, request_id):
        self.cancelled_ui_requests = getattr(self, "cancelled_ui_requests", [])
        self.cancelled_ui_requests.append(request_id)
        self.pending_ui_requests.pop(request_id, None)

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
        conn = FakeConnection(events=[{"type": "agent_settled"}])
        task = await manager.launch(conn, "test-key", "hello", "umo:1")
        assert task.task_id == 1
        assert task.status == "running"
        assert task.prompt == "hello"
        assert task.umo == "umo:1"
        assert conn.sent_commands == [{"type": "prompt", "message": "hello"}]
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)

    @pytest.mark.asyncio
    async def test_launch_handled_prompt_finishes_without_waiting(self, manager):
        """`disposition: "handled"` starts no run, so no settle ever arrives.

        pi docs/rpc.md:67: "If it is `handled`, no run started for this prompt,
        so don't wait for `agent_settled`". Sitting in read_events for the whole
        idle timeout is what made the "wait only for agent_settled" commit
        (29dda10) look broken and get rolled back — every extension command
        "got stuck" for 1200s and then failed.
        """
        conn = FakeConnection(
            events=[],
            command_response={"success": True, "data": {"disposition": "handled"}},
        )
        task = await manager.launch(conn, "test-key", "/compact", "umo:1")
        assert task.status == "completed"
        assert task.no_model_run is True
        assert task.result_text == ""
        assert manager.get_active("test-key") is None
        # No runner was started: there is nothing left to consume.
        assert "test-key" not in manager._runners
        manager._on_task_finished.assert_awaited_once()
        assert manager._on_task_finished.await_args.args[1] == "completed"

    @pytest.mark.asyncio
    async def test_handled_sentinel_after_blocked_ack_finishes_task(self, manager):
        """A dialog can block the acknowledgment; the late response still ends the task.

        `launch` stops waiting after PROMPT_ACK_TIMEOUT and hands the stream to
        the runner. When pi finally answers the prompt with `disposition:
        handled`, the connection enqueues a `prompt_handled` sentinel — the
        runner must treat it as the terminal event instead of idling out.
        """
        conn = FakeConnection(
            events=[{"type": "event", "event": {"type": "prompt_handled"}}]
        )

        async def blocked_send_command(command, timeout=30, drain_on_timeout=True):
            conn.sent_commands.append(command)
            raise PiResponseTimeout("Timeout waiting for response to command prompt")

        conn.send_command = blocked_send_command
        task = await manager.launch(conn, "test-key", "/review", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "completed"
        assert task.no_model_run is True
        assert manager.get_active("test-key") is None

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
                {"type": "event", "event": {"type": "agent_settled"}},
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
    async def test_completion_accepts_normalized_agent_settled(self, manager):
        """agent_settled as production actually delivers it.

        The real PiConnection normalizes raw events, so agent_settled reaches
        the task runner wrapped as {"type": "event", "event": {...}}. Ignoring
        that shape once made every successful run report
        "pi event stream ended without agent_settled".
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
    async def test_agent_end_alone_does_not_complete_task(self, manager):
        """agent_end closes one low-level run; pi may still continue.

        Only agent_settled means "pi will not continue automatically"
        (pi docs/rpc.md). Treating agent_end as terminal cut long runs short.
        """
        conn = FakeConnection(
            events=[{"type": "event", "event": {"type": "agent_end"}}]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "failed"
        assert "without agent_settled" in task.error

    @pytest.mark.asyncio
    async def test_aborted_settle_reports_aborted(self, manager):
        """`agent_settled{aborted: true}` is an abort, not a success.

        Reporting completed here pushed "✅ 完成" plus a half-written answer to
        the chat after the user hit /pi abort.
        """
        conn = FakeConnection(
            events=[
                {"type": "text", "text": "partial answer"},
                {
                    "type": "event",
                    "event": {"type": "agent_settled", "aborted": True},
                },
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.status == "aborted"
        assert task.aborted is True
        assert task.result_text == "partial answer"
        assert manager._on_task_finished.await_args.args[1] == "aborted"

    @pytest.mark.asyncio
    async def test_final_answer_is_mainline_text_after_subtask_result(self, manager):
        """Real pi 1.3.0 event flow: no `role: "custom"` boundary is emitted.

        A push-task round looks like: user prompt → sub-task assistant text →
        `entry_appended{customType: task-result}` → mainline assistant text →
        `agent_settled`, with `boundaries seen: ['user']`. The sub-task answer is
        therefore not dropped by a `custom` boundary (that role only exists on
        pi's `sendCustomMessage` path); it is dropped because RPC is one ordered
        stream and the last text-bearing `message_end` is the mainline answer.
        """
        conn = FakeConnection(
            events=[
                {
                    "type": "event",
                    "event": {"type": "message_start", "message": {"role": "user"}},
                },
                {"type": "text", "text": "sub-task narration "},
                {"type": "text", "text": "子任务过程：跑了 3 个测试"},
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "子任务过程：跑了 3 个测试"}
                            ],
                        },
                    },
                },
                {"type": "event", "event": {"type": "agent_end"}},
                # The sub-task result is an appended entry, not a message.
                {
                    "type": "event",
                    "event": {
                        "type": "entry_appended",
                        "entry": {
                            "type": "custom_message",
                            "customType": "task-result",
                            "content": "[task-result: 子任务] 子任务已完成",
                            "display": True,
                        },
                    },
                },
                {"type": "text", "text": "mainline narration "},
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "主线最终回答"}],
                        },
                    },
                },
                {"type": "event", "event": {"type": "agent_end"}},
                # A tool-call-only turn must not erase the answer.
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {"role": "assistant", "content": []},
                    },
                },
                {"type": "event", "event": {"type": "agent_settled"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.final_answer == "主线最终回答"
        assert task.result_text == "主线最终回答"
        assert "子任务过程" in task.work_log
        assert "mainline narration" in task.work_log
        assert task.result_text == manager._on_task_finished.await_args.args[2]

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


class TestFinalAnswerBoundaries:
    """`message_start` boundaries decide which text is the answer."""

    @pytest.mark.asyncio
    async def test_user_boundary_clears_stale_final_answer(self, manager):
        """A new `user` boundary closes the answer scope of the previous turn.

        Everything before the boundary belongs to an older turn, so a run that
        ends with a tool-call-only turn must not report that older text as its
        answer. Pins the reset in `PiTaskManager._run`.
        """
        conn = FakeConnection(
            events=[
                {
                    "type": "event",
                    "event": {"type": "message_start", "message": {"role": "user"}},
                },
                {"type": "text", "text": "子任务过程"},
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "子任务过程"}],
                        },
                    },
                },
                {"type": "event", "event": {"type": "agent_end"}},
                {
                    "type": "event",
                    "event": {
                        "type": "entry_appended",
                        "entry": {
                            "type": "custom_message",
                            "customType": "task-result",
                            "content": "[task-result: 子任务] 子任务已完成",
                            "display": True,
                        },
                    },
                },
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "主线最终回答"}],
                        },
                    },
                },
                {"type": "event", "event": {"type": "agent_end"}},
                # A user message arrives mid-run: the previous scope is over.
                {
                    "type": "event",
                    "event": {"type": "message_start", "message": {"role": "user"}},
                },
                # ... and that newer scope produces no assistant text at all.
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {"role": "assistant", "content": []},
                    },
                },
                {"type": "event", "event": {"type": "agent_settled"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.final_answer == ""
        assert task.result_text == task.work_log
        assert "主线最终回答" not in task.result_text

    @pytest.mark.asyncio
    async def test_custom_message_does_not_clear_final_answer(self, manager):
        """A `custom` message is not an input boundary.

        pi-plan-mode sends one from `agent_end` with `triggerTurn: false`, so no
        assistant text follows to restore `final_answer`; treating `custom` as a
        boundary would push the whole work log as the answer.
        """
        conn = FakeConnection(
            events=[
                {
                    "type": "event",
                    "event": {"type": "message_start", "message": {"role": "user"}},
                },
                {"type": "text", "text": "旁白：读了 3 个文件"},
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "主线最终回答"}],
                        },
                    },
                },
                {"type": "event", "event": {"type": "agent_end"}},
                # sendCustomMessage: message_start/end with role "custom",
                # triggerTurn false → nothing else follows.
                {
                    "type": "event",
                    "event": {
                        "type": "message_start",
                        "message": {"role": "custom"},
                    },
                },
                {
                    "type": "event",
                    "event": {
                        "type": "message_end",
                        "message": {
                            "role": "custom",
                            "content": [{"type": "text", "text": "计划待确认"}],
                        },
                    },
                },
                {"type": "event", "event": {"type": "agent_settled"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.final_answer == "主线最终回答"
        assert task.result_text == "主线最终回答"
        assert task.result_text != task.work_log


class TestInterruptDetection:
    """A settle is not proof of success: pi emits it from a `finally` block.

    `agent_settled.aborted` only mirrors an explicit abort request, so a run whose
    retries were exhausted (network/provider error) or whose output hit the token
    ceiling arrives as `agent_settled{aborted: false}`. Before this, the plugin
    reported such a run as ✅ completed and handed the chat half an answer.
    """

    async def _run_task(self, manager, events):
        conn = FakeConnection(
            events=[*events, _raw("agent_settled", aborted=False)]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        return task

    @pytest.mark.asyncio
    async def test_subtask_interruption_fails_and_names_the_phase(self, manager):
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _raw(
                    "auto_retry_start",
                    attempt=1,
                    maxAttempts=3,
                    errorMessage="fetch failed",
                ),
                _assistant_end(
                    "正在跑测试", stop_reason="error", error_message="fetch failed"
                ),
                _raw(
                    "auto_retry_end",
                    success=False,
                    attempt=3,
                    finalError="fetch failed",
                ),
            ],
        )
        assert task.status == "failed"
        assert task.interrupt_phase == "task"
        assert task.retry_final_error == "fetch failed"
        assert task.retry_attempts == 3
        assert task.stop_reason == "error"
        # The half sentence is not the answer, but it is not lost either.
        assert task.final_answer == ""
        assert task.partial_answer == "正在跑测试"
        assert task.result_text == "正在跑测试"
        assert manager._on_task_finished.await_args.args[1] == "failed"

    @pytest.mark.asyncio
    async def test_mainline_interruption_before_any_task(self, manager):
        task = await self._run_task(
            manager,
            [
                _raw(
                    "auto_retry_start",
                    attempt=1,
                    maxAttempts=3,
                    errorMessage="connection reset",
                ),
                _assistant_end(
                    "我准备", stop_reason="error", error_message="connection reset"
                ),
                _raw(
                    "auto_retry_end",
                    success=False,
                    attempt=3,
                    finalError="connection reset",
                ),
            ],
        )
        assert task.status == "failed"
        assert task.interrupt_phase == "mainline"
        assert task.partial_answer == "我准备"
        assert task.interruption_reason == "connection reset"

    @pytest.mark.asyncio
    async def test_summary_interruption_keeps_the_finished_answer(self, manager):
        """Sub-task done, mainline summary cut: both facts must survive."""
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _assistant_end("子任务过程：跑了 3 个测试", stop_reason="stop"),
                _raw("agent_end"),
                _entry_appended("custom_message", "task-result"),
                _assistant_end("主线最终回答", stop_reason="stop"),
                _assistant_end(
                    "补充……", stop_reason="error", error_message="fetch failed"
                ),
                _raw(
                    "auto_retry_end",
                    success=False,
                    attempt=1,
                    finalError="fetch failed",
                ),
            ],
        )
        assert task.status == "failed"
        assert task.interrupt_phase == "summary"
        assert task.final_answer == "主线最终回答"
        assert task.partial_answer == "补充……"

    @pytest.mark.asyncio
    async def test_length_stop_stays_completed_but_is_flagged(self, manager):
        """The output-token ceiling is not a failure: the text is usable."""
        task = await self._run_task(
            manager, [_assistant_end("被截断的回答", stop_reason="length")]
        )
        assert task.status == "completed"
        assert task.truncated is True
        assert task.interrupted is False
        assert task.final_answer == "被截断的回答"
        assert task.result_text == "被截断的回答"

    @pytest.mark.asyncio
    async def test_clean_run_is_neither_interrupted_nor_truncated(self, manager):
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _assistant_end("子任务过程", stop_reason="stop"),
                _entry_appended("custom_message", "task-result"),
                _assistant_end("主线最终回答", stop_reason="stop"),
            ],
        )
        assert task.status == "completed"
        assert task.interrupted is False
        assert task.truncated is False
        assert task.stop_reason == "stop"
        assert task.result_text == "主线最终回答"

    @pytest.mark.asyncio
    async def test_mainline_answer_after_subtask_interruption_is_the_result(
        self, manager
    ):
        """pi keeps the run going after a cut turn: the mainline answer wins.

        Flat mode (pi commit 365c1ae) delivers an interruption report as a
        `task-result` entry and lets the mainline answer again before
        `agent_settled`. That answer is the outcome; the interruption is only a
        warning, so the run is not a failure and the half sentence is not
        reported as the answer (a stuck `retry_final_error` used to do exactly
        that).
        """
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _raw(
                    "auto_retry_start",
                    attempt=1,
                    maxAttempts=3,
                    errorMessage="429 Too Many Requests",
                ),
                _assistant_end(
                    "正在跑测试", stop_reason="error", error_message="fetch failed"
                ),
                _raw(
                    "auto_retry_end",
                    success=False,
                    attempt=3,
                    finalError="fetch failed",
                ),
                _entry_appended("custom_message", "task-result"),
                _assistant_end("子任务被限流中断，已跳过该步继续。", stop_reason="stop"),
            ],
        )
        assert task.status == "completed"
        assert task.error is None
        assert manager._on_task_finished.await_args.args[1] == "completed"
        # The mainline answer is delivered, not the stale partial sentence.
        assert task.result_text == "子任务被限流中断，已跳过该步继续。"
        assert task.final_answer == "子任务被限流中断，已跳过该步继续。"
        assert task.partial_answer == "正在跑测试"
        # …while the interruption fact stays readable for the notice.
        assert task.interrupted is False
        assert task.interruption_recovered is True
        assert task.interrupt_phase == "task"
        assert task.interruption_reason == "fetch failed"

    @pytest.mark.asyncio
    async def test_mainline_answer_after_interruption_without_retry_events(
        self, manager
    ):
        """The position rule does not depend on `auto_retry_*` being emitted."""
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _assistant_end("补丁写了一半", stop_reason="error"),
                _entry_appended("custom_message", "task-result"),
                _assistant_end("子任务失败，我手工确认了改动。", stop_reason="stop"),
            ],
        )
        assert task.status == "completed"
        assert task.result_text == "子任务失败，我手工确认了改动。"
        assert task.interruption_recovered is True
        assert task.interrupt_phase == "task"

    @pytest.mark.asyncio
    async def test_interruption_followed_by_a_text_less_turn_still_fails(
        self, manager
    ):
        """A tool-call-only turn is not a recovery: the cut turn stays the result."""
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _assistant_end(
                    "正在跑测试", stop_reason="error", error_message="fetch failed"
                ),
                # pi's tool-call-only turn: no text block at all.
                _raw(
                    "message_end",
                    message={
                        "role": "assistant",
                        "content": [],
                        "stopReason": "toolUse",
                    },
                ),
            ],
        )
        assert task.status == "failed"
        assert task.interrupted is True
        assert task.result_text == "正在跑测试"
        assert task.interruption_recovered is False
        assert task.interrupt_phase == "task"

    @pytest.mark.asyncio
    async def test_aborted_turn_without_a_following_answer_is_a_failure(
        self, manager
    ):
        """`stopReason: "aborted"` is a cut turn too (INTERRUPTED_STOP_REASONS).

        Nothing followed it, so it is the outcome. The reason line names the stop
        reason it actually saw instead of claiming an `error`.
        """
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _assistant_end("开始了", stop_reason="aborted"),
            ],
        )
        assert task.status == "failed"
        assert task.interrupted is True
        assert task.interrupt_phase == "task"
        assert task.interruption_reason == "pi 报告 stopReason=aborted"
        assert task.result_text == "开始了"

    @pytest.mark.asyncio
    async def test_pi_retry_that_succeeds_is_not_an_interruption(self, manager):
        """pi's own successful retry clears the interruption state entirely."""
        task = await self._run_task(
            manager,
            [
                _entry_appended("custom", "task-start"),
                _assistant_end("429 限流", stop_reason="error", error_message="429"),
                _raw("auto_retry_start", attempt=1, maxAttempts=3, errorMessage="429"),
                _assistant_end("重试后的完整回答", stop_reason="stop"),
                _raw("auto_retry_end", success=True, attempt=1),
            ],
        )
        assert task.status == "completed"
        assert task.result_text == "重试后的完整回答"
        assert task.interrupted is False
        assert task.interruption_recovered is False
        assert task.interrupt_phase is None
        assert task.interrupt_reason is None
        assert task.partial_answer == ""


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
                {"type": "event", "event": {"type": "agent_settled"}},
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
        conn = FakeConnection(events=[{"type": "agent_settled"}])
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
                {"type": "agent_settled"},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert task.tool_call_count == 2

    @pytest.mark.asyncio
    async def test_unanswered_ui_request_is_cancelled_and_reported(self, monkeypatch):
        """An unattended dialog must not hold the run forever.

        pi only auto-resolves dialogs that carry their own timeout, so a dialog
        without one blocked the run (and the chat context) until the 1200s
        watchdog killed the connection. Now the plugin cancels it at the
        deadline, tells the chat, and keeps consuming the stream.
        """
        monkeypatch.setattr(tasks_module, "UI_REPLY_TIMEOUT", 0.05)
        on_ui_timeout = AsyncMock()
        manager = PiTaskManager(
            session_key_fn=_session_key,
            on_task_finished=AsyncMock(),
            on_ui_request=AsyncMock(),
            on_ui_timeout=on_ui_timeout,
        )
        ui_request = UIRequest(
            local_id=1, request_id="req-1", method="input", timeout_ms=0
        )
        conn = FakeConnection(
            events=[
                {"type": "ui_request", "request": ui_request},
                {"type": "event", "event": {"type": "agent_settled"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        await asyncio.wait_for(manager._runners["test-key"], timeout=2)
        assert conn.cancelled_ui_requests == ["req-1"]
        on_ui_timeout.assert_awaited_once()
        assert task.status == "completed"

    @pytest.mark.asyncio
    async def test_answered_ui_request_is_not_cancelled(self, manager):
        ui_request = UIRequest(
            local_id=1, request_id="req-2", method="confirm", timeout_ms=0
        )
        conn = FakeConnection(
            events=[
                {"type": "ui_request", "request": ui_request},
                {"type": "event", "event": {"type": "agent_settled"}},
            ]
        )
        task = await manager.launch(conn, "test-key", "prompt", "umo:1")
        deadline = asyncio.get_running_loop().time() + 1
        while task.status != "awaiting_ui":
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("runner did not reach awaiting_ui")
            await asyncio.sleep(0)
        manager.resume_ui("test-key")
        await asyncio.wait_for(manager._runners["test-key"], timeout=1)
        assert getattr(conn, "cancelled_ui_requests", []) == []
