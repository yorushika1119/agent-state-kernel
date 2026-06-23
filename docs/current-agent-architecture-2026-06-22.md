# 当前 Agent 架构说明

日期：2026-06-22

## 1. 总体定位

当前项目不是要替代 Hermes，也不是要重新实现完整 Agent Runtime。

当前架构定位是：

```text
Hermes / Host Runtime
  负责：消息历史、模型执行、工具调用、流式输出、runtime session

KMS
  负责：用户消息调度、任务路由、是否打断、是否直接回答、通知和 dispatch

Kernel
  负责：认知状态、任务状态、事件归约、可见性视图、审计
```

一句话：

```text
Hermes 负责“跑任务”，KMS 负责“调度任务”，Kernel 负责“保存和解释任务状态”。
```

## 2. 当前分层

```mermaid
flowchart TD
    User["User"]
    Hermes["Hermes Gateway / CLI"]
    Adapter["Runtime Adapter / Hermes Adapter"]
    KMS["KMS API + KmsManager"]
    Router["Task Context Router"]
    Directory["User Session + Global Task Directory"]
    Dispatch["Thinker Dispatch Queue"]
    Kernel["Kernel Engine"]
    Reducer["State Reducer"]
    Store["SQLite Derived State Store"]
    Views["manager / observer / talker / thinker / sync / debug views"]
    Notify["NotificationCoordinator"]

    User --> Hermes
    Hermes --> Adapter
    Adapter --> KMS
    KMS --> Directory
    KMS --> Router
    Router --> Directory
    KMS --> Kernel
    Kernel --> Reducer
    Reducer --> Store
    KMS --> Dispatch
    Hermes --> Dispatch
    Dispatch --> Hermes
    Dispatch --> Notify
    Store --> Views
    Notify --> Views
```

## 3. 核心模块

| 模块 | 当前职责 |
|---|---|
| Hermes Gateway / CLI | 接收用户消息，执行真实模型和工具 |
| HermesAdapter | Hermes 侧调用 Kernel/KMS 的 HTTP client |
| RuntimeEventAdapter | 通用 runtime adapter，已完成第一版 |
| KMS API | 对外提供 dispatch、thinker lifecycle、notification、view API |
| KmsManager | 用户消息调度主入口，目前仍偏大 |
| TaskRoutingCoordinator | 管理 user session、读取 global tasks、调用 router |
| Task Context Router | 判断用户消息属于哪个 task，或是否需要澄清 |
| DispatchLifecycleCoordinator | 创建 run、激活 run、创建 thinker dispatch |
| InterruptCoordinator | 处理中断当前任务、暂停 task |
| ResumeCoordinator | 恢复 paused task |
| KernelEngine | 连接 store 和 reducer，生成 views |
| State Reducer | 从事件流计算当前状态 |
| NotificationCoordinator | 根据 dispatch complete / fail 生成 observer notification |
| ConversationRefCoordinator | 统一写 task conversation refs |

## 4. 当前数据模型

| 表 / 状态 | 用途 |
|---|---|
| `user_sessions` | 用户会话，一个 user session 可以挂多个 task |
| `global_tasks` | 全局任务目录，用于多任务路由 |
| `task_context_routes` | 每次用户消息的路由审计 |
| `session_links` | kernel session 和 runtime session 的映射 |
| `task_snapshots` | task 当前快照 |
| `task_brief_states` | task-first 意图/任务概要主读模型 |
| `task_flows` | task-first 计划/流程主读模型 |
| `claim_items` | task-first claim 主读模型 |
| `todo_obligations` | task-first todo 主读模型 |
| `intent_states` | 旧架构意图状态，当前只作为历史数据读取 fallback |
| `plan_states` | 旧架构计划状态，当前只作为历史数据读取 fallback |
| `belief_items` | 旧架构 belief 状态，当前只作为历史数据读取 fallback |
| `commitments` | 旧架构 commitment 状态，当前只作为历史数据读取 fallback |
| `thinker_dispatches` | KMS 下发给 Thinker 的任务单 |
| `observer_notifications` | 通知 Observer / Talker 主动刷新或汇报 |
| `task_conversation_refs` | task 级消息摘要和 runtime message 引用，不保存完整 transcript |
| `runtime_refs` | runtime 消息、工具、结果引用索引 |

当前状态源结论：

- `task_brief_states / task_flows / claim_items / todo_obligations` 已经切为主读模型。
- `save_intent / save_plan / save_belief / save_commitment` 只写新版表。
- 旧表写入代码已经移除，不再支持恢复旧表双写。
- `intent_states / plan_states / belief_items / commitments` 暂不删除，只用于历史数据 fallback。
- `legacy_debug` 只保留在 `manager_view` 和 `debug_view`，不再暴露给 `thinker_view`。
- `GET /kms/state-source-audit` 可查看当前主读切换状态。

