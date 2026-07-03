# astrbot_plugin_piconnector

将 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 与本地 [pi](https://github.com/earendil-works/pi-mono) 编码代理连接起来的插件。

通过本插件，你可以在 AstrBot 内管理 pi 会话、用自然语言与 pi 对话、执行 pi 的 slash 命令，甚至让 AstrBot 自己的 LLM 智能体调用 pi 工具。

## 功能特性

- 打开、恢复和列出绑定到特定项目目录的 pi 会话。
- 向 pi 发送自然语言消息并接收流式回复。
- 执行 pi 的 slash 命令（`/pic <command>`）。
- 回复 pi 的扩展 UI 请求（`confirm`、`select`、`input`、`editor`）。
- 将 pi 操作暴露为 AstrBot LLM 工具，支持智能体工作流。

## 环境要求

- AstrBot v4.x 或更高版本。
- 本地已安装 `pi` CLI（`npm install -g @earendil-works/pi-coding-agent`）。
- 为 pi 配置好 LLM 提供商和 API 密钥。

## 安装

1. 将插件克隆或复制到 AstrBot 的插件目录：

   ```bash
   cd /path/to/astrbot/data/plugins
   git clone <repo-url> astrbot_plugin_piconnector
   ```

2. 重启 AstrBot 或重新加载插件。

3. 插件会将自己的会话存储创建在 `data/plugin_data/astrbot_plugin_piconnector/sessions` 下。

## 命令

### 会话管理

| 命令 | 说明 |
|---------|-------------|
| `/pi open <绝对路径>` | 在指定目录打开新的 pi 会话。 |
| `/pi sessions [目录]` | 列出目录下的会话；省略目录时使用当前会话所在目录。 |
| `/pi session` | 显示当前会话信息。 |
| `/pi info` | `/pi session` 的别名。 |
| `/pi resume [id]` | 按 id 或部分 id 恢复已有会话；省略 id 时恢复最近会话。 |
| `/pi tree [编号]` | 将当前会话 active branch 上的用户消息按行编号列出。使用 `/pi tree <编号>` 从该用户消息分岔到一个**新** pi 会话并继续对话。**注意：这不是 pi 原生的 `/tree` 命令；因为 RPC 模式未暴露原生树导航，所以通过 RPC `fork` 模拟实现。** |
| `/pi abort` | 中止当前 pi 操作。 |

### 对话与 slash 命令

| 命令 | 说明 |
|---------|-------------|
| `/pi <文本>` | 向当前 pi 会话发送自然语言消息。 |
| `/pic <command>` | 执行 pi 的 slash 命令（例如 `/pic opsx-explore`）。 |
| `/pic help` | 列出可用的 pi slash 命令。 |

### 回复 pi 的 UI 请求

当 pi 提出问题时，使用以下专用命令回复：

| 命令 | 说明 |
|---------|-------------|
| `/pi confirm <id> yes\|no` | 回复确认请求。 |
| `/pi select <id> <选项>` | 通过选项文本或 1-based 编号回复选择请求。 |
| `/pi input <id> <内容>` | 回复输入请求。 |
| `/pi edit <id> <文本>` | 回复编辑器请求。 |
| `/pi cancel <id>` | 取消待处理的 UI 请求。 |

## LLM 工具

当 AstrBot 的智能体模式启用时，本插件会暴露以下工具：

- `pi_open_session(path, name?)`
- `pi_list_sessions(dir?)`
- `pi_resume_session(session_id?)`
- `pi_send_message(message)`
- `pi_get_session_info()`
- `pi_run_command(command)`
- `pi_get_available_commands()`
- `pi_abort()`
- `pi_reply_ui(request_id, value)`

面向智能体的使用说明见 `skills/pi-connector/SKILL.md`。

## 使用示例

```text
/pi open /home/guigui/my-project
/pi refactor the auth module to use JWT
/pic opsx-explore
/pi sessions
/pi tree            # 列出 active branch 上编号后的用户消息
/pi tree 3          # 从第 3 条消息分岔，从该点继续对话
/pi resume          # 恢复最近会话
/pi resume abc123
```

## 说明

- 每个 AstrBot 聊天上下文都有独立的 pi 会话。
- 本插件不会连接到一个已经在运行的 pi TUI 进程。要在 TUI 和 AstrBot 之间切换，请在 TUI 会话保存或暂停后，使用 `/pi resume` 加载同一个会话文件。
- 确保 `pi` 在 PATH 中，以便插件可以启动 `pi --mode rpc`。

### 关于 `/pi tree`

本插件中的 `/pi tree` 命令**不是** pi 内置的 `/tree` 命令。pi 原生的 `/tree` 是交互式 TUI 功能，可以让你在会话历史中任意点原地跳转。但 pi 的 **RPC 模式**没有暴露原生的树导航命令（没有 `/tree` 或 `navigateTree` 的 RPC 等价物）。

为了在不修改 pi 的前提下提供类似功能，`/pi tree` 的工作方式如下：

1. 调用 RPC `get_tree` 命令查看当前会话树。
2. 从当前 `leafId` 沿 active branch 回溯到根节点，过滤掉非用户消息，将剩余用户消息按行编号显示。
3. 当你运行 `/pi tree <编号>` 时，将行号解析为对应的 entry id，并调用 RPC `fork` 命令。
4. `fork` 会从该用户消息创建一个新的 pi 会话，当前 RPC 连接会被重新绑定到新会话，然后你就可以从该点继续对话。

这意味着 `/pi tree <编号>` 会生成一个新的 session 文件，而不是在原 session 中做原地 branch。如果你需要真正的原地树导航，请直接使用 pi 的交互式 TUI（`/tree`）。

---

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
| `/pi session` | Show current session info. |
| `/pi info` | Alias for `/pi session`. |
| `/pi resume [id]` | Resume an existing session by its id or partial id. Omit id to resume the most recent session. |
| `/pi tree [number]` | View user-only messages on the active branch as numbered lines. Use `/pi tree <number>` to fork from that user message into a **new** pi session and continue from there. This is **not** pi's native `/tree`; it is emulated via RPC `fork` because RPC mode does not expose native tree navigation. |
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
- `pi_resume_session(session_id?)`
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
/pi tree            # list numbered user messages on the active branch
/pi tree 3          # fork from message #3 and continue from that point
/pi resume          # resume most recent session
/pi resume abc123
```

## Notes

- Each AstrBot chat context has its own isolated pi session.
- The plugin does not connect to an already-running pi TUI process. To switch between TUI and AstrBot, use `/pi resume` to load the same session file after the TUI session is saved or paused.
- Make sure `pi` is on your PATH so the plugin can spawn `pi --mode rpc`.

### About `/pi tree`

The `/pi tree` command in this plugin is **not** pi's built-in `/tree` command. pi's native `/tree` is an interactive TUI feature that lets you jump to any point in the session history in-place. However, pi's **RPC mode** does not expose a native tree-navigation command (there is no RPC equivalent of `/tree` or `navigateTree`).

To provide similar functionality without modifying pi itself, `/pi tree` works as follows:

1. It calls the RPC `get_tree` command to inspect the current session tree.
2. It walks the active branch from the current `leafId` back to the root, filters out non-user messages, and displays the remaining user messages as numbered lines.
3. When you run `/pi tree <number>`, it resolves the line number to the corresponding entry id and invokes the RPC `fork` command.
4. `fork` creates a **new** pi session starting from that user message. The current RPC connection is rebound to the new session, and you continue chatting from that point.

This means `/pi tree <number>` produces a new session file rather than performing an in-place branch inside the original session. If you need true in-place tree navigation, you must use pi's interactive TUI (`/tree`) directly.
