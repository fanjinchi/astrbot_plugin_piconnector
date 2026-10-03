# Design: pi-background-task

## Context

The plugin exposes pi to the AstrBot LLM agent via `@filter.llm_tool` handlers in `main.py`. Today `pi_send_message` / `pi_run_command` call `_collect_prompt_response`, which consumes `PiConnection.send_prompt` events until `agent_end`. Three hard limits make this blocking model fail in practice:

1. AstrBot's main agent kills any local tool call after `provider_settings.tool_call_timeout` (default 120s; enforced in `astrbot/core/astr_agent_tool_exec.py`). Real pi tasks routinely take longer.
2. `PiConnection.send_prompt` waits at most 300s per event; pi goes silent for minutes while running subagents, so the plugin itself also raises `PiError("Timeout waiting for pi response events")`.
3. When the tool call is cancelled by AstrBot, the abandoned generator leaves unread events in `PiConnection._event_queue`, corrupting the next prompt's stream.

After a timeout the agent only sees an error string. It cannot observe pi's progress, so it polls `pi_get_session_info` and eventually aborts a healthy run.

Constraints:

- The plugin must only import AstrBot public APIs (`astrbot.api.*`). Triggering a full main-agent run on completion (the way `astrbot/core/cron/manager.py` does) uses internal modules and is therefore out of scope.
- `PiConnection._event_queue` is a single consumer queue: exactly one coroutine may consume events at a time, or events are split randomly between consumers.
- The `/pi <text>` direct command streams chunks to the user in real time and works well; users expect that behavior to stay.

## Goals / Non-Goals

**Goals:**

- LLM-tool prompts run as fire-and-forget background tasks; the agent's turn ends immediately with clear guidance.
- When a run finishes (completed, failed, or aborted), the chat receives the complete round result without any polling, and the agent learns the outcome through the conversation history.
- Long silent periods (subagents, long tool calls) never abort or fail a healthy run.
- A single, well-defined owner consumes the connection event stream at any time.
- The agent is actively discouraged from polling/aborting via tool descriptions, session-info hints, and a confirmation guard on `pi_abort`.

**Non-Goals:**

- Waking the main agent for a proactive LLM turn when a task completes (requires AstrBot internals; rejected).
- Multiple concurrent background tasks per chat (pi RPC itself rejects concurrent prompts).
- Changing the `/pi <text>` direct-command streaming UX.
- Streaming partial progress messages during a background run (only UI requests and the final result are pushed).

## Decisions

### D1: Async launch with immediate guidance instead of blocking collection

`pi_send_message` / `pi_run_command` forward the prompt via `PiConnection.send_command({"type": "prompt", ...})`, hand the connection to a new `PiTaskRunner`, and return a fixed acknowledgement string to the agent: task id, "do not poll / re-send / abort", "the user will receive the full result automatically". The agent then naturally ends its turn by telling the user the task started.

Alternatives considered:

- *Keep blocking but raise `tool_call_timeout`*: admin-only knob, still leaves the agent blind for very long tasks, and keeps the stale-event bug. Rejected.
- *Return after the first text delta*: mid-run return tells the agent nothing about completion and still leaves the queue ownerless. Rejected.

### D2: A per-chat `PiTaskRunner` owns the event stream

New module `pi_connector/tasks.py`:

- `PiTask` (dataclass): `task_id` (per-chat incrementing int), `status` (`running` / `awaiting_ui` / `completed` / `failed` / `aborted`), `started_at`, `prompt`, accumulated text parts, tool-call count, and an `asyncio.Event` used to resume after a UI reply.
- `PiTaskManager`: one active task per chat key (same `_session_key` scheme as `PiConnectionManager`). Launch, query, cancel, and cleanup on connection close / plugin terminate.

The runner coroutine consumes `PiConnection.read_events()` (see D4) until `agent_end`, then calls a completion callback provided by the plugin. While a runner is active it is the **only** consumer of `_event_queue`.

### D3: Completion notification = user push + history injection (public APIs only)

