# Design: pi-background-task

## Context

The plugin exposes pi to the AstrBot LLM agent via `@filter.llm_tool` handlers in `main.py`. Today `pi_send_message` / `pi_run_command` call `_collect_prompt_response`, which consumes `PiConnection.send_prompt` events until `agent_settled`. Three hard limits make this blocking model fail in practice:

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
- When a run finishes (completed, failed, or aborted), the chat receives the mainline final answer without any polling, and the agent is woken with the same text plus an explicit "do not repeat it" instruction.
- Long silent periods (subagents, long tool calls) never abort or fail a healthy run.
- A single, well-defined owner consumes the connection event stream at any time.
- The agent is actively discouraged from polling/aborting via tool descriptions, session-info hints, and a confirmation guard on `pi_abort`.

**Non-Goals:**

- Waking the main agent for a proactive LLM turn through `astrbot.core.*` internals (what `astrbot/core/cron/manager.py` does) — rejected; the wake event below achieves the same effect through the public event queue.
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

The runner coroutine consumes `PiConnection.read_events()` (see D4) until `agent_settled`, then calls a completion callback provided by the plugin. While a runner is active it is the **only** consumer of `_event_queue`.

`PiTask` keeps two texts apart (see D9): `final_answer` (what the user gets) and `text_parts` (the raw work log, used in logs/wake context only).

### D3: Completion notification = chat push + wake event, same text (decision C)

On task end the plugin (via callback from the manager):

1. Pushes one or more `MessageChain` text messages to the chat with `Context.send_message(event.unified_msg_origin, chain)`: a header (`✅ pi 任务完成` / `❌ 失败` / `⛔ 已中止`, task id, elapsed time, tool-call count) followed by the **mainline final answer**, split into chunks (default 1500 chars) to stay within platform limits. The sub-task work log never reaches the chat — it would read like a second, contradictory answer.
2. Emits a wake event through the public queue: `AstrBotMessage` with `is_wake=True` built from the task's `unified_msg_origin`, carrying exactly the same text as a `Plain` note prefixed with `[pi background task #N finished: <status>]` and suffixed with the instruction *该结果已直接推送给用户，不要复述全文；只有需要补充解释或执行后续动作时才回复用户。*

Two channels with one text is the whole point of the design: the user reads the answer in chat, the agent sees it with a no-repetition directive, and neither has to reconstruct the other's view.

Alternatives considered:

- *Cron-style agent wake through `astrbot.core.astr_main_agent` internals*: violates the plugin's public-API rule. Rejected; the wake event is the public equivalent.
- *Chat push + `conversation_manager.add_message_pair` history injection*: shipped in v1.2.0, removed in v1.3.0. The injected pair only becomes visible on the *next* user turn — after a background task finishes the agent usually stays silent, so the record was often never read, and the pair duplicated text the chat already had.
- *Wake event only, no chat push*: the user sees nothing. Rejected.
- *Wake note without the no-repeat instruction*: verified in practice that the agent re-summarised the answer the user had just read (and occasionally re-sent it through `pi_send_message`). Rejected.

### D4: Optional no-timeout event reads on `PiConnection`

Add `event_timeout: float | None = 300` parameter to `send_prompt` and `read_response`; `None` waits indefinitely. The background runner passes `None` — a background task has no reason to give up on silence. Foreground command handlers keep the 300s default.

To keep the runner simple, add a small `read_events()` async generator on `PiConnection` that yields normalized events until `agent_settled` without sending a prompt first (used after the runner sends the prompt command itself).

### D5: UI request coordination via pause/resume, with a single deadline

When the runner sees an `extension_ui_request` (tracked by `PiConnection` as today), it:

