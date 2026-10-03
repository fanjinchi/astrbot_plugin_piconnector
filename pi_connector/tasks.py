"""Per-chat background task runner for pi prompts."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .connection import PiConnection, PiError, PiIdleTimeout
from .models import UIRequest

logger = logging.getLogger(__name__)

# How long a background task may stay completely silent before it is treated as
# stuck. pi emits events while it works (streaming text, tool start/end), so a
# long silence means either a dead process or a hung request — and without this
# ceiling the runner would wait forever, keeping the chat context locked.
DEFAULT_IDLE_TIMEOUT = 1200.0


@dataclass
class PiTask:
    """A single background pi prompt execution for one chat context."""

    task_id: int
    status: str
    prompt: str
    started_at: float
    umo: str | None
    ui_resolved: asyncio.Event
    text_parts: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    error: str | None = None
    notify_on_finish: bool = True

    @property
    def elapsed(self) -> float:
        """Return the number of seconds since the task started."""
        return time.time() - self.started_at

    @property
    def result_text(self) -> str:
        """Return the accumulated assistant text."""
        return "".join(self.text_parts)


class PiTaskManager:
    """Owns at most one active pi background task per chat context."""

    def __init__(
        self,
        session_key_fn: Callable[[Any], str],
        on_task_finished: Callable[[PiTask, str, str, str | None], Awaitable[None]],
        on_ui_request: Callable[[PiTask, UIRequest], Awaitable[None]],
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        idle_timeout_provider: Callable[[], float] | None = None,
    ):
        self._session_key_fn = session_key_fn
        self._on_task_finished = on_task_finished
        self._on_ui_request = on_ui_request
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

        try:
            command: dict[str, Any] = {"type": "prompt", "message": prompt}
            response = await conn.send_command(command, timeout=10)
            if not response.get("success", False):
                error = response.get("error", "prompt was rejected by pi")
                task.status = "failed"
                task.error = error
                raise RuntimeError(error)
        except Exception:
            self._active.pop(key, None)
            raise

        if self._active.get(key) is not task:
            raise RuntimeError("Task was cancelled before start")

        runner = asyncio.create_task(self._run(conn, key, task))
        self._runners[key] = runner
        return task

    async def _run(self, conn: PiConnection, key: str, task: PiTask) -> None:
        """Consume pi events until the run settles and report the outcome."""
        final_status = "running"
        final_error: str | None = None
        saw_agent_end = False
        try:
            idle_timeout = self.idle_timeout
            async for event in conn.read_events(event_timeout=idle_timeout):
                event_type = event.get("type")
                if event_type == "text":
                    task.text_parts.append(event.get("text", ""))
                elif event_type in ("agent_end", "agent_settled"):
                    # Tests feed raw events; the real connection normalizes them to
                    # {"type": "event", "event": {...}} — accept both shapes.
                    # agent_settled is the true completion signal; agent_end is
                    # accepted on its own so pre-settled pi builds still finish.
                    saw_agent_end = True
                elif event_type == "ui_request":
                    request = event.get("request")
                    if request is None:
                        continue
                    task.status = "awaiting_ui"
                    task.ui_resolved = asyncio.Event()
                    await self._on_ui_request(task, request)
                    # Background tasks are unattended: pi's default 300 s UI wait
                    # left already-completed work queued behind the stall for
                    # minutes (observed: a run finished at 01:37:48 but only
                    # resumed at 01:42:48). Cap the wait so the run moves on; the
                    # user can still answer and have it applied if they notice.
                    timeout = min((request.timeout_ms or 300_000) / 1000, 30.0)
                    try:
                        await asyncio.wait_for(task.ui_resolved.wait(), timeout=timeout)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "UI request %s for task #%s not answered within %.0fs; "
                            "resuming event consumption",
                            getattr(request, "request_id", "?"),
                            task.task_id,
                            timeout,
                        )
                    task.status = "running"
                elif event_type == "event":
                    raw = event.get("event", {})
                    if raw.get("type") == "tool_execution_start":
                        task.tool_call_count += 1
                    elif raw.get("type") in ("agent_end", "agent_settled"):
                        # This is how the real connection delivers them.
                        saw_agent_end = True
            if saw_agent_end:
                final_status = "completed"
            else:
                # The stream ended without agent_end: the run was cut short.
                final_status = "failed"
                final_error = "pi event stream ended without agent_end/agent_settled"
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
                "[pi-diag] runner finishing: status=%s saw_agent_end=%s text_len=%d",
                task.status,
                saw_agent_end,
                len("".join(task.text_parts)),
            )
            await conn.drain_events(timeout=0.5)
            self._active.pop(key, None)
            self._runners.pop(key, None)
            if task.notify_on_finish:
                logger.info("[pi-diag] notifying task #%s", task.task_id)
                # Never let the notification block task teardown: if the platform
                # send hangs, the task would otherwise stay "running" forever and
                # hold its chat context.
                try:
                    await asyncio.wait_for(
                        self._on_task_finished(
                            task, task.status, task.result_text, task.error
                        ),
                        timeout=30,
                    )
                    logger.info("[pi-diag] notify done for task #%s", task.task_id)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Notification for task #%s timed out after 30s; task "
                        "unblocked anyway",
                        task.task_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Notification for task #%s failed: %s", task.task_id, exc
                    )

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
