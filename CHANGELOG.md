# Changelog

本文件记录 astrbot_plugin_piconnector 的版本变更。

## v1.2.0 — 2026-10-04

后台任务（`pi_send_message`）从「不可用」到「可靠可用」的一次集中修复，
并补上了它本该有的语义：**后台任务是跑给 LLM 看的**。

### 新增

- **后台任务完成后唤醒主 agent**：pi 干完活不再只是把结果塞进对话历史、等用户
  下次开口；现在会把结果作为一条唤醒事件（`is_wake = True`）投回 AstrBot 事件
  队列，主 agent 像收到用户消息一样被叫醒来处理结果，并可用
  `send_message_to_user` 自行联系用户。全程只用公开 API
  （`Context.get_platform_inst` / `Platform.create_event` /
  `Context.get_event_queue`）。
- **后台任务空闲看门狗**：卡住的任务不再永久占住会话上下文。

### 修复

- **「pi 干完了活，任务却好几分钟不完成」**（本次核心问题）：pi 启动时会发交互式
  UI 请求，后台任务会等用户回复，上限是 pi 默认的 300 秒；其间 pi 早已发出的
  `agent_end` / `agent_settled` 全部积压在队列里无人消费。等待上限压到 **30 秒**。
- **通知回调可能挂起**：`_notify_task_finished` 加 30 秒超时保护，通知卡住也不再
  拖死任务收尾。
- **任务完成信号判据错误**：`agent_end` 会在长任务中途出现（导致任务被截断），
  只有 `agent_settled` 是真正的结束信号。
- **pi 连接被超长输出行搞断**：行上限从 64KiB 提到 **16MiB**，并补充子进程退出
  诊断。
- **`tasks` 模块日志全部丢失**：该模块用的是裸 `logging.getLogger(__name__)`，
  没有挂到 AstrBot 的日志 handler 上（同项目的 `connection` 模块正常）；改用
  `from astrbot.api import logger`。
- **`ASTRBOT_ROOT` 未固定**：pi 子进程可能把 `data` 目录写进插件目录（曾导致
  多个插件集体加载失败）；现在显式注入，且可配置以适配不同部署方式。
- **重载后仍跑旧代码**：插件加载时清理残留的 `pi_connector` 顶层模块。
- **群聊/任务通知重复**：唤醒事件已携带完整结果，去掉了冗余的历史注入。
- 采纳代码评审意见：看门狗失效、成功误报 `failed`、工具错误泄漏、注入不一致。

### 重构

- **移除对 AstrBot 私有路径（`astrbot.core`）的全部依赖**，统一改走
  `astrbot.api`：
  - `AstrBotMessage` / `MessageMember` / `MessageType` / `Plain` —— 本就有公开
    导出，之前走错了路径；
  - `MessageSession` —— 未公开导出，改为自行解析 umo
    （`"platform_id:MessageType:session_id"`，逻辑等价于 `split(":", 2)`）；
  - `get_astrbot_root()` 兜底 —— 删除，仅保留基于 `astrbot.__file__` 的推导。
  现在全部依赖仅为 `astrbot.api.*` 与顶层 `astrbot` 包本身。

## v1.1.0 — 2026-10-04

- 版本号建立（`v1.0.0` → `v1.1.0`）；后台任务链路初步可用。

## v1.0.0 — 2026-07-30

- 初始版本：连接 AstrBot 与本地 pi agent，支持会话管理、聊天与代码任务。
