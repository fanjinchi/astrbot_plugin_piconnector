## ADDED Requirements

### Requirement: Execute pi prompts as per-chat background tasks
The system SHALL run prompts launched through the LLM tools as background tasks owned by a per-chat task runner. At most one background task SHALL be active per chat context at any time.

#### Scenario: Launch a background task
- **WHEN** a prompt is forwarded to pi through an LLM tool and no background task is active for the chat
- **THEN** the plugin creates a background task with a per-chat incrementing identifier
- **THEN** a runner coroutine consumes the connection event stream until `agent_settled`
- **THEN** the tool call returns immediately without waiting for pi to finish

#### Scenario: Reject a second launch while a task is running
- **WHEN** a prompt is launched through an LLM tool or `/pi <text>` while a background task is active for the chat
- **THEN** the plugin rejects the launch and reports the identifier and elapsed time of the running task
- **THEN** the running task is not interrupted

#### Scenario: Prompt handled without a model run
- **WHEN** pi acknowledges a prompt with `data.disposition == "handled"` (an extension command consumed it, so no `agent_settled` will follow)
- **THEN** the task finishes as completed within the same round, with no result text
- **THEN** the chat notice states that the command was handled and no model turn ran
- **THEN** no wake event is emitted, because there is no answer to report

#### Scenario: Abort reported by pi
- **WHEN** `agent_settled` arrives with `aborted: true`
- **THEN** the task ends with status `aborted` and the abort notice, never as completed with partial text

### Requirement: Tolerate long silent periods during background runs
While a background task owns the event stream, the system SHALL NOT fail or abort the task because pi emits no events, regardless of how long the silence lasts — except when the local idle watchdog (`idle_timeout`, default 1200s) sees no progress at all.

#### Scenario: Subagent silence does not fail the task
- **WHEN** pi emits no events for more than 5 minutes while a background task is running (for example while a subagent or long tool call executes)
- **THEN** the task remains in the running state and continues consuming events until `agent_settled`, an error, or an explicit abort

#### Scenario: Fire-and-forget notifications do not count as progress
- **WHEN** pi emits `setTitle`, `setStatus`, `setWidget`, `set_editor_text`, or `notify` notifications during a streaming run
- **THEN** these are dropped before queueing and do not reset the idle watchdog
- **THEN** the watchdog measures actual model/tool progress only

### Requirement: Report background task status
The system SHALL expose the active background task's identifier, status, and elapsed time to status queries.

#### Scenario: Query while a task is running
- **WHEN** session status is queried while a background task is running
- **THEN** the response includes the task identifier and elapsed time
- **THEN** the response states that silence lasting minutes is normal and advises against polling or aborting

#### Scenario: Query while idle
- **WHEN** session status is queried and no background task is active
- **THEN** the response contains no background task status

### Requirement: Extract the mainline final answer
When a background task ends, the system SHALL report the mainline final answer — the last assistant `message_end` that carries text (after the newest `user`/`custom` input boundary, when the run contains one) — rather than concatenating every assistant delta of the round.

#### Scenario: Sub-task process is not the answer
- **WHEN** a task's event stream contains a sub-task's assistant text, then that sub-task's result as `entry_appended{entry: {customType: "task-result"}}` (the delivery emits no `role: "custom"` message), then the mainline's assistant text
- **THEN** the earlier assistant text is kept only as the work log, because RPC is a single ordered stream and the mainline text arrives last
- **THEN** the answer reported to the user is the last text-bearing assistant message
- **THEN** a following tool-call-only assistant turn (`content: []`) does not erase that answer

#### Scenario: Authoritative message end wins
- **WHEN** `message_update` deltas and a `message_end` describe the same assistant message
- **THEN** the text is taken from `message_end.message`
- **THEN** accumulated deltas are used only when no `message_end` produced text