## 5. 用户消息主流程

```text
1. Hermes 收到用户消息
2. Hermes 调用 /kms/dispatch-user-message
3. KMS observe user_session
4. Task Router 根据 global_tasks + conversation refs 判断目标 task
5. Intent Classifier 判断：
   - 直接由 Kernel 回答
   - 继续当前 task
   - 打断当前 task，创建新 run
   - 恢复 paused task
   - 需要用户澄清
6. 如果需要 Thinker：
   - KMS 创建 run
   - 更新 task/global_task
   - 创建 thinker_dispatch
7. Hermes claim thinker_dispatch
8. Thinker 执行任务并提交事件
9. Thinker complete / fail dispatch
10. KMS 更新 run 状态，生成 notification 和 conversation ref
11. Observer/Talker 读取 observer_view / talker_view
```

## 6. 打断机制

当前打断机制是：

```text
新用户消息进入同一 user session
-> KMS 判断是否会影响当前 active task
-> 如果需要执行新任务，暂停旧 task
-> 标记旧 run stale / interrupted
-> 创建新 run 和 thinker_dispatch
-> Thinker 只能继续写 active run
```

关键点：

- 旧 run 的 stale 写入不会污染当前用户可见状态。
- 旧 task 会进入 paused，可后续恢复。
- `resume_context` 用于恢复暂停任务。
- 当前默认行为仍接近 Codex：新请求优先打断当前执行。

## 7. 多任务模型

当前已经支持：

```text
一个 user_session
  -> 多个 global_task
  -> 每个 task 有 task_id
  -> 每个 task 可以关联 kernel_session / task snapshot / conversation refs
```

当前已经进入 task-first 主存储阶段：

- `task_brief/task_flow/claim/todo` 是当前主读和默认写入模型。
- 旧 `intent/plan/belief/commitment` 表默认不再写入，只作为历史 fallback。
- `task_conversation_refs` 已经按 task 保存消息摘要和 runtime 引用。
- evidence / execution 已带 `task_id`，用于 task-local 查询隔离。

## 8. Conversation Refs 边界

设计文档明确要求 Kernel 不重复实现 message history。

所以当前 `task_conversation_refs` 保存的是：

```text
message_ref_id
text_summary
role
source
task_id
run_id
metadata
```

不保存：

```text
完整聊天 transcript
完整 reasoning
完整工具结果
runtime 私有日志
```

完整消息历史仍属于 Hermes / Host Runtime。

## 9. Views

| View | 使用者 | 当前内容 |
|---|---|---|
| `manager_view` | 管理界面 / 用户管理视角 | task brief、task flow、active task、风险、通知、dispatch、conversation refs |
| `observer_view` | Observer / Talker / 外部 UI | 可转述摘要、安全事实、未确认点、待办、阻塞原因、允许/禁止动作、conversation refs |
| `talker_view` | Chat Talker | 面向聊天表达的安全进度 |
| `thinker_view` | Thinker | task brief、task flow、claims、evidence、executions、dispatch、cancellation token |
| `sync_view` | 外部协作系统 | 最小同步摘要 |
| `debug_view` | 开发和审计 | 事件、派生状态、内部详情 |

## 10. Notification

当前已有：

```text
observer_notifications
NotificationCoordinator
GET /kms/observer/notifications
GET /kms/talker/notifications
ack / resolve
```

当前 NotificationCoordinator 已负责：

- dispatch completed -> `task_done` 或 `progress_update`
- dispatch failed -> `task_failed`
- stale dispatch 不生成 notification
- 根据通知类型套用 urgency / priority / delivery policy

当前已完成第一版：

- notification 去重
- min interval 节流
- priority 策略表
- delivery policy
- SSE 轻量 stream

还未完成：

- 独立后台 push broker
- WebSocket
- 多次失败后自动升级为 `interrupt` 这类复杂 urgency 升级策略

## 11. 当前已稳定验证的能力

这些能力已经完成并通过测试：

| 能力 | 状态 |
|---|---|
| 用户消息 dispatch | 已完成 |
| 打断当前 run | 已完成 |
| stale run 防污染 | 已完成 |
| paused task 恢复 | 已完成 |
| user session + global task directory | 已完成 |
| Task Router | 已完成第一版 |
| LLM Router | 已接入，可开关 |
| thinker_dispatch claim / heartbeat / complete / fail | 已完成 |
| Hermes Gateway/CLI dispatch 生命周期 | 已完成第一版 |
| proxy mode dispatch 生命周期 | 已完成第一版 |
| manager_view / observer_view | 已完成第一版 |
| observer/talker notification API | 已完成第一版 |
| NotificationCoordinator | 已完成第一版 |
| task conversation refs | 已完成第一版 |
| 新状态表主读和写入 | 已完成 |
| 旧状态表写入退场 | 已完成，integration 和真实 smoke 通过 |
| 旧表 fallback 使用审计 | 已完成第一版，含查看脚本，本轮真实链路未命中 |
| Runtime Event Adapter Hermes 工具事件入口 | 已完成第一版 |
| 真实 Hermes kernel dispatch 工具事件 helper | 已完成第一版 |
| 测试分层 fast / core / integration / full | 已完成 |
| 真实 Router smoke | 已通过 |
| 真实 Hermes interrupt smoke | 已通过 |

