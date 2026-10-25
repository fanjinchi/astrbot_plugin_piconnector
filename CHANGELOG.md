# Changelog

本文件记录 astrbot_plugin_piconnector 的版本变更。

## v1.3.1 — 2026-10-25

中断可见性补丁：**模型输出被切断时不再报 ✅ 完成**，并且能看出断在主线上、子任务里，
还是子任务做完后的那条总结。

### 修复

- **`agent_settled` 被当成成功证据**（本次核心问题）：pi 在 `finally` 里发
  `agent_settled`，`aborted` 只反映显式 abort 请求。所以供应商/网络失败重试耗尽、
  输出长度上限截断，都会以 `agent_settled{aborted: false}` 到达 —— 旧代码一律映射
  成 `completed`，还把半截话当最终回答推给聊天。现在读同一事件流里的
  `auto_retry_start` / `auto_retry_end` / `message_end.stopReason` /
  `errorMessage`：`error` / `aborted` → `failed`，半截文本存进 `PiTask.partial_answer`
  并标注「中断前已生成的部分」，**不再覆盖** `final_answer`。重试次数与最后一次
  错误（`retry_attempts` / `retry_final_error`）一并记进任务，日志可见。
- **不知道断在哪一段**：按 `entry_appended` 里的任务簿记条目推断阶段
  （`task-start` / `task-start-delivery` / `task-resume-delivery` → 子任务；
  `task-done` / `task-result` → 总结），中断时快照为 `interrupt_phase`，通知分三种：
  `❌ 子任务执行中断`、`⚠️ 子任务已完成，主线总结中断`、`❌ 主线回答中断`。
  「活没干完」和「活干完了但报告没出来」对用户是两件事，不能合成一句话。
  干净成功路径的文案一个字都没改。
- **长度截断单列**：`stopReason: "length"` 的文本是可用的，状态仍为 `completed`，
  只是追加一行 `⚠️ 回答被长度上限截断` —— 把一个已交付的结果报成失败是更糟的谎。

## v1.3.0 — 2026-10-25

后台任务语义的收口版本：**一次 rpc 请求 = 一个后台任务 = 聊天里一条主线最终回答
+ 一条唤醒事件**。命令行与小模型的交付链路从此只有这一条。

### 修复

- **`disposition: "handled"` 让任务空等 1200 秒**（本次核心问题，也是上一版
  「只等 `agent_settled`」被回滚的真因）：pi 对扩展命令/被处理的 prompt 回复
  `data.disposition == "handled"`，此时**不会**再发 `agent_settled`。旧代码在
  看门狗修好后无条件等 settle，于是每个被处理的命令都硬等到 1200 秒超时。现在
  看到 `handled` 立即以 completed 收尾（无模型回合，不空发唤醒事件）。**不要把
  终点判据改回 `agent_end`** —— `agent_end` 会在长任务中途出现，真正的 bug 在
  `handled` 分支。
- **结果取错了对象**：以前把整轮所有 `text_delta` 拼起来当结果，用户看到的是
  子任务的旁白加上主线回答。现在取本轮**最后一条带文本的 assistant
  `message_end`**（`message_end.message` 优先于流式 delta，纯工具回合的空文本
  不会抹掉答案）。子任务结果在 rpc 模式下以
  `entry_appended{customType: "task-result"}` 进入主线、不产生 `role: "custom"`
  的消息，所以分开它们的是顺序：子任务文本在前、主线回答在后。子任务过程保留为
  work log，只进日志与唤醒上下文。
- **聊天与唤醒事件内容不一致**：两路各拼一份，用户看到的和 agent 看到的不是
  同一段文字。现在共用同一份 `final_answer`，唤醒 note 额外注明「已推送、不要
  复述全文」。
- **`agent_settled{aborted: true}` 被报成 ✅ 完成**：现在如实报 `aborted`，并在
  聊天里给出中止通知；`cancel()` 加了终态保护，不会重复收尾。
- **无人应答的 UI 对话框把任务钉死**：以前 30 秒后放弃等待，但 pi 侧仍阻塞，
  只能等 1200 秒看门狗。现在到点主动 `cancel_ui_request`、清登记表、在聊天提示
  「该确认请求已取消」，任务继续消费事件流。UI 时限收敛到 `tasks` 的
  `UI_REPLY_TIMEOUT` 一处；`connection` 里保留的 300s 清扫只回收没人认领的请求
  登记，不拥有 UI 时限，也不参与任务判定。
- **UI 请求阻塞 prompt 的 response**：对话框未回复时 pi 不回 ack，10 秒的命令
  超时先判任务失败，用户还没看到提问。现在这种情况不再判失败，改由 runner 接住
  迟到的 response（`handled` / 失败都会正确收尾）。
- **看门狗永不触发**：全局扩展的 `setTitle/setStatus/setWidget/set_editor_text/
  notify` 通知也入队，流式期约 80ms 一次，等于持续「有进度」。现在这些噪声在入队
  前丢弃，看门狗只看真实进展。
- **运行中切会话弄丢任务**：`/pi resume`、`/pi open`、`pi_open_session`、
  `pi_resume_session` 会换掉连接与事件队列；现在任务运行期间直接拒绝并提示。
- **`/pi abort` 失败时任务卡在看门狗上**：pi 进程已死、连接异常时 `abort()` 会
  抛错，旧代码因此跳过本地 `cancel()`，任务留在 `_active`，后续切会话一律被拒，
  只能等 1200 秒超时。现在无论 `abort()` 是否成功都收尾（`cancel()` 有终态保护，
  只通知一次），命令与小模型工具两条路径行为一致。
- **交互式 prompt 被 pi `handled` 时零回复**：聊天里发 `/pi /auto` 这类由 pi 直接
  处理的命令，以前只会得到沉默。现在交互路径也会回一句「pi 直接处理了该请求，
  没有启动模型回合」，与后台任务路径的说法一致。
- **测试基线**：`tests/test_tasks.py` 三条用例仍喂 `agent_end`、
  `tests/test_connection.py::test_read_events_*` 永久挂起，均已修复；新增
  `handled` 立即收尾、`aborted` 上报、结果只取主线最终回答等回归用例。

### 变更

- **文档同步**：`openspec/changes/pi-background-task/`（proposal/design/tasks/
  spec）、`README.md`、`skills/pi-connector/SKILL.md` 全部改为「唤醒事件 + 双通道
  同一份全文」，删除 `add_message_pair` 历史注入的整段旧设计。
- **工具描述**：`pi_send_message` / `pi_run_command` 不再宣称结果会注入对话历史，
  改为说明唤醒事件与「不要复述、不要轮询」。

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
