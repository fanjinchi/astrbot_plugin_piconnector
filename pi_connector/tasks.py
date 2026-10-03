"""Per-chat background task runner for pi prompts."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .connection import PiConnection, PiError
from .models import UIRequest


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
    ):
        self._session_key_fn = session_key_fn
        self._on_task_finished = on_task_finished
        self._on_ui_request = on_ui_request
        self._active: dict[str, PiTask] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._counters: dict[str, int] = {}
        self._closed = False

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
        """Consume pi events until agent_end and report the outcome."""
        final_status = "running"
        final_error: str | None = None
        try:
            async for event in conn.read_events(event_timeout=None):
                event_type = event.get("type")
                if event_type == "text":
                    task.text_parts.append(event.get("text", ""))
                elif event_type == "ui_request":
                    request = event.get("request")
                    if request is None:
                        continue
                    task.status = "awaiting_ui"
                    task.ui_resolved = asyncio.Event()
                    await self._on_ui_request(task, request)
                    timeout = (request.timeout_ms or 300_000) / 1000
                    try:
                        await asyncio.wait_for(task.ui_resolved.wait(), timeout=timeout)
                    except asyncio.TimeoutError:
                        # pi may continue without a reply; resume consuming.
                        pass
                    task.status = "running"
                elif event_type == "event":
                    raw = event.get("event", {})
                    if raw.get("type") == "tool_execution_start":
                        task.tool_call_count += 1
            final_status = "completed"
        except asyncio.CancelledError:
            final_status = "aborted"
            final_error = "Task was cancelled"
            raise
        except PiError as exc:
            final_status = "failed"
            final_error = str(exc)
            task.error = final_error
        except Exception as exc:  # noqa: BLE001
            final_status = "failed"
            final_error = str(exc)
            task.error = final_error
        finally:
            if task.status in ("running", "awaiting_ui"):
                task.status = final_status
            if task.error is None:
                task.error = final_error
            await conn.drain_events(timeout=0.5)
            self._active.pop(key, None)
            self._runners.pop(key, None)
            if task.notify_on_finish:
                await self._on_task_finished(
                    task, task.status, task.result_text, task.error
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
