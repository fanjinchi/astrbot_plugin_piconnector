---
name: pi-connector
description: Connect AstrBot to a local pi agent for code tasks, file operations, and session management. Use when the user wants to work with a local project through the pi coding assistant.
---

# AstrBot Pi Connector

## When to use

Use this skill when the user wants to:

- Write, edit, or refactor code in a specific project directory
- Run a series of commands through the local pi coding agent
- Continue a previous pi session from AstrBot
- Execute a pi slash command (e.g., `/opsx-explore`)
- List available pi slash commands
- Respond to a pi extension UI request (confirm, select, input, editor)

## Available tools

- `pi_open_session(path: string, name?: string)` — Open a new pi session at an absolute directory path. `path` must be a real directory.
- `pi_list_sessions(dir?: string)` — List existing pi sessions in a directory, or in the active session's directory if omitted.
- `pi_resume_session(session_id: string)` — Resume an existing session by its id or partial id.
- `pi_send_message(message: string)` — Send a natural language message to the current pi session and return the response.
- `pi_get_session_info()` — Return the current session id, name, working directory, file path, and message count.
- `pi_run_command(command: string)` — Execute a pi slash command in the current session (without the leading `/`).
- `pi_get_available_commands()` — List the slash commands available in the current pi session.
- `pi_abort()` — Abort the current pi operation.
- `pi_reply_ui(request_id: number, value: string)` — Reply to a pending pi extension UI request.

## Standard workflow

1. **Open or resume a session.** If the user has provided a directory, use `pi_open_session`. If they mention a previous session, use `pi_list_sessions` first, then `pi_resume_session`.

2. **Send the request.** Use `pi_send_message` for general tasks, or `pi_run_command` for pi slash commands.

3. **Wait for the response.** The tool returns the final pi text. If pi emits a UI request during processing, the response will include the request and instructions for the user.

4. **Handle UI requests.** If a UI request appears, ask the user to reply with the appropriate `/pi ...` subcommand (e.g., `/pi confirm 1 yes`). Do not fabricate replies yourself.

5. **Summarize.** After pi finishes, summarize the result for the user.

## Safety notes

- pi may execute shell commands or modify files. Always confirm the working directory before opening a session.
- If pi asks for confirmation via a UI request, do not auto-approve. Let the user decide.
- Do not send dangerous commands (e.g., `rm -rf /`, `sudo`, destructive writes) unless the user explicitly requests them.
- Each AstrBot chat context has its own isolated pi session.