On task end the plugin (via callback from the manager):

1. Pushes one or more `MessageChain` text messages to the chat with `Context.send_message(event.unified_msg_origin, chain)`: a header (`✅ pi 任务完成` / `❌ 失败` / `⛔ 已中止`, task id, elapsed time, tool-call count) followed by the complete collected assistant text, split into chunks (default 1500 chars) to stay within platform limits.
2. Injects a message pair into the conversation with `Context.conversation_manager.get_curr_conversation_id(umo)` + `add_message_pair(cid, user_msg, assistant_msg)`: the "user" side is a system-style note (`[pi background task #N finished: <status>]`), the "assistant" side holds the result text (truncated to a reasonable size if huge). The next agent turn sees the outcome in context.

Alternatives considered:

- *Cron-style agent wake*: needs `astrbot.core.astr_main_agent` internals — violates the plugin's public-API rule and would double-deliver results. Rejected.
- *User push only*: the agent never learns the outcome and may later claim the task is still running. Rejected.
- *History injection only*: the user gets no immediate feedback. Rejected.

### D4: Optional no-timeout event reads on `PiConnection`

Add `event_timeout: float | None = 300` parameter to `send_prompt` and `read_response`; `None` waits indefinitely. The background runner passes `None` — a background task has no reason to give up on silence. Foreground command handlers keep the 300s default.

To keep the runner simple, add a small `read_events()` async generator on `PiConnection` that yields normalized events until `agent_end` without sending a prompt first (used after the runner sends the prompt command itself).

### D5: UI request coordination via pause/resume

When the runner sees an `extension_ui_request` (tracked by `PiConnection` as today), it:

1. Sets status to `awaiting_ui` and pushes the formatted request to the chat (with `/pi confirm <id> yes` style instructions).
2. Waits on `task.ui_resolved` (an `asyncio.Event`).
3. The `/pi confirm|select|input|edit` handlers — and the `pi_reply_ui` tool — check for an active runner first: they forward the reply to pi (`confirm_ui_request` / `reply_ui_request` / `cancel_ui_request`) and set `task.ui_resolved`, then return **without** calling `read_response`. The runner resumes consuming and pushes the eventual result as usual.
4. When no runner is active, the existing behavior (reply handler consumes `read_response`) is unchanged.

### D6: Guardrails against agent polling and spurious aborts

- Tool docstrings (visible to the agent in the schema) state the async contract explicitly.
- `pi_get_session_info` appends, when a task is running: task id, elapsed seconds, and "pi may stay silent for minutes while running tools/subagents; this is normal — do NOT poll repeatedly or abort unless the user asks".
- `pi_abort` gains a required `user_confirmed: boolean` argument; calls without `user_confirmed=true` are refused with an explanation.
- Launching a prompt while a task is active (via tool or `/pi <text>`) is rejected with "task #N still running".
- `/pi abort` cancels the runner **and** sends the RPC `abort`; `/pi quit` / `terminate()` cancel runners before closing connections.

## Risks / Trade-offs

- [Agent ignores the guidance and polls anyway] → `pi_get_session_info` explicitly reports the healthy running state and discourages aborts; `pi_abort` hard-requires `user_confirmed`.
- [Plugin/AstrBot restarts mid-run] → the pi subprocess dies with the plugin; on next use the manager spawns a fresh connection. The in-flight task is lost; acceptable because today's behavior loses it too. The completion push may fail if the platform is unreachable; it is logged and the history injection is still attempted.
- [History injection races with an ongoing agent turn] → `add_message_pair` appends to the stored conversation; the worst case is the running turn not seeing it, the next turn will. Acceptable.
- [Very large results] → user push is chunked; history injection truncates oversized text to keep conversations small.
- [Single-consumer invariant broken by future code] → `PiTaskManager.is_running(key)` is the single guard; all event-consuming entry points check it first.

## Migration Plan

Pure behavior change, no data migration. Rollback = revert the plugin; no persistent state is introduced (tasks live in memory).

## Open Questions

None.
