## 1. Foundation

- [ ] 1.1 Create plugin package structure (`pi_connector/` subpackage with `__init__.py`, `connection.py`, `commands.py`, `tools.py`, `models.py`).
- [ ] 1.2 Update `metadata.yaml` with correct plugin name, display name, version, author, and description.
- [ ] 1.3 Add plugin configuration support (e.g., default `session_dir`, `pi_binary_path`) using AstrBot `Star` metadata if available.

## 2. Pi RPC Client

- [ ] 2.1 Implement `PiConnection` class that spawns `pi --mode rpc` as a subprocess with stdin/stdout pipes.
- [ ] 2.2 Implement JSONL writer to send RPC commands (`prompt`, `new_session`, `switch_session`, `get_state`, `get_commands`, `abort`, `get_entries`) to pi stdin.
- [ ] 2.3 Implement JSONL reader that parses pi stdout events (`response`, `message_update`, `message_end`, `agent_end`, `turn_end`, `tool_execution_*`, `extension_ui_request`).
- [ ] 2.4 Implement streaming event aggregation to collect assistant text deltas and tool results into a final response.
- [ ] 2.5 Add process lifecycle management (start, health check, restart on crash, terminate on plugin unload).

## 3. Connection & Session Management

- [ ] 3.1 Implement `PiConnectionManager` that maps each AstrBot chat context to one `PiConnection` instance.
- [ ] 3.2 Implement session file discovery in the configured session directory, parsing filenames and reading session headers for display names.
- [ ] 3.3 Implement `open_session(path, name=None)` that starts a new pi session at an absolute path.
- [ ] 3.4 Implement `resume_session(session_id_or_path)` that loads an existing session via `switch_session` or process restart.
- [ ] 3.5 Implement `list_sessions(dir)` that returns formatted session information.
- [ ] 3.6 Implement `get_session_info()` that calls `get_state` and formats the result.

## 4. AstrBot Commands

- [ ] 4.1 Implement `/pi open <path>` command handler.
- [ ] 4.2 Implement `/pi sessions [dir]` command handler.
- [ ] 4.3 Implement `/pi resume <id>` command handler.
- [ ] 4.4 Implement `/pi <text>` default handler that sends a natural language prompt and streams the response.
- [ ] 4.5 Implement `/pic <command>` handler that executes a pi slash command (auto-prefixing `/` if missing).
- [ ] 4.6 Implement `/pic help` handler that calls `get_commands` and formats the list.
- [ ] 4.7 Implement `/pi abort` handler that sends the `abort` command.
- [ ] 4.8 Implement error handling for "no active session" and invalid commands.

## 5. UI Request Handling

- [ ] 5.1 Implement pending UI request tracking per chat context with local numeric IDs and timeout tracking.
- [ ] 5.2 Implement `/pi confirm <id> yes|no` handler.
- [ ] 5.3 Implement `/pi select <id> <option>` handler.
- [ ] 5.4 Implement `/pi input <id> <text>` handler.
- [ ] 5.5 Implement `/pi edit <id> <text>` handler.
- [ ] 5.6 Implement `/pi cancel <id>` handler.
- [ ] 5.7 Format incoming `extension_ui_request` events into user-friendly messages with request IDs and instructions.
- [ ] 5.8 Implement timeout cleanup: remove expired requests and notify the user.

## 6. LLM Tools

- [ ] 6.1 Register `pi_open_session` tool using `@llm_tool`.
- [ ] 6.2 Register `pi_list_sessions` tool.
- [ ] 6.3 Register `pi_resume_session` tool.
- [ ] 6.4 Register `pi_send_message` tool.
- [ ] 6.5 Register `pi_get_session_info` tool.
- [ ] 6.6 Register `pi_run_command` tool.
- [ ] 6.7 Register `pi_get_available_commands` tool.
- [ ] 6.8 Register `pi_abort` tool.
- [ ] 6.9 Register `pi_reply_ui` tool.
- [ ] 6.10 Ensure all tools share the same `PiConnectionManager` and handle "no active session" gracefully.

## 7. Skill Documentation

- [ ] 7.1 Create `skills/pi-connector/` directory.
- [ ] 7.2 Write `skills/pi-connector/SKILL.md` with Agent Skills frontmatter (`name`, `description`).
- [ ] 7.3 Document when to use the pi connector tools, available tools, and standard workflow.
- [ ] 7.4 Document safety guidance for dangerous commands and UI requests requiring user confirmation.

## 8. Documentation & Polish

- [ ] 8.1 Rewrite `README.md` with installation, configuration, command reference, and usage examples.
- [ ] 8.2 Add inline comments and docstrings in English for all new code.
- [ ] 8.3 Add basic logging for RPC communication, errors, and session lifecycle.

## 9. Verification

- [ ] 9.1 Manually test `/pi open`, `/pi sessions`, `/pi resume`, `/pi <text>`, `/pic <command>`, `/pic help` in AstrBot.
- [ ] 9.2 Manually test UI reply commands (`/pi confirm`, `/pi select`, `/pi input`, `/pi edit`, `/pi cancel`) with an extension that triggers UI requests.
- [ ] 9.3 Verify that each chat context has an isolated pi session and process.
- [ ] 9.4 Verify that streaming responses are displayed correctly in AstrBot.
- [ ] 9.5 Verify that the skill file is discoverable and its instructions are coherent.
