# Proposal: pi-background-task

## Why

When the AstrBot LLM agent drives pi through the `pi_send_message` / `pi_run_command` tools, the tool blocks until pi finishes. AstrBot kills any tool call after `tool_call_timeout` (default 120s), so almost every real pi task ends in a timeout error. The agent then cannot tell that pi is still running in the background: it polls `pi_get_session_info` repeatedly, and when pi goes quiet for minutes while running tools or subagents, the agent mistakes the silence for a hang and calls `pi_abort`, destroying healthy tasks. The plugin's own 300s per-event wait in `send_prompt` has the same failure mode, and a cancelled tool call also leaves stale events in the connection queue that pollute the next prompt.

## What Changes

- `pi_send_message` and `pi_run_command` become **asynchronous launchers**: they forward the prompt to pi, start a background task that owns the event stream, and return immediately with explicit guidance (do not poll, do not re-send, do not abort; completion is reported automatically).
- A new per-chat **background task runner** consumes pi events to `agent_settled`, counts tool executions, tolerates long silent periods (no per-event cap while a background task owns the stream), and separates the **mainline final answer** (the last text-bearing assistant `message_end` of the run — a sub-task's result arrives as an `entry_appended{task-result}` entry, so ordering, not a message boundary, separates them) from the **sub-task work log** (every text delta).
- On completion, failure, or abort, the runner **proactively notifies the chat**: it pushes the mainline final answer to the user via `Context.send_message` (chunked to the platform limit), and also emits a **wake event** through the public event queue (`is_wake=True`) whose note carries the *same* text plus an explicit "already pushed, do not repeat it" instruction.
- When pi emits an interactive UI request during a background run, the runner pushes the request to the chat and waits up to a single aligned deadline; a reply resumes the run, and an unanswered request is **cancelled at the deadline** (pi side released, chat told "该确认请求已取消") so the event stream keeps flowing.
- Guardrails: `pi_get_session_info` reports the running background task with elapsed time and a "silence is normal, do not poll/abort" hint; `pi_abort` requires an explicit `user_confirmed` flag; launching a second prompt while a task is running is rejected with a clear message.
- `/pi abort`, `/pi quit`, and plugin `terminate()` cancel the active background task; `/pi session` shows the active task status.
- Update the agent-facing skill (`skills/pi-connector/SKILL.md`) to document the async model.

## Capabilities

### New Capabilities

- `pi-background-task`: per-chat background execution of pi prompts, lifecycle management (launch, pause/resume for UI requests, completion, abort, cleanup), result extraction, chat notification, and wake-event delivery to the agent.

### Modified Capabilities

- `pi-llm-tools`: `pi_send_message` and `pi_run_command` change from synchronous result collection to asynchronous task launch; `pi_abort` gains a mandatory user-confirmation parameter; `pi_get_session_info` reports active background task status.
- `pi-chat-interface`: `/pi abort` and `/pi quit` cancel the active background task; UI reply commands (`/pi confirm|select|input|edit|cancel`) coordinate with an active background task instead of consuming events themselves.
- `pi-session-management`: `/pi session` (and `/pi info`) report the active background task status alongside session information.

## Impact

- **Code**: new `pi_connector/tasks.py`; changes to `pi_connector/connection.py` (optional no-timeout event wait), `pi_connector/manager.py` (per-chat task registry hooks), and `main.py` (tool and command handlers, notification helpers).
- **Docs**: `skills/pi-connector/SKILL.md`, `README.md`.
- **Tests**: new background-task tests plus updates to existing tool/command tests.
- **Behavior**: `pi_send_message` / `pi_run_command` no longer return pi's final text to the calling agent; they return a "task started" acknowledgement, and the outcome arrives as a chat push plus a wake event that carries the same text. `/pi <text>` direct command behavior is unchanged (still streams to the user in real time).
- **Dependencies**: none (uses existing AstrBot public APIs only: `Context.send_message`, `Context.get_event_queue` with `AstrBotMessage.is_wake`).
