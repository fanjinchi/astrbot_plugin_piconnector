# Tasks: pi-background-task

## 1. Connection layer

- [x] 1.1 Add `event_timeout: float | None = 300` parameter to `PiConnection.send_prompt` and `PiConnection.read_response`; `None` waits indefinitely between events
- [x] 1.2 Add `PiConnection.read_events()` async generator that yields normalized events until `agent_settled` (no prompt send), with an optional `event_timeout` parameter
- [x] 1.3 Add/refresh connection tests for the new timeout behavior and `read_events()`

## 2. Background task core (`pi_connector/tasks.py`)

- [x] 2.1 Implement `PiTask` dataclass: `task_id`, `status`, `prompt`, `started_at`, text parts, tool-call counter, `ui_resolved` event, final result accessors
- [x] 2.2 Implement `PiTaskManager`: per-chat active task registry (same `_session_key` scheme as `PiConnectionManager`), launch (send `prompt` RPC + spawn runner), `is_running`/`get_active`, `cancel`, `cancel_all`
- [x] 2.3 Implement the runner coroutine: consume `read_events()`, count tool executions, tolerate silent periods, extract the mainline final answer (D9), and invoke the completion callback with `(task, status, text, error)`
- [x] 2.4 Implement UI request coordination: on `ui_request` set `awaiting_ui`, push via callback, wait on `ui_resolved`, resume after `reply_to_ui_request()` is called by a command/tool handler
- [x] 2.5 Unit tests for launch, rejection of a second launch, completion callback, failure path, abort, and UI pause/resume (mocked `PiConnection`)

## 3. Plugin integration (`main.py`)

- [x] 3.1 Instantiate `PiTaskManager` in the plugin, wired to the connection manager; cancel tasks on `/pi quit` and `terminate()`
- [x] 3.2 Add notification helpers: push the task outcome to the chat via `Context.send_message` (header + chunked mainline final answer), and wake the main agent with the *same* text through `Context.get_event_queue` + `AstrBotMessage.is_wake` plus a "do not repeat it" instruction (D3)
- [x] 3.3 Convert `pi_send_message` and `pi_run_command` LLM tools to async launchers: reject when a task is active (report id + elapsed), otherwise launch and return the guidance text (no polling, no re-send, no abort)
- [x] 3.4 Update `pi_get_session_info` to append active task id, elapsed time, and the "silence is normal, do not poll/abort" hint
- [x] 3.5 Add required `user_confirmed: bool` argument to `pi_abort`; refuse when not true; on true send RPC abort and cancel the runner
- [x] 3.6 Route `pi_reply_ui` and `/pi confirm|select|input|edit|cancel` through the active runner (forward reply + resume) when a task is awaiting a reply; keep existing consume-after-reply behavior when no task is active
- [x] 3.7 Reject `/pi <text>` and `/pic <command>` with a "task #N still running" message while a task is active; make `/pi abort` cancel the runner; show active task status in `/pi session`
- [x] 3.8 Update tool docstrings (agent-visible) to describe the async contract: the result is pushed to the chat and wakes the agent, it is *not* injected into history, so the agent must neither poll nor re-send

## 5. v1.3.0 fixes

- [x] 5.1 Finish a `disposition: "handled"` prompt immediately (`completed`, no model run) instead of waiting 1200s for a settle that never comes (D7)
- [x] 5.2 Report `agent_settled.aborted: true` as `aborted`, and make `cancel()` idempotent (D8)
- [x] 5.3 Extract the mainline final answer: take the last text-bearing authoritative `message_end` assistant message (the sub-task's `task-result` arrives as an `entry_appended` entry, not as a `custom` message), reset only on real input boundaries, keep the delta work log separately (D9)
- [x] 5.4 Auto-cancel an unanswered UI request at one deadline (`UI_REPLY_TIMEOUT`), tell the chat, and keep consuming; the connection's 300s sweep is narrowed to orphan cleanup and no longer owns a UI deadline (D5)
- [x] 5.5 Drop fire-and-forget notifications before queueing so the idle watchdog measures real progress (D10)
- [x] 5.6 Refuse `/pi open`, `/pi resume`, `pi_open_session`, and `pi_resume_session` while a task is running (D11)
- [x] 5.7 Regression tests: handled prompt, aborted settle, result extraction, UI timeout notice, noise filtering, session-switch guard
- [x] 5.8 Align `openspec/changes/pi-background-task/` (this change), `README.md`, and `skills/pi-connector/SKILL.md` with the shipped behavior
- [x] 5.9 Review follow-ups: `/pi abort` settles the local task even when `abort()`/`_get_connection()` raise (single notification, session block released); the interactive path answers a `handled` prompt instead of staying silent; docs state the real rpc result flow (`entry_appended{task-result}`, no `role: "custom"` message)

## 4. Docs and tests

- [x] 4.1 Update `skills/pi-connector/SKILL.md` with the background-task workflow and anti-polling/anti-abort guidance
- [x] 4.2 Update `README.md` (LLM tools sections, both languages) for the async behavior
- [x] 4.3 Update existing tests for changed tool/command behavior; add `tests/test_tasks.py` and notification-helper tests (mock `Context`)
- [x] 4.4 Run `uv run pytest tests/ -v` and `uv run ruff check main.py pi_connector tests/`; fix all failures
