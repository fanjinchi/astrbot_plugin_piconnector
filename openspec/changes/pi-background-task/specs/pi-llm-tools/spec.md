## MODIFIED Requirements

### Requirement: Expose tools for natural language interaction
The system SHALL register tools that allow the AstrBot LLM agent to send messages to pi and query session state. Message sending is asynchronous: the tool launches a background task and returns immediately.

#### Scenario: Agent sends a message
- **WHEN** the AstrBot LLM agent calls `pi_send_message` with a message and no background task is active
- **THEN** the plugin forwards the message to pi via the `prompt` RPC command
- **THEN** the plugin starts a background task and returns immediately with the task identifier
- **THEN** the returned text instructs the agent not to poll, not to re-send the message, and not to abort, because the complete result will be pushed to the chat automatically

#### Scenario: Agent sends a message while a task is running
- **WHEN** the AstrBot LLM agent calls `pi_send_message` while a background task is active
- **THEN** the plugin rejects the call and reports the running task's identifier and elapsed time

#### Scenario: Agent queries session info while a task is running
- **WHEN** the AstrBot LLM agent calls `pi_get_session_info` while a background task is active
- **THEN** the plugin returns the current session file, identifier, name, message count, and working directory
- **THEN** the response includes the running task's identifier and elapsed time, states that silence lasting minutes is normal, and advises against polling or aborting

#### Scenario: Agent queries session info while idle
- **WHEN** the AstrBot LLM agent calls `pi_get_session_info` and no background task is active
- **THEN** the plugin returns the current session file, identifier, name, message count, and working directory

### Requirement: Expose tools for slash commands and abort
The system SHALL register tools that allow the AstrBot LLM agent to execute pi slash commands and abort operations. Slash command execution is asynchronous; aborting requires explicit user confirmation.

#### Scenario: Agent executes a slash command
- **WHEN** the AstrBot LLM agent calls `pi_run_command` with a command name and no background task is active
- **THEN** the plugin forwards the command to the current pi session via the `prompt` RPC command
- **THEN** the plugin starts a background task and returns immediately with the task identifier and the same guidance as `pi_send_message`

#### Scenario: Agent lists available slash commands
- **WHEN** the AstrBot LLM agent calls `pi_get_available_commands`
- **THEN** the plugin returns the list of pi slash commands available in the current session

#### Scenario: Agent aborts with user confirmation
- **WHEN** the AstrBot LLM agent calls `pi_abort` with `user_confirmed` set to true
- **THEN** the plugin sends the `abort` command to pi, cancels the active background task if any, and returns the abort status

#### Scenario: Agent aborts without user confirmation
- **WHEN** the AstrBot LLM agent calls `pi_abort` without `user_confirmed` set to true
- **THEN** the plugin refuses to abort and explains that aborting requires the user to explicitly ask for cancellation

### Requirement: Expose tool to respond to pi UI requests
The system SHALL register a tool that allows the AstrBot LLM agent to respond to pi extension UI requests.

#### Scenario: Agent replies to a UI request while a background task awaits the reply
- **WHEN** the AstrBot LLM agent calls `pi_reply_ui` with a local request ID and value while a background task is awaiting that reply
- **THEN** the plugin forwards the response to pi via `extension_ui_response` and signals the background task to resume
- **THEN** the tool returns whether the response was accepted without consuming the event stream
- **THEN** the run's final result is delivered via the background task notification

#### Scenario: Agent replies to a UI request with no background task
- **WHEN** the AstrBot LLM agent calls `pi_reply_ui` with a local request ID and value and no background task is active
- **THEN** the plugin forwards the response to pi via `extension_ui_response`
- **THEN** the plugin consumes the resumed event stream and returns pi's response

### Requirement: Provide skill documentation for tool usage
The system SHALL provide a skill file in Agent Skills format that teaches the AstrBot LLM agent how to use the pi tools.

#### Scenario: Skill file is present
- **WHEN** the plugin is loaded
- **THEN** a `skills/pi-connector/SKILL.md` file exists with valid frontmatter
- **THEN** the skill describes when to use pi tools, the available tools, and the standard workflow
- **THEN** the skill documents that `pi_send_message` and `pi_run_command` launch background tasks whose results arrive automatically, and instructs the agent not to poll status or abort running tasks on its own
- **THEN** the skill references the need for user confirmation before executing dangerous commands or aborting a running task
