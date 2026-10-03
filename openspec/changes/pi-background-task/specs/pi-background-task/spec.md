## ADDED Requirements

### Requirement: Execute pi prompts as per-chat background tasks
The system SHALL run prompts launched through the LLM tools as background tasks owned by a per-chat task runner. At most one background task SHALL be active per chat context at any time.

#### Scenario: Launch a background task
- **WHEN** a prompt is forwarded to pi through an LLM tool and no background task is active for the chat
- **THEN** the plugin creates a background task with a per-chat incrementing identifier
- **THEN** a runner coroutine consumes the connection event stream until `agent_end`
- **THEN** the tool call returns immediately without waiting for pi to finish

#### Scenario: Reject a second launch while a task is running
- **WHEN** a prompt is launched through an LLM tool or `/pi <text>` while a background task is active for the chat
- **THEN** the plugin rejects the launch and reports the identifier and elapsed time of the running task
- **THEN** the running task is not interrupted

### Requirement: Tolerate long silent periods during background runs
While a background task owns the event stream, the system SHALL NOT fail or abort the task because pi emits no events, regardless of how long the silence lasts.

#### Scenario: Subagent silence does not fail the task
- **WHEN** pi emits no events for more than 5 minutes while a background task is running (for example while a subagent or long tool call executes)
- **THEN** the task remains in the running state and continues consuming events until `agent_end`, an error, or an explicit abort

### Requirement: Report background task status
The system SHALL expose the active background task's identifier, status, and elapsed time to status queries.

#### Scenario: Query while a task is running
- **WHEN** session status is queried while a background task is running
- **THEN** the response includes the task identifier and elapsed time
- **THEN** the response states that silence lasting minutes is normal and advises against polling or aborting

#### Scenario: Query while idle
- **WHEN** session status is queried and no background task is active
- **THEN** the response contains no background task status

### Requirement: Notify the chat with the complete result on task end
When a background task ends, the system SHALL proactively push the outcome to the originating chat without any polling, using `Context.send_message` with the chat's unified message origin.

#### Scenario: Task completes successfully
- **WHEN** a background task reaches `agent_end`
- **THEN** the plugin pushes a completion header with the task identifier and elapsed time, followed by the complete assistant output of the round
- **THEN** long output is split into multiple message chunks

#### Scenario: Task fails
- **WHEN** a background task ends because of an error (RPC failure, process exit, unexpected exception)
- **THEN** the plugin pushes a failure notice containing the task identifier, the error, and any output collected before the failure

#### Scenario: Task aborted
- **WHEN** a background task ends because the user or the agent aborted it
- **THEN** the plugin pushes an abort notice containing the task identifier and any output collected before the abort

### Requirement: Record the task outcome in the agent's conversation history
When a background task ends, the system SHALL inject a record of the outcome into the chat's LLM conversation history via `Context.conversation_manager`, so the agent learns the result on a subsequent turn.

#### Scenario: Inject completion record
- **WHEN** a background task ends and the chat has a current conversation
- **THEN** the plugin appends a message pair describing the task outcome (status and task identifier) and the result text to the conversation history
- **THEN** oversized result text is truncated in the injected record

#### Scenario: No current conversation
- **WHEN** a background task ends and the chat has no current conversation
- **THEN** the plugin skips history injection and still delivers the chat notification

### Requirement: Coordinate UI requests during background runs
When pi emits an interactive UI request while a background task is active, the system SHALL pause the task, present the request in the chat, and resume the task after the user replies.

#### Scenario: UI request arrives during a background run
- **WHEN** pi emits a `confirm`, `select`, `input`, or `editor` UI request while a background task is running
- **THEN** the task enters an awaiting-reply state and stops consuming events
- **THEN** the plugin pushes the formatted request with reply instructions to the chat

#### Scenario: User replies to the request
- **WHEN** the user replies with a `/pi confirm`, `/pi select`, `/pi input`, or `/pi edit` command while a background task is awaiting a reply
- **THEN** the plugin forwards the reply to pi and signals the task to resume
- **THEN** the reply command does not consume the event stream itself
- **THEN** the task continues consuming until `agent_end` and delivers the final result as usual

### Requirement: Cancel background tasks on abort, disconnect, or plugin unload
The system SHALL cancel the active background task when the user aborts the pi operation, disconnects the session, or when the plugin is unloaded.

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
