"""Low-level pi RPC connection over stdin/stdout JSONL."""

import asyncio
import json
import os
import subprocess
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

from astrbot.api import logger

from .models import UIRequest


class PiError(Exception):
    """Raised when the pi RPC process returns an error or fails."""

    pass


class PiConnection:
    """A single long-running connection to a local pi agent via RPC mode."""

    def __init__(
        self,
        session_path: Optional[str] = None,
        session_dir: Optional[str] = None,
        cwd: Optional[str] = None,
        name: Optional[str] = None,
        executable: str = "pi",
    ):
        self.session_path = session_path
        self.session_dir = session_dir
        self.cwd = cwd
        self.name = name
        self.executable = executable

        self.process: Optional[subprocess.Popen] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._pending_responses: Dict[str, asyncio.Future] = {}
        self.pending_ui_requests: Dict[str, UIRequest] = {}
        self._closed = False
        self._next_id_counter = 0
        self._ui_request_counter = 0

    def _next_command_id(self) -> str:
        """Return a unique command correlation id."""
        self._next_id_counter += 1
        return f"pi-cmd-{self._next_id_counter}-{uuid.uuid4().hex[:8]}"

    def _next_ui_local_id(self) -> int:
        """Return a short local id for a pi UI request."""
        self._ui_request_counter += 1
        return self._ui_request_counter

    async def start(self) -> None:
        """Spawn the pi --mode rpc subprocess and start reader loops."""
        if self.process is not None:
            logger.warning("PiConnection already started")
            return

        args = [self.executable, "--mode", "rpc"]
        if self.session_dir:
            args.extend(["--session-dir", self.session_dir])
        if self.session_path:
            args.extend(["--session", self.session_path])
        if self.name:
            args.extend(["--name", self.name])

        cwd = self.cwd if self.cwd else None
        logger.info("Starting pi RPC process: %s in cwd=%s", " ".join(args), cwd)
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            bufsize=1,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _reader_loop(self) -> None:
        """Read JSONL lines from pi stdout and dispatch them."""
        if self.process is None or self.process.stdout is None:
            return

        try:
            while not self._closed and self.process.poll() is None:
                try:
                    line = await asyncio.get_event_loop().run_in_executor(
                        None, self.process.stdout.readline
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error reading from pi stdout: %s", exc)
                    break

                if not line:
                    if self.process.poll() is not None:
                        break
                    continue

                line = line.rstrip("\r\n")
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Failed to parse pi JSONL line: %s (%s)", line, exc)
                    continue

                await self._handle_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("Pi reader loop crashed: %s", exc)
        finally:
            logger.info("Pi reader loop ended")

    async def _stderr_loop(self) -> None:
        """Log pi stderr lines for debugging."""
        if self.process is None or self.process.stderr is None:
            return

        try:
            while not self._closed and self.process.poll() is None:
                try:
                    line = await asyncio.get_event_loop().run_in_executor(
                        None, self.process.stderr.readline
                    )
                except Exception:  # noqa: BLE001
                    break
                if not line:
                    break
                line = line.rstrip("\r\n")
                if line:
                    logger.debug("pi stderr: %s", line)
        except Exception as exc:  # noqa: BLE001
            logger.error("Pi stderr reader crashed: %s", exc)

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Route events to pending responses or the general event queue."""
        event_type = event.get("type")
        event_id = event.get("id")

        if event_type == "response" and event_id and event_id in self._pending_responses:
            future = self._pending_responses.pop(event_id)
            if not future.done():
                future.set_result(event)
            return

        if event_type == "extension_ui_request":
            self._track_ui_request(event)

        await self._event_queue.put(event)

    def _track_ui_request(self, event: Dict[str, Any]) -> None:
        """Store a pi extension UI request for later user reply."""
        import time

        request_id = event.get("id")
        if not request_id:
            return

        method = event.get("method", "unknown")
        local_id = self._next_ui_local_id()
        ui_request = UIRequest(
            local_id=local_id,
            request_id=request_id,
            method=method,
            title=event.get("title", ""),
            message=event.get("message", ""),
            options=event.get("options", []),
            prefill=event.get("prefill", ""),
            timeout_ms=event.get("timeout"),
            created_at=time.time(),
        )
        self.pending_ui_requests[request_id] = ui_request
        logger.info("Tracked pi UI request: %s", ui_request.to_dict())

    async def _send_raw(self, command: Dict[str, Any]) -> None:
        """Send a JSONL command to pi stdin."""
        if self.process is None or self.process.stdin is None:
            raise PiError("Pi process is not running")

        try:
            line = json.dumps(command, ensure_ascii=False) + "\n"
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            raise PiError(f"Failed to send command to pi: {exc}") from exc

    async def send_command(self, command: Dict[str, Any], timeout: float = 30) -> Dict[str, Any]:
        """Send an RPC command and wait for the matching response."""
        cmd_id = command.get("id") or self._next_command_id()
        command = {**command, "id": cmd_id}

        future = asyncio.get_event_loop().create_future()
        self._pending_responses[cmd_id] = future

        await self._send_raw(command)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending_responses.pop(cmd_id, None)
            raise PiError(
                f"Timeout waiting for response to command {command.get('type')}"
            ) from exc

    async def send_prompt(
        self,
        message: str,
        images: Optional[list] = None,
        streaming_behavior: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Send a natural language prompt and yield response events.

        Yields dictionaries describing pi events, including text deltas and
        extension UI requests. The caller is responsible for handling UI
        requests and asking the user to reply with a dedicated subcommand.
        """
        if not message or not message.strip():
            raise PiError("Prompt message cannot be empty")

        command: Dict[str, Any] = {"type": "prompt", "message": message}
        if images:
            command["images"] = images
        if streaming_behavior:
            command["streamingBehavior"] = streaming_behavior

        response = await self.send_command(command, timeout=10)
        if not response.get("success", False):
            raise PiError(response.get("error", "prompt was rejected by pi"))

        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=300)
            except asyncio.TimeoutError as exc:
                raise PiError("Timeout waiting for pi response events") from exc

            yield self._normalize_event(event)

            if event.get("type") == "agent_end":
                break

    def _normalize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw pi event into a simpler event for consumers."""
        event_type = event.get("type")

        if event_type == "message_update":
            delta = event.get("assistantMessageEvent", {})
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                return {"type": "text", "text": delta.get("delta", "")}
            if delta_type == "thinking_delta":
                return {"type": "thinking", "text": delta.get("delta", "")}

        if event_type == "extension_ui_request":
            request_id = event.get("id")
            ui_request = self.pending_ui_requests.get(request_id)
            if ui_request:
                return {"type": "ui_request", "request": ui_request}

        return {"type": "event", "event": event}

    async def new_session(self, parent_session: Optional[str] = None) -> Dict[str, Any]:
        """Start a fresh session in the current RPC process."""
        command: Dict[str, Any] = {"type": "new_session"}
        if parent_session:
            command["parentSession"] = parent_session
        return await self.send_command(command)

    async def switch_session(self, session_path: str) -> Dict[str, Any]:
        """Switch to a different session file."""
        return await self.send_command(
            {"type": "switch_session", "sessionPath": session_path}
        )

    async def get_state(self) -> Dict[str, Any]:
        """Get current session state."""
        response = await self.send_command({"type": "get_state"})
        if response.get("success") and "data" in response:
            return response["data"]
        raise PiError(response.get("error", "get_state failed"))

    async def get_commands(self) -> list:
        """Get available slash commands."""
        response = await self.send_command({"type": "get_commands"})
        if response.get("success") and "data" in response:
            return response["data"].get("commands", [])
        raise PiError(response.get("error", "get_commands failed"))

    async def abort(self) -> Dict[str, Any]:
        """Abort the current operation."""
        return await self.send_command({"type": "abort"}, timeout=10)

    async def reply_ui_request(self, request_id: str, value: Any) -> None:
        """Send a value response for a select/input/editor UI request."""
        await self._send_raw(
            {"type": "extension_ui_response", "id": request_id, "value": value}
        )
        self.pending_ui_requests.pop(request_id, None)

    async def confirm_ui_request(self, request_id: str, confirmed: bool) -> None:
        """Send a confirmation response for a confirm UI request."""
        await self._send_raw(
            {"type": "extension_ui_response", "id": request_id, "confirmed": confirmed}
        )
        self.pending_ui_requests.pop(request_id, None)

    async def cancel_ui_request(self, request_id: str) -> None:
        """Cancel an extension UI request."""
        await self._send_raw(
            {"type": "extension_ui_response", "id": request_id, "cancelled": True}
        )
        self.pending_ui_requests.pop(request_id, None)

    async def drain_events(self, timeout: float = 0.5) -> None:
        """Drain remaining events from the queue."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def terminate(self) -> None:
        """Stop the pi process and reader loops."""
        self._closed = True
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    try:
                        await self._send_raw({"type": "abort"})
                        await asyncio.sleep(0.2)
                    except PiError:
                        pass
                    self.process.terminate()
                    try:
                        await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None, self.process.wait
                            ),
                            timeout=5,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Pi process did not terminate, killing it")
                        self.process.kill()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error terminating pi process: %s", exc)
            finally:
                self.process = None

        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._reader_task = None
        self._stderr_task = None
