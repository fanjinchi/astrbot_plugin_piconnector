## Why

当前 `astrbot_plugin_piconnector` 仅是一个 hello-world 模板，没有实际功能。用户希望 AstrBot 能成为本地 pi 代理的聊天入口：在 AstrBot 中打开、恢复、管理 pi session，并通过自然语言或 slash 命令与 pi 交互。同时 AstrBot 自身的 LLM agent 也应能调用 pi 的各项能力来完成复杂任务。

## What Changes

- 实现 `pi` 系列 AstrBot 命令：`/pi open`、`/pi sessions`、`/pi resume`、`/pi <自然语言>`、`/pic <command>`、`/pic help`。
- 实现 `pi` 扩展 UI 回复子命令：`/pi confirm`、`/pi select`、`/pi input`、`/pi edit`、`/pi cancel`。
- 插件通过 `pi --mode rpc` 为每个 AstrBot 聊天上下文维护一个本地 pi 子进程。
- 通过 JSONL RPC 协议与 pi 通信，管理 session、发送 prompt、执行 slash command、获取可用命令列表。
- 注册一组 LLM tools（`@llm_tool`），让 AstrBot 的 agent 能调用 pi 能力。
- 创建 `skills/pi-connector/SKILL.md`，以 Agent Skills 格式说明工具使用流程。
- 更新 `metadata.yaml` 和 `README.md`，反映插件实际功能。

## Capabilities

### New Capabilities

- `pi-session-management`: 在 AstrBot 中创建、列出、恢复、命名本地 pi session，绑定到绝对路径工作目录。
- `pi-chat-interface`: 通过 AstrBot 命令与 pi 进行自然语言对话、执行 pi slash 命令，并处理 pi extension 弹出的 UI 请求。
- `pi-llm-tools`: 向 AstrBot 的 LLM agent 暴露操作 pi 的 tools，并提供配套 skill 文档。

### Modified Capabilities

- 无。

## Impact

- 影响范围：插件 `main.py`、新增 `pi/` 子模块、新增 `skills/` 目录、更新 `metadata.yaml`、`README.md`。
- 外部依赖：本地安装 `pi` CLI（`@earendil-works/pi-coding-agent`），通过 `pi --mode rpc` 调用。
- 无破坏性变更（当前插件为模板状态）。