### Requirement: Deliver the same result text to chat and agent
When a background task ends, the system SHALL push the mainline final answer to the originating chat with `Context.send_message`, and SHALL also wake the main agent with the identical text through the public event queue, so neither channel has to reconstruct the other's view.

#### Scenario: Task completes successfully
- **WHEN** a background task reaches `agent_settled` without `aborted`
- **THEN** the plugin pushes a completion header with the task identifier, elapsed time, and tool-call count, followed by the mainline final answer
- **THEN** long output is split into multiple message chunks
- **THEN** the sub-task work log is not sent to the chat

#### Scenario: Wake event carries the same text
- **WHEN** a background task ends with a result
- **THEN** the plugin enqueues an `AstrBotMessage` with `is_wake=True` for the chat's unified message origin
- **THEN** the note starts with `[pi background task #N finished: <status>]`, contains the same final answer, and ends with the instruction that the result was already pushed to the user and must not be repeated

#### Scenario: Task fails
- **WHEN** a background task ends because of an error (RPC failure, process exit, unexpected exception, idle watchdog)
- **THEN** the plugin pushes a failure notice containing the task identifier, the error, and any output collected before the failure

#### Scenario: Task aborted
- **WHEN** a background task ends because the user or the agent aborted it, or pi reported `aborted: true`
- **THEN** the plugin pushes an abort notice containing the task identifier and any output collected before the abort

### Requirement: Coordinate UI requests during background runs
When pi emits an interactive UI request while a background task is active, the system SHALL present the request in the chat, wait for a reply for a single bounded deadline, and resume the task after the user replies.

#### Scenario: UI request arrives during a background run
- **WHEN** pi emits a `confirm`, `select`, `input`, or `editor` UI request while a background task is running
- **THEN** the task enters an awaiting-reply state and stops consuming events
- **THEN** the plugin pushes the formatted request with reply instructions to the chat

#### Scenario: User replies to the request
- **WHEN** the user replies with a `/pi confirm`, `/pi select`, `/pi input`, or `/pi edit` command while a background task is awaiting a reply
- **THEN** the plugin forwards the reply to pi and signals the task to resume
- **THEN** the reply command does not consume the event stream itself
- **THEN** the task continues consuming until `agent_settled` and delivers the final result as usual

#### Scenario: Nobody answers
- **WHEN** no reply arrives within `UI_REPLY_TIMEOUT` (30s, or pi's own shorter deadline)
- **THEN** the plugin cancels the request on the pi side, clears it from the request registry, and tells the chat that the confirmation request was cancelled
- **THEN** the task keeps consuming events instead of hanging until the idle watchdog

### Requirement: Cancel background tasks on abort, disconnect, or plugin unload
The system SHALL cancel the active background task when the user aborts the pi operation, disconnects the session, or when the plugin is unloaded, and SHALL ignore repeated cancels once a task reached a terminal state.

#### Scenario: User aborts
- **WHEN** the user sends `/pi abort` or the agent aborts with user confirmation while a background task is running
- **THEN** the plugin sends the `abort` RPC command to pi and terminates the task runner

#### Scenario: User disconnects or plugin unloads
- **WHEN** `/pi quit` is used or the plugin's `terminate()` runs while a background task is active
- **THEN** the plugin cancels the task runner before closing the connection

### Requirement: Enforce single ownership of the connection event stream
At any time, exactly one consumer SHALL read from a connection's event queue: the background task runner while a task is active, or the foreground command handler otherwise.

#### Scenario: Foreground commands during an active task
- **WHEN** a foreground command that would consume events (`/pi <text>`, `/pic <command>`) is used while a background task is active
- **THEN** the plugin rejects the command and reports the running task instead of consuming events

#### Scenario: Session switching during an active task
- **WHEN** `/pi open`, `/pi resume`, `pi_open_session`, or `pi_resume_session` would replace the connection while a background task is active
- **THEN** the plugin refuses and tells the user to wait for completion or abort the task first
- **THEN** the running task keeps its connection and event queue