## 12. 当前工作区状态

当前主线改动已经按阶段提交。具体是否还有本地未提交内容，以 `git status` 为准。

近期已完成并提交的内容：

| 内容 | 状态 |
|---|---|
| 外部 Observer/Talker 最终回复 conversation ref 回传 | 已完成第一版 |
| ConversationRefCoordinator | 已完成第一版 |
| RuntimeEventAdapter 通用封装 | 已完成第一版 |
| Observer / Talker notification SSE | 已完成第一版 |
| Notification policy 去重 / 节流 / 优先级 | 已完成第一版 |
| StateSourceAudit 主从切换审查 | 已完成第一版 |
| legacy_debug 收窄到 manager/debug | 已完成 |
| 旧状态表写入退场 | 已完成 |
| 旧表 fallback 使用审计 | 已完成第一版 |
| KmsManager dispatch decision 小拆分 | 已完成第一版 |
| KmsManager task dispatch planner 小拆分 | 已完成第一版 |
| Runtime Event Adapter Hermes 事件方法 | 已完成第一版 |
| 旧表物理删除 removal-check | 已完成第一版 |
| 测试分层 | 已完成 |
| 移除旧表写入后的 integration / real smoke | 已通过 |

这些仍然是渐进式实现，不代表 Runtime 生态已经全部接完。

## 13. 仍未完成的主要差距

| 差距 | 说明 |
|---|---|
| KmsManager 仍偏大 | 返回对象和 task dispatch planner 已拆出，但 dispatch 主流程还可继续拆 |
| Runtime Event Adapter 深度接入 Hermes | 工具/summary/raw result 方法已有，真实 Hermes 共享 helper 已补，Gateway 主流程还可继续逐步接入 |
| Observer notification WebSocket | SSE 第一版已完成，WebSocket 未做 |
| Notification 高级优先级策略 | 第一版策略表已完成，复杂升级策略未做 |
| 旧表读取 fallback | 仍保留，用于历史 DB 兼容；已有命中审计 |
| 旧表物理删除 | 未做，需要最后评估 |

## 14. 当前完成度与剩余风险

粗略完成度：

| 模块 | 完成度 | 说明 |
|---|---:|---|
| KMS / Kernel / Thinker 分层 | 92% | 职责基本清晰，dispatch decision 和 task dispatch planner 已从 manager 拆出 |
| 打断与恢复 | 90% | integration 和真实 Hermes smoke 通过 |
| Thinker dispatch 生命周期 | 85% | claim / heartbeat / complete / fail 已接通 |
| Task Router 多任务路由 | 75% | 支持常见指代，LLM Router 已接入 |
| Kernel 直接回答状态问题 | 80% | progress / evidence / failures / claims / todos 已支持 |
| User Session 多任务管理 | 80% | user_sessions / global_tasks / conversation refs 已有 |
| Observer / Manager / Notification | 65% | API / SSE / policy 第一版可用 |
| 新状态表迁移 | 90% | 新表主读、写入代码已切到新表 |
| 旧表退场 | 86% | 写入代码已移除，fallback 已审计且有查看脚本，removal-check 已有，物理删除未完成 |
| 测试体系 | 80% | fast / core / integration / full 已分层 |

当前没有发现完成不了的硬阻塞。剩余主要是收尾、加固和产品化。

真正需要谨慎的风险：

- LLM Router 无法保证 100% 不误判，需要保留低置信度澄清和回归样例。
- 旧表读取 fallback 不能贸然删除，虽然本轮真实链路未命中，但真实历史 DB 可能仍有未迁移数据。
- Observer/Talker 产品层还需要根据真实 UI/协议继续打磨。

## 15. 不建议立刻做的事

暂时不建议：

- 删除旧 `intent/plan/belief/commitment` 表；
- 把完整 message history 存进 Kernel；
- 让 Kernel 接管 Hermes 的 runtime session DB；
- 让 Observer 绕过 `observer_view` 直接读 debug/raw state；
- 为了新命名强行大迁移旧数据。

当前更稳的方向是：

