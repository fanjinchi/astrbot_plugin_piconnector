## ADDED Requirements

### Requirement: Open new pi session at absolute path
The system SHALL allow users to create a new pi session bound to a specified absolute directory path.

#### Scenario: User opens a new session
- **WHEN** the user sends `/pi open /home/guigui/project`
- **THEN** the plugin spawns a new `pi --mode rpc` subprocess for the current chat context
- **THEN** the new session is created with the working directory set to `/home/guigui/project`
- **THEN** the plugin responds with the session identifier and name

### Requirement: List existing pi sessions in a directory
The system SHALL allow users to list existing pi sessions in a specified directory.

#### Scenario: User lists sessions
- **WHEN** the user sends `/pi sessions /home/guigui/project`
- **THEN** the plugin returns a list of sessions stored under that directory
- **THEN** each session entry includes its identifier, display name, timestamp, and message count

#### Scenario: User lists sessions without specifying directory
- **WHEN** the user sends `/pi sessions` and the current chat has an active session
- **THEN** the plugin lists sessions in the same directory as the active session
- **THEN** if no active session exists, the plugin responds with usage instructions

### Requirement: Resume an existing pi session
The system SHALL allow users to resume a previously created pi session by its identifier.

#### Scenario: User resumes a session
- **WHEN** the user sends `/pi resume abc123`
- **THEN** the plugin loads the session file matching the identifier
- **THEN** if the chat already has an active RPC process, the plugin sends `switch_session` to that process
- **THEN** if no active process exists, the plugin spawns a new `pi --mode rpc` process bound to the session
- **THEN** the plugin confirms the resumed session and its working directory

### Requirement: Display current session information
The system SHALL allow users to query the current pi session state.

#### Scenario: User requests session info
- **WHEN** the user sends `/pi session` or `/pi info`
- **THEN** the plugin returns the current session file, identifier, display name, message count, and working directory
- **THEN** if no session is active, the plugin responds with instructions to open or resume one
