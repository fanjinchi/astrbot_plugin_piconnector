## Context

`astrbot_plugin_piconnector` 当前是一个 hello-world 模板。用户希望 AstrBot 能作为本地 pi agent 的入口，在聊天中管理 pi session、发送自然语言指令、执行 pi slash 命令，并允许 AstrBot 自己的 LLM agent 通过 tools 调用 pi 能力。

pi 是本地 CLI 工具（`@earendil-works/pi-coding-agent`），支持 `--mode rpc` 通过 stdin/stdout JSONL 协议进行 headless 操作。session 以 JSONL 文件形式保存在 `~/.pi/agent/sessions/`（或 `--session-dir` 指定目录），按工作目录组织。`pi --mode rpc` 提供 `prompt`、`switch_session`、`get_commands` 等命令，以及 `message_update`、`agent_end`、`extension_ui_request` 等事件。

## Goals / Non-Goals

**Goals:**
- 每个 AstrBot 聊天上下文维护一个独立的 `pi --mode rpc` 子进程。
- 用户可通过 AstrBot 命令打开、列出、恢复 pi session。
- 用户可通过自然语言与当前 pi session 连续对话。
- 用户可执行 pi slash 命令并查看可用 slash 命令列表。
- 用户可回复 pi extension 弹出的 UI 请求（select / confirm / input / editor）。
- 向 AstrBot LLM agent 暴露 tools，并提供 skill 文档说明使用流程。

**Non-Goals:**
- 不直接连接或接管正在运行中的 pi TUI 进程。
- 不实现跨多个聊天上下文共享同一个 pi session。
- 不替代 pi 自身的 TUI 体验；插件定位为远程/聊天入口。

## Decisions

### 1. 使用 `pi --mode rpc` 作为唯一集成方式
**理由**: pi 的 TUI 模式没有 socket/attach 机制，无法被外部接管。RPC 模式专为嵌入其他应用设计，支持完整的会话生命周期、命令发现、流式事件和 UI 请求。替代方案（tmux 按键注入）脆弱且不可靠。

### 2. 每个 AstrBot 聊天上下文一个 pi 进程
**理由**: 避免不同用户/群聊互相干扰 session 状态。每个聊天绑定到独立的 `PiConnection` 实例，内部持有自己的 `pi --mode rpc` 子进程。如果聊天没有 active pi 进程，`/pi open` 或 `/pi resume` 会新建；`switch_session` 可在同一个进程内切换 session。

### 3. session 使用 pi 原生文件系统组织
**理由**: 不引入额外数据库，依赖 pi 的 session 文件格式。插件使用 `pi --session-dir` 或默认目录查找 session。`--session-dir` 可配置为插件数据目录，避免与 TUI 的默认 session 目录冲突。

### 4. 命令采用显式子命令，避免路径/session/自然语言歧义
**理由**: `pi` 后面可能是路径、session ID 或自然语言，无法仅靠文本判断。因此采用：
- `/pi open <path>` 新建 session
- `/pi sessions [dir]` 列出 session
- `/pi resume <id>` 恢复 session
- `/pi <自然语言>` 默认发送给当前 session

### 5. pi extension UI 请求通过专用子命令回复
**理由**: 避免普通消息被误识别为 UI 回复。插件为每个待处理请求分配本地编号，用户回复 `/pi confirm 1 yes`、`/pi select 1 Allow` 等。插件维护 `pending_ui_requests` 映射，超时后自动取消。

### 6. LLM tools 直接操作 RPC 协议
**理由**: tools 是插件向 AstrBot LLM 暴露的接口，与命令层共享同一个 `PiConnectionManager`。避免重复实现，确保行为一致。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| pi 子进程崩溃 | 捕获异常，重启 RPC 进程；提示用户当前 session 可能中断 |
| JSONL 解析失败 | 忽略无法解析的行，记录错误；对半行事件等待完整行 |
| 同一 session 被 TUI 和 AstrBot 同时写入 | 明确不支持；恢复时提示用户先关闭 TUI |
| 多个 UI 请求并发 | 本地编号 + 超时管理；超时后拒绝回复 |
| 长流式输出阻塞 AstrBot | 通过 `yield` 流式返回文本增量 |
| 用户发送 `/pic` 命令时缺少前导 `/` | 插件自动补全，或报错提示正确格式 |

## Migration Plan

- 无迁移步骤。当前插件为模板，直接替换为新的 `main.py` 和模块结构即可。
- 用户需确保本地已安装 `pi` CLI 并配置好 provider/API key。

## Open Questions

- 是否需要支持多个 provider/model 切换？可通过 `pi_get_session_info` 和 `pi_run_command` 部分实现。
- 是否需要把 pi 的 `message_update` 事件完整转发到 AstrBot，还是仅返回最终文本？建议先支持流式返回文本增量。
- 用户输入的图片、文件等 AstrBot 消息类型如何传给 pi RPC？目前 `prompt` 支持 `images` 字段，可后续扩展。