```text
继续渐进式换骨：
先补 task-local refs / views / coordinator
再拆 KmsManager
再完善通知推送
最后再逐步移除旧表兼容依赖
```
## 15. 2026-06-23 本轮架构更新

本轮没有改变系统的大方向，仍然遵守：

```text
Talker / Hermes 面向用户
KMS 负责调度和任务选择
Kernel 负责状态和视图
Thinker 负责真实执行
```

新增和调整：

| 模块 | 变化 | 作用 |
|---|---|---|
| `DispatchPreparationCoordinator` | 从 `KmsManager` 拆出 | 先准备 user session、task route、target session、intent flags |
| `KmsManager` | 主流程变薄 | 不再直接塞满路由和意图判断细节，继续作为 KMS 总控 |
| Hermes Gateway `_submit_kernel_event()` | 改为调用共享 helper | Gateway/CLI 后续共用同一套 runtime event 上报逻辑 |
| `hermes_cli.kernel_dispatch.async_submit_runtime_event()` | 支持 stale run 返回 `None` | 旧 run 被打断后不会污染当前 run，Gateway 仍得到 `False` |

当前效果：

- 用户消息进来后，KMS 先做“准备判断”，再决定是否直接由 Kernel 回答、澄清、继续、恢复、打断或创建 thinker dispatch。
- Hermes Gateway 的工具事件、推理摘要、原始结果、任务完成/失败仍从原入口发出，但底层统一走 `kernel_dispatch` helper。
- 调度逻辑没有下沉到 Kernel，符合架构设计文档。
## 16. 2026-06-23 DispatchExecution 拆分

本轮继续把 `KmsManager` 从“大函数总管”改成“薄总控”。

新增模块：

| 模块 | 位置 | 职责 |
|---|---|---|
| `DispatchExecutionCoordinator` | KMS 层 | 创建/激活 run，提交用户消息，创建/同步 task，创建 thinker dispatch |

当前主流程变成：

```text
Hermes 收到用户消息
-> KMS DispatchPreparation 先判断用户意图和任务路由
-> KmsManager 选择分支
-> 如果需要 Thinker，交给 DispatchExecution 执行调度
-> Kernel 只接收事件和状态更新
-> Hermes/Thinker claim dispatch 并执行
```

这一步没有改变架构边界：

- KMS 仍然负责调度。
- Kernel 仍然负责状态和视图。
- Thinker 仍然负责执行。
- Talker/Observer 仍然读取 KMS/Kernel 给出的可见结果。
## 17. 2026-06-23 DispatchResponse 拆分

本轮继续让 `KmsManager` 变薄，新增 `DispatchResponseCoordinator`。

当前 KMS 用户消息主流程：

```text
DispatchPreparationCoordinator
  -> 先判断 user session / task route / intent flags

KmsManager
  -> 只选择走哪个分支

DispatchResponseCoordinator
  -> 处理澄清、Kernel 直接回复、no-resume 回复

DispatchExecutionCoordinator
  -> 处理需要 Thinker 的 run/task/dispatch 创建
```

模块职责：

| 模块 | 职责 |
|---|---|
| `DispatchPreparationCoordinator` | 读状态，准备判断 |
| `DispatchResponseCoordinator` | 包装不需要 Thinker 的 KMS 回复 |
| `DispatchExecutionCoordinator` | 执行需要 Thinker 的调度 |
| `KmsManager` | 串起这些 coordinator，保持总控 |

架构边界没有变化：

- KMS 负责调度和回复包装。
- Kernel 负责状态和视图。
- Thinker 只执行 dispatch。
- Talker/Observer 只消费 KMS/Kernel 暴露的结果。
## 18. 2026-06-23 Dispatch 目录分组

`src/kms` 根目录已经不再继续散放 dispatch 相关模块，当前统一为：

```text
src/kms/dispatch/
  decision.py
  preparation.py
  response.py
  execution.py
  lifecycle.py
  thinker_dispatch.py
```

这只是目录整理，不改变职责：

| 层 | 职责 |
|---|---|
| KMS dispatch package | 用户消息调度、回复包装、run/task/dispatch 编排 |
| Kernel | 状态、事件、视图 |
| Thinker / Hermes | 执行模型和工具 |
| Talker / Observer | 展示和通知 |
## 19. 2026-06-23 Routing 目录分组

KMS 的任务路由模块已经统一到：

```text
src/kms/routing/
  task_routing.py
  task_context_router.py
```

职责不变：

| 模块 | 职责 |
|---|---|
| `routing/task_routing.py` | observe user session，读取 global tasks，调用 router |
| `routing/task_context_router.py` | 根据任务目录、routing hints、LLM/router 规则选择目标 task |

这一步只是 KMS 内部目录整理，不改变 Kernel / Thinker / Talker 的边界。
