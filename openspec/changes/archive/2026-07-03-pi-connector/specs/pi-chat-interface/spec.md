## ADDED Requirements

### Requirement: Send natural language to current pi session
The system SHALL allow users to send natural language messages to the currently active pi session.

#### Scenario: User sends a natural language message
- **WHEN** the user sends `/pi 帮我重构这个函数`
- **THEN** the plugin forwards the message to the current `pi --mode rpc` process via the `prompt` command
- **THEN** the plugin streams pi's response back to the user
- **THEN** if no session is active, the plugin responds with instructions to open or resume one

### Requirement: Execute pi slash commands
The system SHALL allow users to execute pi slash commands through AstrBot.

#### Scenario: User executes a slash command
- **WHEN** the user sends `/pic opsx-explore`
- **THEN** the plugin sends the command as `/opsx-explore` to the current pi session via the `prompt` command
- **THEN** the plugin returns the command output to the user
- **THEN** if the user omits the command name, the plugin responds with usage instructions

#### Scenario: Command name without leading slash is accepted
- **WHEN** the user sends `/pic explore`
- **THEN** the plugin automatically prefixes the command with `/` and executes `/explore`

### Requirement: List available pi slash commands
The system SHALL allow users to list all slash commands available in the current pi session.

#### Scenario: User requests command help
- **WHEN** the user sends `/pic help`
- **THEN** the plugin sends `get_commands` to the pi RPC process
- **THEN** the plugin returns a formatted list of command names, descriptions, and sources
- **THEN** if no session is active, the plugin responds with instructions to open or resume one

### Requirement: Handle pi extension UI requests
The system SHALL allow users to respond to UI requests initiated by pi extensions.

#### Scenario: Pi asks for confirmation
- **WHEN** the pi process emits a `confirm` UI request
- **THEN** the plugin assigns a local request ID and displays the question to the user
- **THEN** the plugin instructs the user to reply with `/pi confirm <id> yes` or `/pi confirm <id> no`
- **THEN** when the user replies, the plugin forwards the response to pi via `extension_ui_response`

#### Scenario: Pi asks for a selection
- **WHEN** the pi process emits a `select` UI request with options
- **THEN** the plugin displays the options and the local request ID to the user
- **THEN** the user sends `/pi select <id> <option>`
- **THEN** the plugin forwards the selected option to pi

#### Scenario: Pi asks for text input
- **WHEN** the pi process emits an `input` UI request
- **THEN** the plugin displays the prompt and the local request ID
- **THEN** the user sends `/pi input <id> <text>`
- **THEN** the plugin forwards the value to pi

#### Scenario: Pi asks for multi-line text editor input
- **WHEN** the pi process emits an `editor` UI request with prefilled content
- **THEN** the plugin displays the editing request and the local request ID
- **THEN** the user sends `/pi edit <id> <text>`
- **THEN** the plugin forwards the edited value to pi

#### Scenario: UI request times out
- **WHEN** the user does not respond before the pi UI request times out
- **THEN** the plugin removes the pending request from its local mapping
- **THEN** the plugin informs the user that the request has timed out

### Requirement: Cancel current pi operation
The system SHALL allow users to abort the current pi operation.

#### Scenario: User aborts
- **WHEN** the user sends `/pi abort`
- **THEN** the plugin sends the `abort` command to the current pi process
- **THEN** the plugin confirms the abort request was sent
