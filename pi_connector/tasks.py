"""Per-chat background task runner for pi prompts."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .connection import PiConnection, PiError, PiIdleTimeout, PiResponseTimeout
from .models import UIRequest

# How long a background task may stay completely silent before it is treated as
# stuck. pi emits events while it works (streaming text, tool start/end), so a
# long silence means either a dead process or a hung request — and without this
# ceiling the runner would wait forever, keeping the chat context locked.
DEFAULT_IDLE_TIMEOUT = 1200.0

# How long pi may take to acknowledge the `prompt` command. Acceptance is
# immediate for a normal prompt and for an extension command that returns right
# away; an extension command that opens a dialog *without* its own timeout does
# not get acknowledged until the user answers (pi blocks the response, see
# `docs/rpc-extension-ui.md`), so this window expiring is not a failure — see
# `PiTaskManager.launch`.
PROMPT_ACK_TIMEOUT = 10.0

# How long an unattended chat waits for the user to answer an interactive pi
# dialog. This is the single deadline for background tasks: pi only auto-resolves
# dialogs that carry their own `timeout`, and the connection layer no longer
# expires tracked requests on a second, contradictory schedule (it used to sweep
# them after 300s, which left pi waiting while the plugin had already forgotten
# the request). When the deadline fires first, the request is cancelled on pi's
# side so the run continues instead of stalling for 20 minutes.
UI_REPLY_TIMEOUT = 30.0

# Message roles that start a new "answer scope". The chat's own prompt arrives as
# a `user` message, and a queued follow-up prompt that pi runs in the same run
# starts another one; whatever the assistant said before the newest boundary
# belongs to an older turn, so it is not the answer the chat user asked for.
#
# `custom` covers pi's `sendCustomMessage` path, which emits `message_start/end`
# with `role: "custom"` (dist/core/agent-session.js, used when an extension
# injects a user-visible message). It does NOT fire for this plugin's delivery
# path: the tree task extension ships a finished sub-task's `task-result` as an
# `entry_appended{entry:{type:"custom_message",customType:"task-result"}}`
# (agent-session `_applyBoundaryDrafts`), never as a `custom` message — a real
# pi 1.3.0 probe saw boundaries `['user']` only. The sub-task/mainline split
# therefore comes from ordering, not from this boundary: RPC stdout is one
# ordered stream and the mainline's assistant text always follows the
# sub-task's, so the last text-bearing `message_end` is the mainline answer.
INPUT_BOUNDARY_ROLES = frozenset({"user", "custom"})


def _assistant_text(message: dict[str, Any]) -> str:
    """Return the text of an assistant message from a `message_end` event.

    pi's `message_end.message` is the authoritative final message (pi
    `docs/json.md`: "This is the authoritative final message", and "Replace the
    whole partial message with `message_end.message` when it arrives") — the
    streamed `text_delta`s are only reconstructed display data.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _response_disposition(response: dict[str, Any]) -> str:
    """Return `data.disposition` of a pi command response (or "")."""
    data = response.get("data")
    if isinstance(data, dict):
        return str(data.get("disposition") or "")
    return ""


@dataclass
class PiTask:
    """A single background pi prompt execution for one chat context."""

    task_id: int
    status: str
    prompt: str
    started_at: float
    umo: str | None
    ui_resolved: asyncio.Event
    # Raw streaming transcript of the whole run (mainline narration + every
    # sub-task). Logs only: it is explicitly *not* what the chat receives.
    text_parts: list[str] = field(default_factory=list)
    # Authoritative answer: the last text-bearing assistant message of the run.
    # In rpc mode a finished sub-task's result arrives as an `entry_appended`,
    # so this is just the last text-bearing message of the ordered stream (the
    # mainline reply, which follows any sub-task text). This is what the user
    # is shown and what the wake-up event carries.
    final_answer: str = ""
    tool_call_count: int = 0
    error: str | None = None
    # pi reported the run as aborted (`agent_settled.aborted`).
    aborted: bool = False
    # The prompt was handled without starting a model run
    # (`disposition: "handled"`): no settle will ever arrive.
    no_model_run: bool = False
    notify_on_finish: bool = True

    @property
    def elapsed(self) -> float:
        """Return the number of seconds since the task started."""
        return time.time() - self.started_at

    @property
    def work_log(self) -> str:
        """Return the raw transcript of the run (sub-task process, narration)."""
        return "".join(self.text_parts)

    @property
    def result_text(self) -> str:
        """Return the text delivered to the chat and to the wake-up event.

        Falls back to the raw transcript only when no `message_end` was seen —
        an aborted or failed run has no authoritative final message, and the
        partial text is the honest thing to report then.
        """
        return self.final_answer or self.work_log


