# pi-llm-tools Specification

## Purpose
TBD - created by archiving change pi-connector. Update Purpose after archive.
## Requirements
### Requirement: Expose tools to AstrBot LLM agent for pi session management
The system SHALL register tools that allow the AstrBot LLM agent to open, list, and resume pi sessions.

#### Scenario: Agent opens a session
- **WHEN** the AstrBot LLM agent calls `pi_open_session` with a valid absolute path
- **THEN** the plugin creates a new pi session at that path
- **THEN** the plugin returns the session identifier and working directory

#### Scenario: Agent lists sessions
- **WHEN** the AstrBot LLM agent calls `pi_list_sessions` with a directory path
- **THEN** the plugin returns a list of available sessions with identifiers and names

#### Scenario: Agent resumes a session
- **WHEN** the AstrBot LLM agent calls `pi_resume_session` with a session identifier
- **THEN** the plugin loads the session into the current chat context
- **THEN** the plugin confirms the active session

### Requirement: Expose tools for natural language interaction
The system SHALL register tools that allow the AstrBot LLM agent to send messages to pi and query session state.

#### Scenario: Agent sends a message
- **WHEN** the AstrBot LLM agent calls `pi_send_message` with a message
- **THEN** the plugin forwards the message to pi via the `prompt` RPC command
- **THEN** the plugin returns the final pi response after the agent run completes

#### Scenario: Agent queries session info
- **WHEN** the AstrBot LLM agent calls `pi_get_session_info`
- **THEN** the plugin returns the current session file, identifier, name, message count, and working directory

### Requirement: Expose tools for slash commands and abort
The system SHALL register tools that allow the AstrBot LLM agent to execute pi slash commands and abort operations.

#### Scenario: Agent executes a slash command
- **WHEN** the AstrBot LLM agent calls `pi_run_command` with a command name
- **THEN** the plugin executes the command in the current pi session
- **THEN** the plugin returns the command output

#### Scenario: Agent lists available slash commands
- **WHEN** the AstrBot LLM agent calls `pi_get_available_commands`
- **THEN** the plugin returns the list of pi slash commands available in the current session

#### Scenario: Agent aborts current operation
- **WHEN** the AstrBot LLM agent calls `pi_abort`
- **THEN** the plugin sends the `abort` command to pi
- **THEN** the plugin returns the abort status

### Requirement: Expose tool to respond to pi UI requests
The system SHALL register a tool that allows the AstrBot LLM agent to respond to pi extension UI requests.

#### Scenario: Agent replies to a UI request
- **WHEN** the AstrBot LLM agent calls `pi_reply_ui` with a local request ID and value
- **THEN** the plugin forwards the response to pi via `extension_ui_response`
- **THEN** the plugin returns whether the response was accepted

### Requirement: Provide skill documentation for tool usage
The system SHALL provide a skill file in Agent Skills format that teaches the AstrBot LLM agent how to use the pi tools.

#### Scenario: Skill file is present
- **WHEN** the plugin is loaded
- **THEN** a `skills/pi-connector/SKILL.md` file exists with valid frontmatter
- **THEN** the skill describes when to use pi tools, the available tools, and the standard workflow
- **THEN** the skill references the need for user confirmation before executing dangerous commands

