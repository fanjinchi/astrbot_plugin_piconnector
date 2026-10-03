## MODIFIED Requirements

### Requirement: Handle pi extension UI requests
The system SHALL allow users to respond to UI requests initiated by pi extensions, both in foreground operation and while a background task is running.

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

#### Scenario: UI request arrives while a background task is running
- **WHEN** the pi process emits an interactive UI request while a background task is active for the chat
- **THEN** the plugin pushes the request and reply instructions to the chat proactively
- **THEN** when the user replies, the plugin forwards the reply and the background task resumes and delivers the final result

#### Scenario: UI request times out
- **WHEN** the user does not respond before the pi UI request times out
- **THEN** the plugin removes the pending request from its local mapping
- **THEN** the plugin informs the user that the request has timed out

### Requirement: Cancel current pi operation
The system SHALL allow users to abort the current pi operation, including an active background task.

#### Scenario: User aborts
- **WHEN** the user sends `/pi abort`
- **THEN** the plugin sends the `abort` command to the current pi process
- **THEN** the plugin cancels the active background task if one is running
- **THEN** the plugin confirms the abort request was sent
