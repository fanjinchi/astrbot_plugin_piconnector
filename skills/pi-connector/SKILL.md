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
- `pi_resume_session(session_id?: string)` — Resume an existing session by its id or partial id. Omit to resume the most recent session.
- `pi_send_message(message: string)` — Send a natural language message to the current pi session. This launches a background task and returns a task id immediately. The complete result will be pushed to the chat automatically.
- `pi_get_session_info()` — Return the current session id, name, working directory, file path, and message count. If a background task is running, also reports its id, status, and elapsed time.
- `pi_run_command(command: string)` — Execute a pi slash command in the current session. This launches a background task and returns a task id immediately, just like `pi_send_message`.
- `pi_get_available_commands()` — List the slash commands available in the current pi session.
- `pi_abort(user_confirmed: boolean)` — Abort the current pi operation. `user_confirmed` must be `true`; only abort when the user explicitly asks to cancel.
- `pi_reply_ui(request_id: number, value: string)` — Reply to a pending pi extension UI request. Requests expire after ~30 seconds if nobody answers.

## Standard workflow

1. **Open or resume a session.** If the user has provided a directory, use `pi_open_session`. If they mention a previous session, use `pi_list_sessions` first, then `pi_resume_session`.

2. **Send the request.** Use `pi_send_message` for general tasks, or `pi_run_command` for pi slash commands.

3. **Return immediately.** Both tools launch a background task and return a task id. Do not wait for the result in the same turn.

4. **Do not poll or re-send.** While the task is running, do not call `pi_get_session_info` repeatedly and do not call `pi_send_message`/`pi_run_command` again for the same request. pi may stay silent for minutes while running tools or subagents; this is normal.

5. **Handle UI requests.** If a UI request appears, ask the user to reply with the appropriate `/pi ...` subcommand (e.g., `/pi confirm 1 yes`). Do not fabricate replies yourself.

6. **Trust the automatic delivery.** When the background task finishes, the mainline final answer is pushed to the user and the *same* text wakes you up in a `[pi background task #N finished: ...]` note. The note says the result was already delivered — do **not** restate it. Reply only if it needs extra explanation or a follow-up action; never re-send it through `pi_send_message`.

7. **Do not switch sessions mid-task.** While a background task runs, `/pi open`, `/pi resume`, `pi_open_session`, and `pi_resume_session` are refused — the connection and its event stream belong to the running task. Wait for it to finish, or abort it first.

8. **Unanswered UI requests expire.** A UI request that nobody answers is cancelled after ~30 seconds; the chat is told so and pi continues on its own default. Do not invent a reply; if the user wanted a different answer, resend the task.

## Safety notes

- pi may execute shell commands or modify files. Always confirm the working directory before opening a session.
- If pi asks for confirmation via a UI request, do not auto-approve. Let the user decide.
- Do not send dangerous commands (e.g., `rm -rf /`, `sudo`, destructive writes) unless the user explicitly requests them.
- Each AstrBot chat context has its own isolated pi session.
- Only call `pi_abort(user_confirmed=true)` when the user explicitly asks to cancel a running task. Do not abort because the task has been silent for a while.