1. Sets status to `awaiting_ui` and pushes the formatted request to the chat (with `/pi confirm <id> yes` style instructions).
2. Waits on `task.ui_resolved` (an `asyncio.Event`) for at most `UI_REPLY_TIMEOUT` (30s, or pi's own deadline if that is shorter).
3. The `/pi confirm|select|input|edit` handlers — and the `pi_reply_ui` tool — check for an active runner first: they forward the reply to pi (`confirm_ui_request` / `reply_ui_request` / `cancel_ui_request`) and set `task.ui_resolved`, then return **without** calling `read_response`. The runner resumes consuming and pushes the eventual result as usual.
4. If nobody answers within the deadline, the runner **cancels the request on the pi side** (`cancel_ui_request`), clears the registry entry, tells the chat *该确认请求已取消*, and keeps consuming the stream — pi then continues on its own default. Previously the plugin returned to consuming while pi stayed blocked forever, so the task hung until the 1200s watchdog.
5. When no runner is active, the existing behavior (reply handler consumes `read_response`) is unchanged.

There used to be three unrelated deadlines (30s waiter in `tasks.py`, a 300s registry sweep in `connection.py`, pi's own wait). They are now one: `tasks.py` owns the deadline; `connection.py` only keeps a stale-entry sweep as a safety net for requests no task owns.

### D6: Guardrails against agent polling and spurious aborts

- Tool docstrings (visible to the agent in the schema) state the async contract explicitly.
- `pi_get_session_info` appends, when a task is running: task id, elapsed seconds, and "pi may stay silent for minutes while running tools/subagents; this is normal — do NOT poll repeatedly or abort unless the user asks".
- `pi_abort` gains a required `user_confirmed: boolean` argument; calls without `user_confirmed=true` are refused with an explanation.
- Launching a prompt while a task is active (via tool or `/pi <text>`) is rejected with "task #N still running".
- `/pi abort` cancels the runner **and** sends the RPC `abort`; `/pi quit` / `terminate()` cancel runners before closing connections.

### D7: `disposition: "handled"` ends the task immediately (no model run)

pi acknowledges a `prompt` RPC with `{success: true, data: {disposition}}`, where `disposition` is `"handled"` (an extension command consumed the prompt — **no agent run starts, so no `agent_settled` ever follows**, `docs/rpc.md:67`), `"queued"`, or `"started"`. The runner therefore finishes a `handled` prompt itself: status `completed`, empty text, `no_model_run=True`, header note *命令已处理，没有启动模型回合*.

This is the real root cause of commit `29dda10` ("只等 agent_settled 让每个任务卡死" → rollback): the watchdog fix made the runner wait for `agent_settled` unconditionally, and prompts that pi had *handled* (slash commands forwarded through the LLM tool, e.g. `/compact`) then waited the full 1200s. The fix belongs in the `handled` path, not in the terminal-event rule — do not roll the terminal event back to `agent_end`.

Because a dialog can block the prompt acknowledgment longer than the 10s ack timeout, `launch` stops waiting for the ack when that happens and lets the runner pick the outcome up: the late `response` is turned into a `prompt_handled` / `prompt_rejected` pseudo-event on the queue.

### D8: `agent_settled.aborted` reports `aborted`, not `completed`

`agent_settled` carries `aborted` (`docs/json.md`). The runner records it in `PiTask.aborted`; a settle with `aborted: true` ends the task as `aborted` (⛔), never as `✅ 完成` with half a message. `PiTaskManager.cancel()` bails out when the task is already in a terminal state, so a user abort and a settle for the same run cannot produce two notifications.

### D9: The result is the last text-bearing assistant message of the run

The old code joined every `text_delta` of the round into one string, so a task's answer was the *entire sub-task narration* (tool commentary, intermediate plans) — and in flat mode the mainline answer is what actually matters. The rule is now:

- The `message_end` of an assistant message whose content has text sets `final_answer`; the **last** non-empty one wins. A tool-call-only turn (`content: []`) must not erase it. `message_end.message` is authoritative (`docs/json.md`: partial `message_update` texts are superseded by it), so `text_parts` (deltas) stay only as a fallback and work log.
- Why that is the mainline answer: in RPC/flat mode a finished sub-task's `task-result` does **not** arrive as a message. The tree extension appends it through `_commitBoundaryDrafts` → `_applyBoundaryDrafts` (`dist/core/agent-session.js:636-641`, `:579-604`), which emits only `entry_appended{entry: {type: "custom_message", customType: "task-result"}}`; `role: "custom"` `message_start`/`message_end` pairs come exclusively from `sendCustomMessage` (`:1815-1861`). A pi 1.3.0 probe of a push-task round saw exactly `message_start(user) → assistant 子任务文本 → agent_end → entry_appended{task-result} → assistant 主线文本 → agent_end → agent_settled`, with `boundaries seen: ['user']`. RPC stdout is one ordered stream, so the sub-task text precedes the delivery and the mainline text follows it: the last text-bearing `message_end` is the mainline answer, and the earlier assistant text naturally stays behind as work log.
- A `message_start` whose `message.role` is `user` still resets `final_answer` (`INPUT_BOUNDARY_ROLES = frozenset({"user"})`, `pi_connector/tasks.py:59`): the text spoken before the newest scope belongs to an older turn. That reset is **defence in depth** here — "pi picks up a follow-up prompt queued during the same run" is not reachable through this plugin (every prompt/command path is refused by `get_active` while a task runs), so no reachable path opens a second scope mid-run; a regression test pins the reset anyway.
- `custom` is deliberately **not** a boundary. pi's `sendCustomMessage` emits `message_start/end` with `role: "custom"` (`dist/core/agent-session.js:1815-1861`), and such a message can arrive with `triggerTurn: false` — pi-plan-mode sends exactly that from its `agent_end` handler (`pi-plan-mode/src/plan-mode.ts:681`, scheduled through `scheduleAfterCurrentAgentRun` at `:805-812` with `setTimeout(0)`). Treating it as a boundary would clear the mainline answer with no later assistant text to restore it, and `PiTask.result_text` would fall back to the whole work log (the sub-task narration this change exists to hide). This plugin's own `task-result` does not travel through that path at all (`entry_appended`).
- `PiTask.result_text` returns `final_answer` or, when empty, the joined deltas; `PiTask.work_log` exposes the deltas for logs.

### D10: Watchdog noise filtering

`setTitle` / `setStatus` / `setWidget` / `set_editor_text` / `notify` are fire-and-forget notifications (`docs/rpc-extension-ui.md`), emitted roughly every 80ms during streaming. Treating them as progress reset the 1200s idle watchdog forever, so a wedged run was never detected. `PiConnection._handle_event` now drops them before queueing: no consumer ever needed them (the UI formatters deal with dialogs only), and the watchdog then measures real progress.

### D11: Session switching is refused while a task runs

`/pi open`, `/pi resume`, and the `pi_open_session` / `pi_resume_session` tools replace the connection and its event queue. Doing that mid-task would silently strand the runner. All four now refuse with a message naming the running task and pointing at `/pi abort`.

### D12: `agent_settled` is not proof of success — interruption is classified, not assumed away

`agent_settled` is emitted from `_emitAgentSettled` inside pi's `finally` block (`dist/core/agent-session.js:1435-1442`), so **every** run ends with one, and its `aborted` flag only mirrors an explicit abort request. A provider/network failure whose retries were exhausted, or a run cut by the output-token ceiling, therefore arrives as `agent_settled{aborted: false}` — indistinguishable from a clean finish if the terminal event is the only evidence. Before this decision the runner read that as ✅ `completed` and pushed whatever half sentence the assistant had managed to emit.

So the runner reads the interruption signals that travel in the same stream (rpc forwards *all* session events, `dist/modes/rpc/rpc-mode.js:265-269`):

- `auto_retry_start{attempt, maxAttempts, errorMessage}` / `auto_retry_end{success, attempt, finalError}` — pi's own retry chain. `success: false` means the chain gave up (`agent-session.js:1523-1527`), and `finalError` is the last assistant's `errorMessage`.
- `message_end` of an assistant message: `stopReason` ∈ `"pending" | "stop" | "length" | "toolUse" | "error" | "aborted" | "deferred"` (`pi-ai` `dist/types.d.ts:301`), plus `errorMessage`.

Classification (`_record_assistant_turn`, `pi_connector/tasks.py`):

- `error` / `aborted` + text → the text is a **prefix**, kept in `PiTask.partial_answer` and never in `final_answer`; `interrupt_phase` snapshots where it happened. Status becomes `failed`, `result_text` returns the partial text so the chat still shows something, and the notification marks it as such.
- `length` + text → the text is complete as far as it goes, so it *is* the answer; `truncated = True` adds a flag line and the status stays `completed`. Rationale: a token-ceiling stop usually lands after the work is done, and calling a delivered result "failed" would be the worse lie.
- `stop` / `toolUse` → unchanged, last text-bearing message wins (D9).

The classification is **positional**, because pi does not end a run on an interrupted turn: in flat/rpc mode (pi commit `365c1ae`, `src/index.ts:1633` `interruptionReport`) a cut sub-task epoch appends an interruption report as a `task-result` `custom_message` and lets the mainline answer again in the *same* run, so one `agent_settled` can close several tasks. A text-bearing assistant turn *after* the cut turn therefore means the run recovered. `PiTask.interrupt_unresolved` — not `stop_reason`, which every later clean turn overwrites, and not `retry_final_error`, which used to stay stuck forever after `auto_retry_end{success:false}` — is the single source of truth for `PiTask.interrupted`:

- interruption is the newest event (no text-bearing turn after it) → `interrupt_unresolved = True`, status `failed`, `result_text = partial_answer`, the ❌ phase lines above;
- a text-bearing turn followed it → `interrupt_unresolved = False` and `interruption_recovered = True`, status `completed`, `result_text` = that turn's text (the mainline answer reaches both the chat and the wake note), and `interrupt_phase` / `interrupt_reason` keep the interruption as a ⚠️ warning line (`main.py`, 子任务执行中断（…），主线已说明) instead of a ❌ failure;
- a text-less turn (`stopReason: "toolUse"`) proves nothing: it neither replaces the answer nor clears the interruption.

`auto_retry_end{success: true}` — pi's own retry worked — clears the whole interruption state (phase, reason, partial text, `retry_final_error`), because that cut turn was a failed attempt, not the run's outcome.

Note the classification stays event-driven: the interruption report's *text* never reaches `text_parts` (`entry_appended` is bookkeeping, not a message), so nothing here parses it.

### D13: The interruption phase is inferred from the task bookkeeping entries

pi has no explicit phase field, so the runner infers it from `entry_appended` (rpc forwards every entry; the task system's own entries travel as `entry.type == "custom"` with `customType` `task-start` / `task-done` and as `entry.type == "custom_message"` with `customType` `task-result` / `task-start-delivery` / `task-resume-delivery`; the two entry types are matched separately because they share the field name but not the shape).

`PHASE_BY_ENTRY` maps `task-start`/`task-start-delivery`/`task-resume-delivery` → `task` and `task-done`/`task-result` → `summary`; no entry seen yet means `mainline`. The three states are what a user needs to distinguish: *the work never got done*, *the work got done but the report never came*, and *the answer itself was cut*. A single "interrupted" line would collapse them into the least useful of the three, so the notification branches on `interrupt_phase` (`main.py`, next to the `abort_uncertain` caveat). Note that the phase is a *hint*, not a fence: a `deferred`/`error` stop can arrive after the last entry of a phase, which is exactly why `interrupt_phase` is a snapshot and `phase` keeps advancing independently.

## Risks / Trade-offs

- [Agent ignores the guidance and polls anyway] → `pi_get_session_info` explicitly reports the healthy running state and discourages aborts; `pi_abort` hard-requires `user_confirmed`.
- [Plugin/AstrBot restarts mid-run] → the pi subprocess dies with the plugin; on next use the manager spawns a fresh connection. The in-flight task is lost; acceptable because today's behavior loses it too. The completion push may fail if the platform is unreachable; it is logged and the wake event is still attempted.
- [The agent repeats the answer the user just received] → the wake note ends with an explicit "already pushed, do not repeat" instruction, and the tool descriptions no longer promise history injection (which used to make the agent re-read/re-post results).
- [A prompt is handled by pi but reported as running] → the `disposition == "handled"` path (D7) ends the task immediately; regression test pins it.
- [Very large results] → the user push is chunked; the wake note carries the same text, so a platform that rejects a chunk also truncates only that chunk.
- [Single-consumer invariant broken by future code] → `PiTaskManager.is_running(key)` is the single guard; all event-consuming entry points check it first.

## Migration Plan

Pure behavior change, no data migration. Rollback = revert the plugin; no persistent state is introduced (tasks live in memory).

## Open Questions

No open design question blocks this change. (Extension commands answered with `disposition: "handled"` have no model turn, so there is nothing to report beyond the header note — see D7.)

### Known limits (recorded, not fixed)

- **B1 — mainline run without text after a sub-task.** If the mainline's turn after `task-result` contains only tool calls and no assistant text, `final_answer` keeps the sub-task's last text and the chat receives it as the mainline answer. Probably rare: the task system ends a pushed task with a text report. Unfixed. Note the overlap with D12: a mainline that is *cut* (`stopReason: "error"`) is now reported as an interruption and no longer falls through to this path; B1 is the silent residue — a clean `stop` with nothing to say.
- **B2 — late `response` reassigned to the next task.** `launch` gives up on the prompt ack after `PROMPT_ACK_TIMEOUT` (10s) and lets the runner pick the late `response` up as a `_response_sentinel` pseudo-event (`pi_connector/connection.py:124-142`, `:376-383`). A response that lands between two tasks can be read as the next task's own and surface a previous command's error. Very low probability; the fix is to carry the `id` in the sentinel and match it against the `cmd_id` the task's `launch` sent. Unfixed.
- **B2b — abort settled locally while pi is still running the turn.** Same window as B2, and the price of `_abort_run`'s "settle the local task no matter what" (`main.py:205-241`): `conn.abort()` (`pi_connector/connection.py:676-678`) waits 10s for the ack, and on `PiError` (timeout, broken pipe) the plugin settles the task and tells the user "cancelled locally" while pi never confirmed — that turn may still be executing. A task created right afterwards starts without draining the old run (`launch` does not drain), so the old turn's trailing assistant text plus its `agent_settled` are read by the new runner as its own result. The runner's `finally` drain (`pi_connector/tasks.py:495`) does not cover it: `drain_events` (`pi_connector/connection.py:705-713`) only empties what is already queued and breaks on `QueueEmpty`, so an event that arrives later lands in the new task's queue. Very low probability (needs a simultaneous ack timeout plus an immediate re-launch); the notification now carries the caveat "pi 侧可能仍在执行该回合" (`PiTask.abort_uncertain`, set in `_abort_run`, read by `_notify_task_finished`), but the result contamination itself is unfixed — the fix is to drain (or fence) the queue before `launch` starts a new epoch.
- **B6 — `handled` read as "nothing will follow".** `docs/rpc-commands.md:40` says `data.disposition` "does not describe the independent work started by an extension", while the plugin's `no_model_run` report and its skipped wake-up rest on `handled ⇒ no follow-up work`. The `handled` answers available in this deployment are immediately-finishing extension commands, so it has not been hit; a `handled` prompt that started independent extension work would be reported as complete with no text. Unfixed.
- **B7 — "主线已说明" is not proof the work finished.** When a sub-task is cut and the mainline answers afterwards, D12's positional rule delivers the mainline's answer and keeps the interruption as a warning. The plugin cannot judge whether that answer means "the missing step was redone" or "the missing step was skipped": it only knows that the run ended with an answer, so the header stays ✅ and the warning line carries the caveat. A stricter report (status `completed` + a `partial work` flag) would need a signal pi does not send. Unfixed; the notification wording ("主线已说明") is deliberately about who said what, not about the work being complete.
