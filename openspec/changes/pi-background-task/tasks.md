# Tasks: pi-background-task

## 1. Connection layer

- [x] 1.1 Add `event_timeout: float | None = 300` parameter to `PiConnection.send_prompt` and `PiConnection.read_response`; `None` waits indefinitely between events
- [x] 1.2 Add `PiConnection.read_events()` async generator that yields normalized events until `agent_end` (no prompt send), with an optional `event_timeout` parameter
- [x] 1.3 Add/refresh connection tests for the new timeout behavior and `read_events()`

## 2. Background task core (`pi_connector/tasks.py`)

- [x] 2.1 Implement `PiTask` dataclass: `task_id`, `status`, `prompt`, `started_at`, text parts, tool-call counter, `ui_resolved` event, final result accessors
- [x] 2.2 Implement `PiTaskManager`: per-chat active task registry (same `_session_key` scheme as `PiConnectionManager`), launch (send `prompt` RPC + spawn runner), `is_running`/`get_active`, `cancel`, `cancel_all`
- [x] 2.3 Implement the runner coroutine: consume `read_events()`, accumulate text deltas, count tool executions, tolerate silent periods, and invoke the completion callback with `(task, status, text, error)`
- [x] 2.4 Implement UI request coordination: on `ui_request` set `awaiting_ui`, push via callback, wait on `ui_resolved`, resume after `reply_to_ui_request()` is called by a command/tool handler
- [x] 2.5 Unit tests for launch, rejection of a second launch, completion callback, failure path, abort, and UI pause/resume (mocked `PiConnection`)

## 3. Plugin integration (`main.py`)

- [x] 3.1 Instantiate `PiTaskManager` in the plugin, wired to the connection manager; cancel tasks on `/pi quit` and `terminate()`
- [x] 3.2 Add notification helpers: push task outcome to the chat via `Context.send_message` (header + chunked full result), and inject outcome into conversation history via `conversation_manager.get_curr_conversation_id` + `add_message_pair` (truncate oversized text)
- [x] 3.3 Convert `pi_send_message` and `pi_run_command` LLM tools to async launchers: reject when a task is active (report id + elapsed), otherwise launch and return the guidance text (no polling, no re-send, no abort)
- [x] 3.4 Update `pi_get_session_info` to append active task id, elapsed time, and the "silence is normal, do not poll/abort" hint
- [x] 3.5 Add required `user_confirmed: bool` argument to `pi_abort`; refuse when not true; on true send RPC abort and cancel the runner
- [x] 3.6 Route `pi_reply_ui` and `/pi confirm|select|input|edit|cancel` through the active runner (forward reply + resume) when a task is awaiting a reply; keep existing consume-after-reply behavior when no task is active
- [x] 3.7 Reject `/pi <text>` and `/pic <command>` with a "task #N still running" message while a task is active; make `/pi abort` cancel the runner; show active task status in `/pi session`
- [x] 3.8 Update tool docstrings (agent-visible) to describe the async contract

## 4. Docs and tests

- [x] 4.1 Update `skills/pi-connector/SKILL.md` with the background-task workflow and anti-polling/anti-abort guidance
- [x] 4.2 Update `README.md` (LLM tools sections, both languages) for the async behavior
- [x] 4.3 Update existing tests for changed tool/command behavior; add `tests/test_tasks.py` and notification-helper tests (mock `Context`)
- [x] 4.4 Run `uv run pytest tests/ -v` and `uv run ruff check main.py pi_connector tests/`; fix all failures
