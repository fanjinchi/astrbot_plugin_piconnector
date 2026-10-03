## MODIFIED Requirements

### Requirement: Display current session information
The system SHALL allow users to query the current pi session state, including any active background task.

#### Scenario: User requests session info
- **WHEN** the user sends `/pi session` or `/pi info`
- **THEN** the plugin returns the current session file, identifier, display name, message count, and working directory
- **THEN** if a background task is running, the plugin also returns the task identifier, status, and elapsed time
- **THEN** if no session is active, the plugin responds with instructions to open or resume one