class PiTaskManager:
    """Owns at most one active pi background task per chat context."""

    def __init__(
        self,
        session_key_fn: Callable[[Any], str],
        on_task_finished: Callable[[PiTask, str, str, str | None], Awaitable[None]],
        on_ui_request: Callable[[PiTask, UIRequest], Awaitable[None]],
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        idle_timeout_provider: Callable[[], float] | None = None,
        on_ui_timeout: Callable[[PiTask, UIRequest], Awaitable[None]] | None = None,
    ):
        self._session_key_fn = session_key_fn
        self._on_task_finished = on_task_finished
        self._on_ui_request = on_ui_request
        self._on_ui_timeout = on_ui_timeout
        self._idle_timeout = float(idle_timeout)
        self._idle_timeout_provider = idle_timeout_provider
        self._active: dict[str, PiTask] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._counters: dict[str, int] = {}
        self._closed = False

    @property
    def idle_timeout(self) -> float:
        """Idle timeout in seconds; read lazily so config edits apply live."""
        if self._idle_timeout_provider is not None:
            try:
                return float(self._idle_timeout_provider())
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idle timeout provider failed (%s); using %.0fs",
                    exc,
                    self._idle_timeout,
                )
        return self._idle_timeout

    def is_running(self, key: str) -> bool:
        """Return True when a task is active (running or awaiting UI reply)."""
        task = self._active.get(key)
        if task is None:
            return False
        return task.status in ("running", "awaiting_ui")

    def get_active(self, key: str) -> PiTask | None:
        """Return the active task for the key, or None if no task is running."""
        task = self._active.get(key)
        if task is None:
            return None
        if task.status in ("running", "awaiting_ui"):
            return task
        return None

    async def launch(
        self,
        conn: PiConnection,
        key: str,
        prompt: str,
        umo: str | None,
    ) -> PiTask:
        """Send a prompt RPC command and spawn a background runner for it.

        Args:
            conn: The pi RPC connection for this chat context.
            key: Unique chat context key.
            prompt: The prompt text or slash command to send to pi.
            umo: The chat's unified message origin, used for notifications.

        Returns:
            The newly created task.

        Raises:
            RuntimeError: If a task is already running for the key, or if pi
                rejects the prompt command.
        """
        if self.is_running(key):
            raise RuntimeError(f"A pi background task is already running for {key}")

        self._counters[key] = self._counters.get(key, 0) + 1
        task = PiTask(
            task_id=self._counters[key],
            status="running",
            prompt=prompt,
            started_at=time.time(),
            umo=umo,
            ui_resolved=asyncio.Event(),
        )
        self._active[key] = task

        handled = False
        try:
            command: dict[str, Any] = {"type": "prompt", "message": prompt}
            # drain_on_timeout=False: if pi has not acknowledged the prompt in
            # time, the queued events must survive — the one that is blocking
            # the acknowledgment is usually the dialog we still have to show.
            response = await conn.send_command(
                command, timeout=PROMPT_ACK_TIMEOUT, drain_on_timeout=False
            )
            if not response.get("success", False):
                error = response.get("error", "prompt was rejected by pi")
                task.status = "failed"
                task.error = error
                raise RuntimeError(error)
            handled = _response_disposition(response) == "handled"
        except PiResponseTimeout:
            # An extension command that opens a dialog (confirm/select/…) without
            # its own timeout does not acknowledge the prompt until the user
            # answers, so this window expires while the dialog request is already
            # sitting on the event stream. The old code failed the task *and*
            # drained the queue, so the question never reached the user at all
            # while pi stayed blocked on it. Hand the stream to the runner
            # instead: it surfaces the dialog (and cancels it at its deadline),
            # then finishes on the response sentinel the connection enqueues
            # once pi finally acknowledges the prompt (`prompt_handled`). A
            # genuinely wedged process is still caught by the idle watchdog.
            logger.warning(
                "pi did not acknowledge the prompt for task #%s within %.0fs; "
                "assuming a dialog is blocking it, the runner will surface it",
                task.task_id,
                PROMPT_ACK_TIMEOUT,
            )
        except Exception:
            self._active.pop(key, None)
            raise

        if handled:
            # Extension command / handled input: pi started no run for this
            # prompt, so no `agent_settled` will ever arrive
            # (pi docs/rpc.md:67 — "If it is `handled`, no run started for this
            # prompt, so don't wait for `agent_settled`").
            #
            # This is the real root cause behind commit 29dda10, which rolled
            # `wait only for agent_settled` back after every background task
            # "got stuck": the tasks were extension commands, and the client sat
            # in `read_events` for the full 1200s watchdog before failing the
            # task and killing the connection. Keep this branch (and the
            # `prompt_handled` sentinel in `PiConnection.read_events`) when
            # touching the completion logic; do not go back to waiting only for
            # a settle.
            await self._finish_without_run(key, task)
            return task

        if self._active.get(key) is not task:
            raise RuntimeError("Task was cancelled before start")

        runner = asyncio.create_task(self._run(conn, key, task))
        self._runners[key] = runner
        return task

    async def _finish_without_run(self, key: str, task: PiTask) -> None:
        """Finish a task whose prompt started no model run (`disposition: handled`)."""
        task.status = "completed"
        task.no_model_run = True
        self._active.pop(key, None)
        logger.info("Task #%s handled by pi without a model run", task.task_id)
        if task.notify_on_finish:
            await self._notify_finished(task)

    async def _notify_finished(self, task: PiTask) -> None:
        """Invoke the completion callback without letting it block teardown."""
        logger.info("Notifying task #%s", task.task_id)
        try:
            await asyncio.wait_for(
                self._on_task_finished(task, task.status, task.result_text, task.error),
                timeout=30,
            )
            logger.info("Notified task #%s", task.task_id)
        except asyncio.TimeoutError:
            logger.warning(
                "Notification for task #%s timed out after 30s; task unblocked anyway",
                task.task_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notification for task #%s failed: %s", task.task_id, exc)

    async def _await_ui_reply(
        self, conn: PiConnection, task: PiTask, request: UIRequest
    ) -> None:
        """Surface an interactive dialog and wait, bounded, for the user.

        Background tasks are unattended, so the deadline is the plugin's
        (`UI_REPLY_TIMEOUT`), shortened by pi's own dialog timeout when it has
        one. When our deadline fires first the request is cancelled on pi's side
        — pi only auto-resolves dialogs that carry their own `timeout`, and
        otherwise waits forever while the run (and the chat context) stays
        blocked. The user is told that the request expired.
        """
        task.status = "awaiting_ui"
        task.ui_resolved = asyncio.Event()
        await self._on_ui_request(task, request)

        pi_timeout = (request.timeout_ms or 0) / 1000
        timeout = (
            UI_REPLY_TIMEOUT if pi_timeout <= 0 else min(pi_timeout, UI_REPLY_TIMEOUT)
        )
        try:
            await asyncio.wait_for(task.ui_resolved.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "UI request %s for task #%s not answered within %.0fs",
                getattr(request, "request_id", "?"),
                task.task_id,
                timeout,
            )
            if pi_timeout <= 0 or pi_timeout > UI_REPLY_TIMEOUT:
                # pi is still waiting for this answer: cancel it so the run can
                # continue, and tell the chat nobody answered.
                try:
                    await conn.cancel_ui_request(request.request_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to cancel expired pi UI request %s: %s",
                        getattr(request, "request_id", "?"),
                        exc,
                    )
                if self._on_ui_timeout is not None:
                    try:
                        await self._on_ui_timeout(task, request)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Failed to report expired pi UI request: %s", exc
                        )
        task.status = "running"

    async def _run(self, conn: PiConnection, key: str, task: PiTask) -> None:
        """Consume pi events until the run settles and report the outcome."""
        final_status = "running"
        final_error: str | None = None
        saw_settled = False
        saw_handled = False
        idle_timeout = self.idle_timeout
        try:
            async for event in conn.read_events(event_timeout=idle_timeout):
                event_type = event.get("type")
                if event_type == "text":
                    # Raw transcript: logs only, never the chat result.
                    task.text_parts.append(event.get("text", ""))
                elif event_type == "ui_request":
                    request = event.get("request")
                    if request is None:
                        continue
                    await self._await_ui_reply(conn, task, request)
                elif event_type == "event":
                    raw = event.get("event", {})
                    raw_type = raw.get("type")
                    if raw_type == "tool_execution_start":
                        task.tool_call_count += 1
                    elif raw_type == "message_start":
                        # New input boundary: whatever the assistant said before
                        # it belongs to an earlier turn (a follow-up prompt
                        # queued during the same run starts one).
                        message = raw.get("message") or {}
                        if message.get("role") in INPUT_BOUNDARY_ROLES:
                            task.final_answer = ""
                    elif raw_type == "message_end":
                        # `message_end.message` is the authoritative final
                        # message for that assistant turn; the last one with
                        # text wins (tool-call-only turns have no text and must
                        # not erase the answer). Sub-task text is superseded by
                        # the mainline text that follows it in the stream.
                        message = raw.get("message") or {}
                        if message.get("role") == "assistant":
                            text = _assistant_text(message)
                            if text.strip():
                                task.final_answer = text
                    elif raw_type == "agent_settled":
                        # agent_end only closes one low-level run; agent_settled
                        # is the only "pi will not continue" signal.
                        saw_settled = True
                        task.aborted = bool(raw.get("aborted"))
                    elif raw_type == "prompt_handled":
                        # The prompt response arrived after `launch` stopped
                        # waiting for it (dialog blocked the acknowledgment):
                        # pi started no run, so nothing will ever settle.
                        saw_handled = True
            if task.aborted:
                # pi itself ended the run as aborted. Reporting "completed"
                # here used to tell the user "✅ 完成" and hand them half an
                # answer after /pi abort timed out.
                final_status = "aborted"
            elif saw_handled:
                final_status = "completed"
                task.no_model_run = True
            elif saw_settled:
                final_status = "completed"
            else:
                # The stream ended without agent_settled: the run was cut short.
                final_status = "failed"
                final_error = "pi event stream ended without agent_settled"
        except PiIdleTimeout:
            # Watchdog: nothing at all for idle_timeout seconds. The process may
            # be wedged and we cannot tell, so fail the task and drop the
            # connection — otherwise this chat context stays locked forever.
            final_status = "failed"
            final_error = (
                f"pi 已 {int(idle_timeout)}s 无任何输出，按卡死处理"
                "（连接已断开，可重新发起任务）"
            )
            task.error = final_error
            try:
                await conn.terminate()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to drop idle pi connection: %s", exc)
        except asyncio.CancelledError:
            final_status = "aborted"
            final_error = "Task was cancelled"
            raise
        except PiError as exc:
            final_status = "failed"
            final_error = str(exc)
            task.error = final_error
            if "Timeout waiting for pi response events" in str(exc):
                # read_events() converted its idle timeout into PiError, so the
                # asyncio.TimeoutError watchdog above never fires for the
                # streaming case. Treat it the same way: the process may be
                # wedged (e.g. stuck on an unanswered UI request), so fail the
                # task with the watchdog message and drop the connection —
                # otherwise a zombie connection stalls the next task on this
                # chat context.
                final_error = (
                    f"pi 已 {int(idle_timeout)}s 无任何输出，按卡死处理"
                    "（连接已断开，可重新发起任务）"
                )
                task.error = final_error
                try:
                    await conn.terminate()
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("Failed to drop idle pi connection: %s", exc2)
        except Exception as exc:  # noqa: BLE001
            final_status = "failed"
            final_error = str(exc)
            task.error = final_error
        finally:
            if task.status in ("running", "awaiting_ui"):
                task.status = final_status
            if task.error is None:
                task.error = final_error
            logger.info(
                "Runner finished: status=%s settled=%s handled=%s aborted=%s "
                "answer_len=%d transcript_len=%d",
                task.status,
                saw_settled,
                saw_handled,
                task.aborted,
                len(task.final_answer),
                len(task.work_log),
            )
            if task.work_log:
                # Sub-task process and mainline narration stay in the log: the
                # chat only gets `result_text` (the final answer).
                logger.debug(
                    "Task #%s transcript: %s", task.task_id, task.work_log[:2000]
                )
            await conn.drain_events(timeout=0.5)
            self._active.pop(key, None)
            self._runners.pop(key, None)
            if task.notify_on_finish:
                await self._notify_finished(task)

    def resume_ui(self, key: str) -> bool:
        """Resume a task that is awaiting a UI reply.

        Args:
            key: Unique chat context key.

        Returns:
            True if a task was awaiting a reply and has been resumed.
        """
        task = self.get_active(key)
        if task is None or task.status != "awaiting_ui":
            return False
        task.ui_resolved.set()
        task.status = "running"
        return True

    async def cancel(self, key: str, notify: bool = True) -> PiTask | None:
        """Cancel the active task for a chat context.

        Args:
            key: Unique chat context key.
            notify: If False, suppress the completion notification.

        Returns:
            The cancelled task, or None if no task was running.
        """
        task = self.get_active(key)
        if task is None:
            return None
        if task.status not in ("running", "awaiting_ui"):
            # Terminal already (the runner finished while we were called): never
            # rewrite a completed/aborted task, or the user gets two outcomes.
            return None
        task.status = "aborted"
        task.notify_on_finish = notify
        runner = self._runners.get(key)
        if runner is not None and not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        self._active.pop(key, None)
        self._runners.pop(key, None)
        return task

    async def cancel_all(self) -> None:
        """Cancel all active tasks, used during plugin shutdown."""
        self._closed = True
        for key in list(self._runners.keys()):
            await self.cancel(key, notify=False)
