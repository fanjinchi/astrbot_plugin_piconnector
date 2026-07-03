# astrbot_plugin_piconnector

An [AstrBot](https://github.com/AstrBotDevs/AstrBot) plugin that connects your AstrBot instance to a local [pi](https://github.com/earendil-works/pi-mono) coding agent.

With this plugin, you can manage pi sessions, chat with pi using natural language, execute pi slash commands, and even let AstrBot's own LLM agent call pi tools — all from within AstrBot.

## Features

- Open, resume, and list pi sessions bound to specific project directories.
- Send natural language messages to pi and receive streaming responses.
- Execute pi slash commands (`/pic <command>`).
- Reply to pi extension UI requests (`confirm`, `select`, `input`, `editor`).
- Expose pi operations as AstrBot LLM tools for agentic workflows.

## Requirements

- AstrBot v4.x or later.
- `pi` CLI installed locally (`npm install -g @earendil-works/pi-coding-agent`).
- A configured LLM provider and API key for pi.

## Installation

1. Clone or copy this plugin into AstrBot's plugin directory:

   ```bash
   cd /path/to/astrbot/data/plugins
   git clone <repo-url> astrbot_plugin_piconnector
   ```

2. Restart AstrBot or reload the plugin.

3. The plugin will create its session storage under `data/plugin_data/astrbot_plugin_piconnector/sessions`.

## Commands

### Session management

| Command | Description |
|---------|-------------|
| `/pi open <absolute path>` | Open a new pi session at the given directory. |
| `/pi sessions [dir]` | List sessions in a directory. Uses the active session's directory if omitted. |
| `/pi resume <id>` | Resume an existing session by its id or partial id. |
| `/pi abort` | Abort the current pi operation. |

### Chat and slash commands

| Command | Description |
|---------|-------------|
| `/pi <text>` | Send a natural language message to the current pi session. |
| `/pic <command>` | Execute a pi slash command (e.g., `/pic opsx-explore`). |
| `/pic help` | List available pi slash commands. |

### Replying to pi UI requests

When pi asks a question, reply with one of these dedicated commands:

| Command | Description |
|---------|-------------|
| `/pi confirm <id> yes\|no` | Reply to a confirm request. |
| `/pi select <id> <option>` | Reply to a select request by option text or 1-based number. |
| `/pi input <id> <value>` | Reply to an input request. |
| `/pi edit <id> <text>` | Reply to an editor request. |
| `/pi cancel <id>` | Cancel a pending UI request. |

## LLM tools

When AstrBot's agent mode is active, the plugin exposes the following tools:

- `pi_open_session(path, name?)`
- `pi_list_sessions(dir?)`
- `pi_resume_session(session_id)`
- `pi_send_message(message)`
- `pi_get_session_info()`
- `pi_run_command(command)`
- `pi_get_available_commands()`
- `pi_abort()`
- `pi_reply_ui(request_id, value)`

See `skills/pi-connector/SKILL.md` for the agent-facing instructions.

## Example usage

```text
/pi open /home/guigui/my-project
/pi refactor the auth module to use JWT
/pic opsx-explore
/pi sessions
/pi resume abc123
```

## Notes

- Each AstrBot chat context has its own isolated pi session.
- The plugin does not connect to an already-running pi TUI process. To switch between TUI and AstrBot, use `/pi resume` to load the same session file after the TUI session is saved or paused.
- Make sure `pi` is on your PATH so the plugin can spawn `pi --mode rpc`.
