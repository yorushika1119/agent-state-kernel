# 当前项目与新版架构文档差距分析

日期：2026-06-18

对照文件：`C:\program1\Runtime-side Agent State Kernel 功能设计.md`

## 核心结论

当前项目没有偏离原始方向，但已经落后于新版设计文档的抽象层级。

现在的实现基本是：

```text
runtime_session_id -> kernel_session_id -> active_task_id -> run_id
```

新版设计要求的是：

```text
user_session -> Task Context Router -> global task -> kernel task session -> thinker dispatch
```

也就是说，当前项目已经能证明“Kernel 可以保存状态、KMS 可以判断是否打断、可以恢复暂停任务”，但还没有真正具备新版文档强调的“全局任务目录 + 多任务路由 + 外部观察者通知 + Thinker dispatch 队列”能力。

## 关于“是否必须先读 Kernel 才能判断”

是的，很多问题必须先读 Kernel，再决定动作。

当前 `intent_classifier` 主要根据用户文本和轻量 session summary 判断：

- 是不是状态查询；
- 是不是新任务；
- 是不是继续之前任务；
- 是不是同任务补充；
- 是不是不确定。

这对简单句子够用，但对真实交互不够。因为很多用户输入的含义取决于 Kernel 里有没有对应状态。

例如：

| 用户输入 | 只看文本的判断 | 必须读 Kernel 后才能判断 |
|---|---|---|
| “现在能直接告诉我结果吗？” | 可能是状态查询 | 要看是否已有 verified claim / final progress |
| “有什么依据？” | evidence 查询 | 要看 evidence 是否存在、是否可见 |
| “继续刚才那个” | resume | 要看是否有 paused task，以及“刚才那个”指哪个 task |
| “不用做了” | 可能取消 | 要看当前是否有 active task，还是普通闲聊 |
| “先别发，给我看草稿” | 同任务修改 | 要看当前任务是否涉及发送动作、是否已有 draft |
| “另一个调研继续做” | resume/switch | 要看 global task directory 里的候选任务 |

所以后续应该把 KMS 调度拆成两段：

```text
1. 粗分类：用户这句话大概属于 query / task_control / new_task / chat / uncertain
2. 状态路由：读取 Kernel 的 user session、global_tasks、active task、paused task、progress、evidence、claim、todo
3. 最终决策：respond_from_kernel / create_task / switch_task / resume_task / interrupt_and_replan / ask_clarification
```

也就是：模型不是直接给最终 action，而是先给候选解释；最终动作由 KMS 结合 Kernel 状态仲裁。

## 当前已经完成的能力

### 1. KMS 统一写入入口已有雏形

当前已有 `/kms/request` 和 `KernelEngine.submit_event()`，Thinker / Talker 事件会进入 KMS pipeline，再写入 event log 和 derived state。

这符合新版文档中“KMS 是唯一解释层和写入仲裁层”的原则。

### 2. 事件日志和派生状态已有基础

当前已有：

- `cognitive_events`
- `intent_states`
- `plan_states`
- `evidence_items`
- `belief_items`
- `execution_actions`
- `progress_states`
- `runtime_refs`
- `session_links`

这证明事件源和 reducer 的主线已经存在。

### 3. 状态查询不打断 Thinker 已验证

当前 `dispatch_user_message()` 已支持：

- 查询 progress / failures / evidence / run / resumable task；
- 如果能从 Kernel 回答，则 `requires_thinker=False`；
- 不打断当前 active run。

这正是新架构相对纯 Hermes Thinker 的核心优势之一。

### 4. 中途打断和恢复暂停任务已验证

当前已经能：

- 新请求打断 active run；
- 快照当前 active task；
- 后续“继续刚才”恢复 paused task；
- stale run completion 被拒绝。

这已经覆盖了“实施打断”的基础闭环。

### 5. LLM fallback 已有接口

当前 `intent_classifier` 已有规则 fast path + DeepSeek fallback。

这说明 KMS 可以逐步从规则判断升级为“规则约束 + 小模型理解 + Kernel 状态仲裁”。

## 与新版设计的主要差距

### 差距 1：缺少 User Session

新版设计明确区分：

```text
User Session
Kernel Task Session
```

当前项目只有 `session_links`，更像是 `kernel_session_id` 与 `runtime_session_id` 的绑定。

问题是：

- 普通聊天也会被当成可能创建 kernel session；
- 一个 runtime session 默认只取最近一个 kernel session；
- 同一用户会话内多个任务的路由能力不足；
- 无法表达“这个 user session 绑定了多个 task，当前 active task 是哪个”。

建议新增：

- `UserSession` schema；
- `user_sessions` 表；
- `observe_user_session()`；
- `/kernel/user-sessions/observe`；
- `linked_task_ids` / `active_task_id`。

### 差距 2：缺少 Global Task Directory

当前有 `task_snapshots`，但它只属于某个 `kernel_session_id`。

新版设计需要全局任务目录，用来回答：

- 用户有哪些活跃任务；
- “刚才那个”“另一个调研”“A 公司那个”指哪个任务；
- 哪些任务需要确认；
- 哪些任务阻塞或失败；
- 哪个任务最近被用户触达。

当前 `TaskSnapshot` 缺少新版文档要求的关键字段：

- `user_id`
- `agent_id`
- `task_type`
- `task_description`
- `task_brief_version`
- `priority`
- `stage`
- `last_user_touch_at`
- `last_activity_at`
- `last_manager_update_at`
- `last_talker_update_at`
- `last_thinker_update_at`
- `external_refs`
- `routing_hints`

建议不要直接把 `task_snapshots` 改成全局目录。更稳妥的是新增 `global_tasks`，让旧的 `task_snapshots` 暂时作为 task 内部恢复快照保留。

### 差距 3：缺少 Task Context Router

当前 `KmsManager._find_target_session()` 只做：

```text
target_session_id 优先
否则 runtime_session_id 找最近 session
```

这不是新版设计里的任务路由。

新版 Task Context Router 应输出：

- `routing_decision`
- `target_task_id`
- `confidence`
- `matched_hints`
- `time_reason`
- `candidate_tasks`
- `needs_user_clarification`
- `clarification_question`

当前系统无法可靠处理：

- “另一个调研继续做”；
- “不是这个，是昨天那个候选人筛选”；
- “把发邮件那个任务取消”；
- “这件事也加到同一个任务里”。

建议新增：

- `src/kms/task_context_router.py`
- `TaskRouteDecision`
- `store.list_global_tasks_for_user()`
- `store.save_task_context_route()`

第一版 router 可以先用规则 + 简单打分，不必一开始就复杂。

### 差距 4：当前意图识别和状态读取顺序不合理

当前流程是：

```text
找 session -> classify_dispatch_intent_with_llm(text, session) -> 根据 intent 决策
```

问题是 classifier 只拿轻量 session summary，不知道：

- evidence 是否为空；
- failure 是否存在；
- paused task 有几个；
- active task 的 title / routing hints；
- 是否已有 final progress；
- 是否有 pending confirmation；
- 是否有多个候选任务。

建议调整为：

```text
User message
  -> observe user session
  -> load routing context from Kernel
  -> rough intent classification
  -> Task Context Router
  -> state-aware capability check
  -> dispatch decision
```

这里的关键是新增一个 `KernelDispatchContext`，把 KMS 做决策需要的状态集中读出来。

### 差距 5：状态模型命名还停留在旧版

新版文档把状态类型改为：

- `task_brief`
- `task_flow`
- `evidence`
- `claim`
- `todo`
- `progress`
- `runtime_ref`

当前实现仍是：

- `intent_states`
- `plan_states`
- `belief_items`
- `commitments`
- `execution_actions`

这些不是完全错误，但语义不完全贴合新版文档。

建议短期先兼容，不急着大改表：

| 新版概念 | 当前近似实现 | 建议 |
|---|---|---|
| task_brief | intent_states | 新增 task_brief_states，先由 intent_states 迁移 |
| task_flow | plan_states + execution_actions | 新增 task_flows，先记录关键轨迹节点 |
| claim | belief_items | 后续改名或新增 claim_items |
| todo | commitments | 新增 todo_obligations，commitments 可逐步并入 |
| progress | progress_states | 保留 |
| runtime_ref | runtime_refs | 保留 |

我不建议现在立刻删除旧表，因为测试和 pipeline 已经依赖它们。

### 差距 6：缺少 notification / wakeup orchestrator

新版设计里，KMS 不只是回应用户，还要主动：

- 通知 Talker 任务完成；
- 通知用户需要确认；
- 标记任务阻塞；
- 创建 ThinkerDispatch 唤醒后台 worker。

当前项目没有：

- `talker_notifications`
- `observer_notifications`
- `thinker_dispatches`
- `/kms/thinker/dispatches/claim`
- dispatch heartbeat / complete / fail

现在的 Hermes 集成更像是直接返回一个 `run_id`，让外部 runtime 自己继续跑。

建议先实现最小 `thinker_dispatches`：

```text
dispatch_id
task_id
kernel_session_id
task_brief_version
dispatch_type
status
cancellation_token
created_at
claimed_at
completed_at
```

2026-06-22 后续状态：

- `thinker_dispatches` 的 claim / heartbeat / complete / fail 已完成。
- Gateway / CLI / Gateway proxy mode 已接入 dispatch 生命周期。
- `observer_notifications` 最小表和 Observer / Talker notification API 已完成。
- 当前还没有独立 notification policy coordinator，通知策略仍是 dispatch complete / fail 的最小触发。

### 差距 7：同任务 steer 还不是真正的 steer

当前 `same_task_steer` 只是避免走“新任务打断”的部分逻辑，但仍然会创建新 run，并通过 raw message 更新 intent / plan。

新版设计更接近 Codex 的 steer：

- 同一个 active turn / run 接收补充指令；
- 带 expected run / turn id；
- 不一定中断整个任务；
- Thinker 需要检查 task_brief_version 或 dispatch version。

建议先增加结构化 `TaskBriefUpdated` 事件，再让 Hermes worker 在每步前检查 `intent_version/task_brief_version`。

### 差距 8：缺少 observer / manager view 的区分

当前已有：

- talker view
- thinker view
- sync view
- debug view

新版设计还需要：

- manager view
- observer view

当前 `talker_view` 更像用户可见进度，`thinker_view` 是执行视图，但没有专门给管理台或外部观察者的结构化视图。

建议：

- `manager_view`：包含任务目录、状态、阻塞、待确认、风险；
- `observer_view`：比 talker 结构化，但不暴露内部 debug；
- `talker_view`：只保留可表达的自然语言安全状态。

## 推荐改进顺序

### 第一阶段：修正 KMS 决策上下文

目标：解决“必须读 Kernel 才能判断”的问题。

建议做：

1. 新增 `KernelDispatchContext`；
2. 在 `dispatch_user_message()` 中先加载 progress / evidence / failures / tasks / active task；
3. 把 context 传给 classifier 和最终仲裁；
4. 增加测试：同一句话在有 evidence 和无 evidence 时，决策结果不同；
5. 增加测试：多个 paused task 时，“继续刚才”不能盲目取最后一个，需要路由或澄清。

### 第二阶段：引入 User Session 和 Global Task Directory

目标：从单 session 调度升级为多任务路由。

建议做：

1. 新增 `UserSession`、`GlobalTask` schema；
2. 新增 `user_sessions`、`global_tasks` 表；
3. 新增 `/kernel/user-sessions/observe`；
4. 当前 `/kms/dispatch-user-message` 兼容旧参数，但内部转成 user session observe；
5. 创建 task 时同步写入 global task。

### 第三阶段：实现 Task Context Router

目标：让“刚才那个 / 另一个 / A 公司那个”可测试。

建议做：

1. 新增 `TaskRouteDecision`；
2. 基于 `routing_hints`、时间字段、状态做简单打分；
3. 低置信度返回 `ask_clarification`；
4. LLM 只参与候选任务解释，不直接写状态；
5. 记录 `task_context_routes` 便于复盘。

### 第四阶段：任务状态命名向新版收敛

目标：降低概念偏差。

建议做：

1. 新增 `task_brief_states`，先映射旧 `intent_states`；
2. 新增 `task_flows`，先映射 plan/execution 关键节点；
3. 新增 `claim_items`，逐步替代 `belief_items`；
4. 新增 `todo_obligations`，逐步替代 `commitments`；
5. 保持旧 API 暂时可用，避免一次性大迁移。

### 第五阶段：补 notification / thinker dispatch

目标：KMS 能真正唤醒 Thinker，而不是只把 run_id 返回给外部。

建议做：

1. 新增 `thinker_dispatches` 表；
2. 新增 claim / heartbeat / complete / fail API；
3. dispatch 带 `task_id`、`task_brief_version`、`cancellation_token`；
4. Hermes adapter 改为领取 dispatch；
5. stale dispatch 完成时拒绝写入用户可见进度。

## 下一步最适合做什么

我建议下一步先做第一阶段：`KernelDispatchContext`。

原因：

1. 它正好回答你现在的疑问；
2. 改动范围小，不需要先做大迁移；
3. 能立刻提升复杂指令判断；
4. 后续 Task Context Router 可以复用这个上下文；
5. 可以用现有测试快速验证。

第一阶段最小验收标准：

- 状态查询是否可回答，不只看文本，还看 Kernel 是否有对应状态；
- evidence 为空时，不伪装成“可完整回答”，只返回“当前还没有可用证据”；
- 多个 paused task 时，KMS 返回澄清或明确候选，而不是默认继续最后一个；
- active task 的标题、goal、progress 会进入 LLM 分类 prompt；
- 测试覆盖复杂自然语言，例如：
  - “先别打断它，我只是想看看现在有没有依据能说”
  - “那个失败是哪个工具导致的”
  - “刚才那个先放着，另一个调研继续”
  - “这不是新任务，只是把输出改成表格”

## 2026-06-18 第一阶段完成情况

已完成 `KernelDispatchContext` 的最小实现。

新增文件：

- `src/kms/context/dispatch_context.py`

当前 KMS 在 `dispatch_user_message()` 里会先读取 Kernel 状态，再做意图分类：

```text
find session
  -> build KernelDispatchContext
  -> classify_dispatch_intent_with_llm(text, session, context)
  -> dispatch decision
```

上下文目前包含：

- session；
- intent；
- plan；
- progress；
- tasks；
- evidence；
- executions；
- active task；
- paused tasks；
- failed executions；
- progress 是否有可表达内容。

这解决了一个关键问题：KMS 现在不只是根据 Talker 文本猜测，而是能把 Kernel 中已有的状态作为判断依据。

本次同步调整：

- `intent_classifier` 支持 `context` 参数；
- LLM prompt 使用 `Kernel dispatch context`，包含 active task、证据数、失败工具数、暂停任务数、进度摘要；
- 没有 Kernel 上下文时，不调用 LLM 猜第一条消息；
- 增加“依据 / 手头依据 / 依据够不够”等状态查询表达；
- `KmsManager` 在调度前加载 context；
- 补充复杂证据查询不打断当前 run 的测试；
- 补充 manager 确实把已加载 Kernel context 传给 classifier 的测试。

验证结果：

```text
python -m pytest -q tests/test_intent_classifier.py tests/test_requested_user_scenarios.py
10 passed

python -m pytest -q tests/test_intent_classifier.py tests/test_requested_user_scenarios.py tests/test_smoke_interrupt.py tests/test_architecture_ab_experiment.py tests/test_pipeline_event_flow.py
39 passed
```

下一步建议进入第二阶段：新增 `user_sessions` 与 `global_tasks`，为 Task Context Router 做准备。

## 2026-06-18 第二阶段完成情况

已完成 `user_sessions`、`global_tasks`、`task_context_routes` 的最小落地。

新增/修改：

- `src/schema/state.py`
  - 新增 `UserSession`
  - 新增 `GlobalTask`
  - 新增 `TaskRouteDecision`
- `src/stores/sqlite_store.py`
  - 新增 `user_sessions`
  - 新增 `global_tasks`
  - 新增 `task_context_routes`
  - 新增 user session observe/get/link 方法
  - 新增 global task upsert/get/list 方法
  - 新增 route decision 保存方法
- `src/kms/task_context_router.py`
  - 新增最小 Task Context Router
  - 支持 routing hints 命中
  - 支持“刚才 / 上一个 / 之前”等最近任务引用
  - 支持“另一个 / 不是这个”等第二候选任务引用
  - 多候选低置信时返回 `ask_clarification`
- `src/kms/manager.py`
  - dispatch 开头自动 observe user session
  - 保存 Task Context Router 决策
  - 创建 / 暂停 / 恢复 task 时同步写 `global_tasks`
  - 把 task 链接到 user session
- `src/kernel/engine.py`
  - `complete_run()` 后同步更新 global task 状态
- `src/api/server.py`
  - 新增 `POST /kernel/user-sessions/observe`
  - 新增 `GET /kernel/user-sessions/{user_session_id}`
  - `/kms/dispatch-user-message` 支持并返回 `user_session_id`

新增测试：

- `tests/test_task_directory_router.py`

验证结果：

```text
python -m pytest -q tests/test_task_directory_router.py tests/test_intent_classifier.py tests/test_requested_user_scenarios.py tests/test_smoke_interrupt.py tests/test_architecture_ab_experiment.py tests/test_pipeline_event_flow.py
42 passed
```

当前边界：

- Task Context Router 目前只是最小规则版，还没有接入 LLM 对候选任务做语义路由。
- Router 已保存决策，但 `KmsManager` 仍主要沿用旧的 runtime session 调度行为，没有完全改成由 router 决定目标 kernel task session。
- `global_tasks` 已经可用，但旧的 `task_snapshots` 仍保留为恢复快照。

下一步建议进入第三阶段：拆分 `KmsManager` 职责，把 `KernelDirectResponder`、`InterruptCoordinator`、`ResumeCoordinator` 抽出来，避免 manager 继续变胖。

## 2026-06-18 第三阶段完成情况

已完成 `KmsManager` 的低风险职责拆分。

新增：

- `src/kms/response/kernel_direct_responder.py`
  - 负责从 Kernel 状态直接构造回复；
  - 覆盖 progress / failures / evidence / resume / run；
  - 不唤醒 Thinker。
- `src/kms/task/coordinators.py`
  - `InterruptCoordinator`
    - 确保 active task 存在；
    - 打断前快照当前 task；
    - 将当前 task 标记为 paused；
  - `ResumeCoordinator`
    - 构造 resume context；
    - 把 paused task 恢复进 session 的 intent / plan。

`KmsManager` 当前保留统一调度入口，但把以下职责委托出去：

```text
KmsManager
├── KernelDispatchContext
├── IntentClassifier
├── TaskContextRouter
├── KernelDirectResponder
├── InterruptCoordinator
└── ResumeCoordinator
```

验证结果：

```text
python -m pytest -q tests/test_task_directory_router.py tests/test_intent_classifier.py tests/test_requested_user_scenarios.py tests/test_smoke_interrupt.py tests/test_architecture_ab_experiment.py tests/test_pipeline_event_flow.py
42 passed
```

第三阶段结束时的边界：

- `KmsManager` 仍然是唯一总调度入口；
- Task Context Router 还没有完全接管目标 task session 选择；
- 状态表仍然以旧表为主，下一步才迁移到 `task_brief / task_flow / claim / todo` 命名；
- 还没有实现 `thinker_dispatches` claim/heartbeat/complete/fail。

## 2026-06-18 第四、第五阶段完成情况

已完成“状态命名兼容层”和最小 `thinker_dispatches` worker 协议。

这次没有删除旧表，也没有推倒 reducer。实现方式是：保留旧状态表作为当前事实来源，同时新增新版命名的影子表，让上层可以开始使用新版架构词汇。

新增状态兼容表：

- `task_brief_states`
  - 兼容映射自 `intent_states`
  - 对应新版 `task_brief`
- `task_flows`
  - 兼容映射自 `plan_states`
  - 同步最近 execution 摘要
  - 对应新版 `task_flow`
- `claim_items`
  - 兼容映射自 `belief_items`
  - 对应新版 `claim`
- `todo_obligations`
  - 兼容映射自 `commitments`
  - 对应新版 `todo`

新增 Thinker Dispatch：

- 新增 `thinker_dispatches` 表；
- `dispatch_user_message()` 在需要 Thinker 执行时自动创建 dispatch；
- `/kms/dispatch-user-message` 返回 `thinker_dispatch_id`；
- `src/adapters/hermes_adapter.py` 增加 claim / heartbeat / complete / fail 便捷方法；
- 新增 worker API：
  - `GET /kms/thinker/dispatches`
  - `POST /kms/thinker/dispatches/claim`
  - `POST /kms/thinker/dispatches/{dispatch_id}/heartbeat`
  - `POST /kms/thinker/dispatches/{dispatch_id}/complete`
  - `POST /kms/thinker/dispatches/{dispatch_id}/fail`

视图更新：

- `thinker_view` 增加：
  - `task_brief`
  - `task_flow`
  - `claims`
  - `todos`
  - `thinker_dispatches`
- `debug_view` 增加同样字段。
- 旧字段 `intent / plan / beliefs / commitments` 仍然保留。

本次还修正了一个 action 语义边界：

- 显式新任务，例如 `mode=new_task` 或“这是一个新任务”，仍返回 `start_new_task`；
- 普通新请求导致旧 run 被替换时，对外返回 `interrupt_and_replan`；
- 内部 `task_action` 仍可表达是否创建了新 task。

新增测试：

- `tests/test_state_alias_and_thinker_dispatch.py`
  - 验证新版状态影子表会跟随旧状态写入；
  - 验证 `thinker_dispatches` 可以 claim / heartbeat / complete；
  - 验证 dispatch complete 后会清理 active run。

已验证：

```text
python -m pytest -o addopts='' tests/test_state_alias_and_thinker_dispatch.py -q
2 passed

python -m pytest -o addopts='' tests/test_task_directory_router.py -q
3 passed

python -m pytest -o addopts='' tests/test_smoke_interrupt.py -q
8 passed

python -m pytest -o addopts='' tests/test_pipeline_event_flow.py -q
18 passed

python -m pytest -o addopts='' tests -q
52 passed
```

当前边界：

- `task_brief / task_flow / claim / todo` 还是兼容影子层，不是最终主表；
- `thinker_dispatches` 已经有最小 worker 协议，项目内 `HermesAdapter` 已有 helper，但真实 Hermes Gateway/CLI 还没有改成“先 claim dispatch 再执行”；
- Task Context Router 仍没有完全接管目标 task session 选择；
- notification / wakeup orchestrator 还没有独立模块。

下一步建议：

1. 让 Hermes Gateway/CLI 优先使用 `thinker_dispatch_id` 或 claim API；
2. 给 dispatch 增加 stale dispatch 输出抑制测试；
3. 再考虑把 Router 的 `target_task_id` 真正接入 `KmsManager` 的目标任务选择；
4. 最后再做旧表到新版表的主从切换。
## 2026-06-18 第六阶段完成情况

本阶段完成了真实 Hermes Gateway/CLI 与 KMS `thinker_dispatches` 生命周期的接通。

核心变化：
- Kernel/KMS：
  - `POST /kms/thinker/dispatches/claim` 支持按 `dispatch_id` 精确领取，避免多个 pending dispatch 串单。
  - dispatch `complete/fail` 在旧 run 已 stale 时仍会收尾 dispatch 状态，但不会清掉当前 active run。
  - `HermesAdapter.claim_thinker_dispatch()` 支持传入 `dispatch_id`。
  - intent classifier 增加工作请求规则，已有 active session 时 `research/analyze/implement/write/build ...` 这类请求会稳定判为 `new_task`，避免测试和运行时依赖 LLM 随机判断。
- 真实 Hermes 部署目录 `C:\Users\EDY\AppData\Local\hermes\hermes-agent`：
  - 新增 `hermes_cli/kernel_dispatch.py`，统一封装 claim / heartbeat / complete / fail。
  - Gateway 在本地执行 agent 前会 claim + heartbeat `thinker_dispatch_id`。
  - Gateway 执行结束后，如果有 `thinker_dispatch_id`，用 dispatch complete/fail 替代旧 `/kms/complete-run`。
  - CLI 在真正执行 thinker 前 claim + heartbeat dispatch。
  - CLI 执行结束后，如果有 active dispatch，优先走 dispatch complete/fail；没有 dispatch 时才回退旧 `/kms/complete-run`。
  - CLI 已修正 queued dispatch 不能覆盖当前 active dispatch，避免打断时旧 run 收尾错用新 dispatch。

保持不变：
- `respond_from_kernel` 仍然直接从 Kernel 回答，不唤醒 Thinker。
- `interrupt_and_replan` / `resume_context` 语义保持不变。
- 旧状态表和旧 `/kms/complete-run` 仍保留，作为兼容路径。

新增/更新测试：
- `tests/test_state_alias_and_thinker_dispatch.py`
  - 精确 claim dispatch。
  - stale dispatch fail 不清掉新 active run。
- `tests/test_intent_classifier.py`
  - 工作请求规则不调用 LLM。
- Hermes Gateway：
  - claim dispatch 后 heartbeat。
  - dispatch complete 替代旧 complete-run。
  - interrupted result 会 fail dispatch。
- Hermes CLI：
  - active dispatch 保存和清理。
  - queued dispatch 不覆盖 active dispatch。
  - claim / heartbeat / complete / fail 生命周期。

已验证：

```text
python -m pytest -o addopts='' C:\program1\agent-state-kernel\tests -q
55 passed

python -m pytest -o addopts='' C:\Users\EDY\AppData\Local\hermes\hermes-agent\tests\gateway\test_busy_session_ack.py -q
32 passed, 1 skipped

python -m pytest -o addopts='' C:\Users\EDY\AppData\Local\hermes\hermes-agent\tests\gateway\test_interrupt_demo_output.py -q
1 passed

python -m pytest -o addopts='' C:\Users\EDY\AppData\Local\hermes\hermes-agent\tests\cli\test_cli_init.py -q
59 passed
```

当前边界：
- proxy mode 的 Gateway 仍沿用原路径，本轮只接通本地真实 Gateway/CLI 执行链路。
- Task Context Router 仍主要记录 route decision，还没有完全主控目标 task 选择。
- `task_brief/task_flow/claim/todo` 仍是兼容影子层，未替换旧主表。

下一步建议：
1. 做真实本地联调：启动 kernel，再用真实 Hermes CLI/Gateway 跑一次“旧任务执行中 -> 用户新请求打断 -> 新 dispatch claim -> 新回答返回”。
2. 把 Task Context Router 的 `target_task_id` 接入 `KmsManager` 的目标 task 选择。
3. 再考虑 proxy mode dispatch 生命周期。
4. 最后再做旧状态表到新命名表的主从切换。

## 2026-06-18 第七阶段完成情况

本阶段完成了 Task Context Router 从“只记录 route decision”到“实际接管目标 task 选择”的第一步。

核心变化：
- `KmsManager` 现在会读取 `route_task_context()` 返回的 `target_task_id`。
- 当 router 明确选中已有 task，且用户消息不是明确新任务 / resume 指令时，KMS 会切换到该 task。
- 如果当前有 active task，KMS 会先把当前 task 快照为 paused，再恢复 router 选中的 task。
- ambiguous route 会直接返回 `respond_from_kernel` 澄清问题，不唤醒 Thinker，也不打断当前 active run。
- `respond_from_kernel`、`resume_context`、`interrupt_and_replan` 的旧语义保持兼容。
- 修复了 `SemanticConflictJudge` 的局部 `EventType` import 作用域问题。
- 给 `BeliefReviewJudge` 和 `Gate` 增加了很窄的离线规则，避免 DeepSeek key 无效时核心安全测试不稳定。

新增测试：
- router 选中旧 task 后，KMS 真的切回该 task，并暂停当前 task。
- router 多候选低置信时，只问用户澄清，不打断当前 run。
- “继续刚才的任务”仍走旧的 paused task resume 语义，不被 router 的“最近任务”误抢。

已验证：

```text
python -m pytest -o addopts='' C:\program1\agent-state-kernel\tests -q
57 passed

python -m pytest -o addopts='' C:\Users\EDY\AppData\Local\hermes\hermes-agent\tests\gateway\test_interrupt_demo_output.py -q
1 passed

python -m pytest -o addopts='' C:\Users\EDY\AppData\Local\hermes\hermes-agent\tests\gateway\test_busy_session_ack.py -q
32 passed, 1 skipped

python -m pytest -o addopts='' C:\Users\EDY\AppData\Local\hermes\hermes-agent\tests\cli\test_cli_init.py -q
59 passed
```

当前边界：
- 这一步只保证同一 kernel session 内的多 task 路由稳定；跨 kernel session 的 task 切换还没有完整调度模型。
- router 仍是规则 + 简单打分，公共词弱匹配已经被 KMS 的 intent 判断压住，但还不是最终语义路由器。
- direct kernel response 仍主要按 session 读状态，尚未做到完全 task-local 的 progress/evidence/failure 查询。

下一步建议：
1. 做真实本地联调 smoke，记录一次完整用户输出：“旧任务执行中 -> 新请求打断 -> 新 dispatch claim -> 新回答返回”。
2. 让 direct responder 支持按 `target_task_id` 查询 task-local 状态。
3. 再做 proxy mode dispatch 生命周期。
4. 最后再考虑旧状态表到新命名表的主从切换。

## 2026-06-18 第八阶段完成情况

本阶段完成了真实 Hermes 部署目录下的本地联调 smoke 输出整理。

执行命令：

```text
cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python scripts\live_interrupt_demo.py
```

说明：
- 该 smoke 走真实 Hermes Gateway interrupt 路径。
- 该 smoke 走真实 KMS/kernel dispatch API。
- 为了稳定复现，不调用真实大模型，而是用可控 `DemoAgent` 模拟“第一个任务很慢、第二个任务立即回答”。

关键输出：

| step | actor | visible content / action | run_id | dispatch_id | status |
| --- | --- | --- | --- | --- | --- |
| 1 | user | first long task |  |  | sent |
| 2 | KMS | start_new_task | run_9189e6b9d94a | td_466b336b2225 | failed |
| 3 | user | interrupt and answer only this |  |  | sent while first run active |
| 4 | KMS | interrupt_and_replan | run_b742d61a2817 | td_3d1109c7991e | completed |
| 5 | assistant | Interrupting current task. I'll respond to your message shortly. | run_9189e6b9d94a |  | interrupt notice |
| 6 | assistant | FINAL:interrupt and answer only this | run_b742d61a2817 |  | final reply |
| 7 | kernel | active_run cleared |  |  | empty |

结论：
- 第一次用户请求创建 `run_1 / dispatch_1`。
- 第二次用户请求在 `run_1` 未完成时进入 KMS，触发 `interrupt_and_replan`。
- Hermes Gateway 对旧 agent 发出 interrupt。
- 旧 dispatch 被标记为 `failed`，没有输出 `FINAL:first long task`。
- 新 dispatch 被 `hermes-gateway` claim，并最终 `completed`。
- 用户可见输出只有中断提示和第二个请求的最终回复。

当前边界：
- 这仍是本地可控 smoke，不是真实大模型输出 smoke。
- 真实大模型 smoke 需要有效模型 API key，并且运行时间、输出内容不完全稳定。
- 该 smoke 已足够验证 Gateway/KMS/kernel 的打断调度链路。

## 2026-06-18 第九阶段：真实大模型 smoke 尝试

本阶段尝试用真实 Hermes 模型配置跑同一条打断链路。

执行命令：

```text
cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python scripts\live_interrupt_demo.py --real-model
```

本次 smoke 使用：
- Hermes 真实 Gateway 路径。
- KMS/kernel 真实 dispatch API。
- Hermes 当前真实 provider 配置：`deepseek` / `deepseek-v4-pro`。
- smoke 脚本内临时禁用 `reasoning_effort`，避免 DeepSeek OpenAI-compatible endpoint 不接受该参数。

实际结果：

| step | actor | visible content / action | run_id | dispatch_id | status |
| --- | --- | --- | --- | --- | --- |
| 1 | user | 要求先执行 `Start-Sleep -Seconds 15`，再总结项目职责 |  |  | sent |
| 2 | KMS | start_new_task | run_a306d4e00958 | td_2a2735d313ed | failed |
| 3 | user | 中断刚才任务，直接回答 KMS 和 Kernel 的职责差异 |  |  | sent while first run active |
| 4 | KMS | interrupt_and_replan | run_3d1adb6cb4b7 | td_1d28407379aa | failed |
| 5 | assistant | Interrupting current task... | run_a306d4e00958 |  | interrupt notice |
| 6 | assistant | provider failed after retries | run_3d1adb6cb4b7 |  | provider failure reply |
| 7 | kernel | active_run cleared |  |  | empty |

结论：
- 真实 Gateway/KMS/kernel 打断链路跑通：第二条消息触发 `interrupt_and_replan`，旧 run 被中断，两个 dispatch 都被 `hermes-gateway` claim 并收尾。
- 真实大模型回答没有成功，失败原因不是 KMS，而是 DeepSeek provider 返回 `HTTP 402 Insufficient Balance`。
- 当前环境只检测到 `DEEPSEEK_API_KEY`，没有其他可切换的真实 provider key。

后续处理：
- 给 DeepSeek 账号补余额后，可以直接重跑：
  `python scripts\live_interrupt_demo.py --real-model`
- 或配置另一个可用 provider key，再用同一脚本重跑真实模型 smoke。

## 2026-06-18 第十阶段完成情况

本阶段完成了 `KernelDirectResponder` 的 task-local 查询支持。

核心变化：
- direct responder 支持接收 `target_task_id`。
- `KmsManager` 会把 Task Context Router 选中的 task 传给 direct responder。
- 查询 progress / evidence / failures 时，可以按目标 task 过滤，不再默认读取当前 active task 的全 session 状态。
- 修正了状态查询路由优先级：如果用户说“支付 webhook 那个当前进度”，router 会优先选择命中的 task，而不是盲目选择 active task。

新增测试覆盖：
- 查询旧任务 A 的证据，不混入当前任务 B 的证据。
- 查询旧任务 A 的失败工具，不混入当前任务 B 的失败工具。
- 查询旧任务 A 的进度时，不打断当前任务 B 的 active run。

验证结果：

```text
python -m pytest
61 passed
```

已同步提交：

```text
6371750 Scope direct kernel replies to routed tasks
```

当前边界：
- `evidence_items` 和 `execution_actions` 旧表仍没有原生 `task_id` 列。
- 当前 task-local 过滤主要依赖 `task.last_run_id`、task dispatch、task steps、claim supporting evidence 和事件日志。
- 后续如果要更强隔离，可以给旧主表补 `task_id`，但这会牵涉迁移和 reducer 更新，不建议和当前阶段混在一起做。

## 2026-06-18 第十一阶段：真实 DeepSeek smoke 复测成功

更换新的 DeepSeek key 后，重新运行真实模型 smoke。

执行命令：

```text
cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python scripts\live_interrupt_demo.py --real-model
```

本次 smoke 使用：
- Hermes 真实 Gateway 路径。
- KMS/kernel 真实 dispatch API。
- Hermes 当前真实 provider 配置：`deepseek` / `deepseek-v4-pro`。
- 真实 DeepSeek API 调用成功返回答案。

关键输出：

| step | actor | visible content / action | run_id | dispatch_id | status |
| --- | --- | --- | --- | --- | --- |
| 1 | user | 要求先执行 `Start-Sleep -Seconds 15`，再总结 agent-state-kernel 职责 |  |  | sent |
| 2 | KMS | start_new_task | run_9ed870c8e7a6 | td_60de924e0e5c | failed |
| 3 | user | 中断刚才任务，直接回答 KMS 和 Kernel 的职责差异 |  |  | sent while first run active |
| 4 | KMS | interrupt_and_replan | run_868748056c37 | td_b78bc51c1e17 | completed |
| 5 | assistant | Interrupting current task... | run_9ed870c8e7a6 |  | interrupt notice |
| 6 | assistant | 回答 KMS 是 Kernel 内的判断/评估引擎，Kernel 是完整认知操作系统 | run_868748056c37 |  | final reply |
| 7 | kernel | active_run cleared |  |  | empty |

结论：
- 第一次真实模型请求创建 `run_9ed870c8e7a6 / td_60de924e0e5c`。
- 第二次用户请求在第一条 run 仍活跃时进入 KMS，触发 `interrupt_and_replan`。
- Hermes Gateway 中断旧 API call，旧 dispatch 被标记为 `failed`。
- 新 dispatch `td_b78bc51c1e17` 被 `hermes-gateway` claim，并最终 `completed`。
- DeepSeek 真实回答成功返回。
- Kernel 最后清空 `active_run`。

当前边界：
- 这次验证的是真实模型回答和 Gateway/KMS/kernel 打断链路。
- 真实工具调用中断边界还可以继续压测，例如长 shell、文件写入、浏览器工具调用等。
- Hermes 部署目录当前本地 git 状态为 `main...origin/main [ahead 1, behind 505]`，本次 smoke 没有修改该目录文件。

## 2026-06-18 第十二阶段：真实工具调用中断 smoke

本阶段新增并运行了真实工具进程中断 smoke。

新增脚本：

```text
scripts/live_tool_interrupt_smoke.py
```

脚本说明：
- 复用 Hermes Gateway 的真实 interrupt 路径。
- 复用 KMS/kernel dispatch API。
- 使用可控 agent，但不是纯 sleep mock：agent 会启动一个真实子进程工具。
- 工具进程执行当前 Python 解释器的 `time.sleep(15)`。
- 用户第二条消息到达后，Gateway interrupt 会终止该子进程。
- agent 会尝试提交迟到 tool result，用来验证旧输出不会进入用户可见结果。

执行命令：

```text
python scripts\live_tool_interrupt_smoke.py
```

关键输出：

| step | actor | visible content / action | run_id | dispatch_id | status |
| --- | --- | --- | --- | --- | --- |
| 1 | user | first long task |  |  | sent |
| 2 | KMS | start_new_task | run_4b4fe527c1c3 | td_eb8e9fa12362 | failed |
| 3 | user | interrupt and answer only this |  |  | sent while first run active |
| 4 | KMS | interrupt_and_replan | run_e64e53cfb164 | td_01a3d96f94de | completed |
| 5 | assistant | Interrupting current task... | run_4b4fe527c1c3 |  | interrupt notice |
| 6 | assistant | FINAL:interrupt and answer only this | run_e64e53cfb164 |  | final reply |
| 7 | kernel | active_run cleared |  |  | empty |

工具进程结果：

```text
tool_started=True
process_started=True
interrupt_received=True
process_was_terminated=True
late_tool_result_attempted=True
process_return_code=1
```

结论：
- 真实工具子进程确实启动。
- 用户第二条消息触发 Gateway interrupt。
- 旧工具子进程被终止。
- 旧任务没有输出 `FINAL:first long task`。
- 旧 dispatch 被标记为 `failed`。
- 新 dispatch 正常 `completed`。
- Kernel 最后清空 `active_run`。

新增单元测试：

```text
tests/test_smoke_interrupt.py::test_old_run_tool_completed_and_raw_result_are_rejected_after_interrupt
```

该测试验证：
- 旧 run 被打断后，迟到 `ToolCompleted` 会被拒绝。
- 旧 run 被打断后，迟到 `RawResultAvailable` 会被拒绝。
- 当前 active run 仍保持为新 run。
- execution 视图不会被旧 run 的迟到结果污染。

验证结果：

```text
python -m py_compile scripts\live_tool_interrupt_smoke.py
python scripts\live_tool_interrupt_smoke.py
python -m pytest
62 passed
```

当前边界：
- 这个 smoke 使用可控 agent 触发真实子进程工具，不依赖真实大模型选择工具。
- 浏览器工具、文件写入工具等更复杂工具还没有逐一压测。
- Gateway proxy mode 的 dispatch 生命周期已在后续 2026-06-22 进度中接入第一版。

## 2026-06-18 第十三阶段：evidence / execution 原生 task_id 归属

本阶段把多任务隔离从调度层进一步落到状态层。

核心变化：
- `EvidenceItem` 新增 `task_id` 字段。
- `ExecutionAction` 新增 `task_id` 字段。
- SQLite 表新增原生归属列：
  - `evidence_items.task_id`
  - `execution_actions.task_id`
- 新库建表会直接包含该列。
- 旧库启动时通过 `_ensure_column()` 自动补列，不做大迁移。
- `save_evidence()` 会在证据没有 task_id 时绑定当前 `session.active_task_id`。
- `save_execution()` 会在执行动作没有 task_id 时绑定当前 `session.active_task_id`。
- `thinker_view` 中的 evidence / executions 现在会输出 `task_id`。
- `task_flow.execution_summary` 现在也包含 execution 的 `task_id`。

direct responder 同步调整：
- 查询某个 routed task 的 evidence / failures 时，优先使用原生 `task_id` 过滤。
- 历史旧数据如果没有 `task_id`，仍保留原有 fallback：
  - claim supporting evidence；
  - task dispatch run_id；
  - event log run_id；
  - task steps。

新增测试覆盖：
- 同一 kernel session 下，任务 A 的 evidence 写入 `task_id=A`。
- 同一 kernel session 下，任务 B 的 evidence 写入 `task_id=B`。
- 任务 A 的 failed execution 写入 `task_id=A`。
- 任务 B 的 failed execution 写入 `task_id=B`。
- thinker view 暴露 evidence / execution 的 `task_id`。
- direct responder 查询任务 A 时，不混入任务 B 的 evidence / failures。

验证结果：

```text
python -m py_compile src\schema\state.py src\stores\sqlite_store.py src\kernel\engine.py src\kms\kernel_direct_responder.py
python -m pytest
62 passed
```

当前边界：
- 这一步没有回填历史旧数据的 `task_id`，旧数据仍依赖 fallback。
- `belief_items` 仍是旧主表，`claim_items` 已有 task_id 兼容层。
- `progress_states` 仍是 session 级摘要；task-local progress 当前主要来自 task snapshot。
- 后续如要继续收敛，可以考虑给 `progress_states` 或新版 `task_flow` 做更强 task-local 查询，而不是直接大改旧 progress 主表。

## 2026-06-18 第十四阶段：task-local progress 读取层

本阶段继续收敛多任务状态隔离，但没有大改 `progress_states` 主表。

核心变化：
- direct responder 新增 task-local progress builder。
- 查询指定 task 的 progress 时，会先判断该 task 是否是 active task。
- active task 继续使用当前 session progress / plan，同时读取属于该 task 的 `task_flow`。
- 非 active task 优先使用属于该 task 的 `task_flow`；没有匹配的 `task_flow` 时回退到 `TaskSnapshot`。
- task progress 中的 execution 摘要会按 `task_id` 过滤；历史旧数据没有 `task_id` 时再用 task step_id 兜底。
- 查询任务 A 的 progress 不会混入任务 B 的步骤或执行摘要。

新增测试覆盖：
- 任务 A 被暂停后，查询 A 的 progress 仍返回 A snapshot 中的当前步骤。
- 当前 active 任务 B 有自己的 plan / execution summary。
- 查询任务 A 的 progress 不包含 B 的步骤或工具调用。
- 查询 active B 的 task-local progress 可以返回 B 自己的步骤和最近执行。

验证结果：

```text
python -m py_compile src\kms\kernel_direct_responder.py tests\test_task_directory_router.py
pytest tests\test_task_directory_router.py
9 passed
pytest
62 passed
```

当前边界：
- `task_flows` 仍是 session 级兼容表，本阶段只做读取层隔离，不做表结构迁移。
- 历史 execution 没有 `task_id` 时只能按 step_id 做有限 fallback。
- router 对非常短的“那个进度”仍可能要求澄清，这是路由层问题，不在本阶段扩大处理。

## 2026-06-18 第十五阶段：task-local claim / todo 查询

本阶段继续收敛多任务可查询能力，不改旧主表结构。

核心变化：
- direct responder 新增 `claims` 回答类型。
- direct responder 新增 `todos` 回答类型。
- 查询指定 task 的 claims 时，优先按 `claim_items.task_id` 过滤。
- 查询指定 task 的 todos 时，优先按 `todo_obligations.task_id` 过滤。
- 历史旧数据没有 `task_id` 时，claims / todos 会尝试用 task event payload 做有限 fallback。
- intent classifier 现在能把“结论 / 风险 / 待办 / 待确认”识别成 kernel 可直接回答的问题。
- 移除了过宽的“判断”单词匹配，避免把“我想判断材料是否足够”误判为 claims 查询。

新增测试覆盖：
- A/B 两个任务分别写入 claim 和 todo。
- `claim_items` / `todo_obligations` 会绑定对应 task_id。
- 查询任务 A 的 claims 不混入任务 B。
- 查询任务 A 的 todos 不混入任务 B。
- 查询 active B 的 claims / todos 只返回 B 自己的内容。
- intent classifier 能识别 claims / todos，同时保留复杂材料判断问题的 LLM fallback。

验证结果：

```text
python -m py_compile src\kms\intent_classifier.py src\kms\kernel_direct_responder.py tests\test_intent_classifier.py tests\test_task_directory_router.py
pytest tests\test_intent_classifier.py tests\test_task_directory_router.py
15 passed
pytest
63 passed
```

当前边界：
- `belief_items` / `commitments` 仍是旧 session 级主表，task-local 查询主要依赖 `claim_items` / `todo_obligations` 影子表。
- legacy fallback 只能覆盖带 task/run 线索的旧事件，无法百分百还原所有历史归属。
- 本阶段按用户要求不上传 GitHub；本地提交后仍需等网络恢复再统一 push。

## 2026-06-22：manager_view / observer_view 第一版

本阶段补齐新版架构中的视图分层入口，不新增状态表。

核心变化：
- 新增 `GET /kms/sessions/{session_id}/views/observer`。
- 新增 `GET /kms/sessions/{session_id}/views/manager`。
- `observer_view` 面向 Talker / Observer，只暴露可转述进度、安全事实、未确认点、待办、阻塞原因、允许/禁止动作和 pending observer notifications。
- `manager_view` 面向 Manager UI / 用户管理视角，暴露 task brief、task flow、active task、任务列表、风险、待确认项、notifications 和 thinker dispatches。
- `observer_view` 不暴露 raw belief、evidence、thinker dispatch 等内部执行细节。

验证覆盖：
- Observer 只能看到安全进度和通知，看不到内部状态。
- Manager 能看到风险、待确认项、通知和 thinker dispatch。
- 原有 pipeline view、thinker dispatch、打断、恢复、router 回归均通过。

当前边界：
- 仍是 HTTP 查询视图，不是订阅推送。
- Notification policy 仍在 API 层做最小生成，后续应拆为 coordinator。
- 旧状态表仍是事实来源，新命名表仍处于兼容/影子层。

## 2026-06-22：task-local conversation refs

本阶段补齐“一个 user session 下多个 task 的消息归属”能力，但不把 Kernel 做成 message history 数据库。

核心变化：
- 新增 `TaskConversationRef`。
- 新增表 `task_conversation_refs`。
- `/kms/dispatch-user-message` 支持可选 `runtime_refs`，可携带 runtime message id。
- KMS 在 dispatch 决策确定目标 task 后，写入 task-local conversation ref。
- Kernel 直接回复会写入 `role=assistant` 的 conversation ref。
- Thinker dispatch complete 可带 `response_summary` / `runtime_refs`，写入 assistant conversation ref。
- Task Router 会把最近 task conversation refs 作为临时 routing hints，用来辅助“那个任务 / 当前进度 / 另一个”这类表达的归属判断。
- `observer_view` / `manager_view` 增加 `recent_conversation_refs`。

架构边界：
- 保存的是 `text_summary` 和 runtime `message_ref_id`，不是完整 transcript。
- 完整消息历史、FTS、上下文压缩仍属于 Hermes / 宿主 runtime。
- conversation refs 只服务 task routing、view 上下文和审计，不作为 claim/evidence 的事实来源。
- Observer/Talker 在外部 UI 最终发给用户的全文仍由 runtime 保存；Kernel 后续只需要接收摘要和 message id。

验证覆盖：
- dispatch 后用户消息会归档到对应 task。
- status query 不唤醒 Thinker 时也会归档到同一个 task。
- Kernel direct response 会归档为 assistant ref。
- Thinker complete 回传的回答摘要会归档为 assistant ref。
- Router 能利用 task conversation refs 选择正确 task。
- manager / observer view 能看到最近 conversation refs。

## 2026-06-22：NotificationCoordinator 第一版

本阶段把 notification 生成策略从 API 层收敛到 KMS coordinator。

核心变化：
- 新增 `src/kms/notification/coordinator.py`。
- API 的 thinker dispatch complete / fail endpoint 不再直接拼 observer notification。
- `NotificationCoordinator.notify_dispatch_completed()` 负责生成 `task_done` 或 `progress_update`。
- `NotificationCoordinator.notify_dispatch_failed()` 负责生成 `task_failed`。
- stale dispatch 不生成 notification 的规则由 coordinator 统一处理。

架构意义：
- 更接近设计文档中的 Notification / Wakeup Orchestrator。
- API 层只保留协议适配职责。
- 后续通知去重、节流、优先级升级、SSE/WebSocket 推送可以继续在 coordinator 下扩展。

当前边界：
- 只覆盖 dispatch complete / fail。
- 还没有根据 progress diff 自动判断是否通知。
- 还没有推送通道，Observer / Talker 仍需查询 notification API。

## 2026-06-22：Runtime adapter / notification stream / state switch review

本阶段继续按新架构收敛 runtime 接入和通知层。

核心变化：
- 新增 `ConversationRefCoordinator`，KmsManager 不再直接构造 conversation ref 落库参数。
- 新增 `POST /kms/conversation-refs`，支持 Observer / Talker / Runtime 回传外部最终回复摘要和 message id。
- 新增通用 `RuntimeEventAdapter`，不再只依赖 HermesAdapter。
- HermesAdapter 补齐 `runtime_refs`、`response_summary` 和 conversation ref 回传方法。
- 新增 notification SSE：
  - `GET /kms/observer/notifications/stream`
  - `GET /kms/talker/notifications/stream`
- NotificationCoordinator 增加：
  - `dedupe_key` 去重；
  - `min_interval_seconds` 节流；
  - `silent_update` / `requires_user_visible_message` delivery policy。

架构审查：
- 本阶段没有让 Kernel 保存完整 transcript，仍只保存摘要和 runtime message id。
- RuntimeEventAdapter 只是协议适配，不接管 runtime session DB、message history 或工具执行。
- SSE 当前是轻量轮询 stream，不是独立推送 broker。

旧状态表主从切换结论：
- 暂不切换。
- 原因是 reducer、progress synthesize、direct responder、打断恢复链路仍依赖旧表作为事实来源。
- 新表继续作为 task-first 兼容读模型。
- 后续应逐表切换读取优先级，而不是一次性迁移。

## 2026-06-22：task-first 状态表主读切换

本阶段继续按设计文档推进状态模型迁移，不再停留在“影子表只读展示”。

核心变化：
- `task_brief_states / task_flows / claim_items / todo_obligations` 已切为主读模型。
- `intent_states / plan_states / belief_items / commitments` 改为兼容输出和 debug 过渡层。
- 新版 getter 优先读新版表，缺数据再回退旧表。
- 旧版 getter 也反向优先由新版表合成，缺数据再回退旧表。
- KMS dispatch、pause、resume、global task 同步优先使用 `task_brief_version`。
- Pipeline 分配事件版本时优先使用 `task_brief_states.task_brief_version`。
- manager / observer / thinker view 的风险、阻塞、取消判断优先使用 `claim_items / todo_obligations / task_brief`。
- `save_intent / save_plan / save_belief / save_commitment` 调整为先写新版表，再写旧兼容表。
- 新增 `tests/test_state_primary_read_switch.py`，验证新旧表内容冲突时以新版表为准。

当前边界：
- 旧表没有物理删除。
- reducer 入口函数名仍保留旧命名，内部通过 store 写入新版主表和旧兼容表。
- 下一步如果继续推进，应先冻结旧表直接读取依赖，再考虑删除或迁移历史数据。

## 2026-06-22：Thinker 架构词汇提示接入

本阶段处理真实大模型 smoke 中暴露的概念混淆问题：Hermes/Thinker 在回答项目架构时，曾把 KMS 解释成 Kernel Memory System 或 Judge，这和当前架构文档不一致。

核心变化：
- 在真实 Hermes 部署目录加入 Runtime-side Agent State Kernel 词汇说明。
- 仅当 Hermes 配置了 `gateway.kernel_url` 时注入，避免影响纯 Hermes 使用。
- 明确 Kernel 是底层状态内核，负责事件日志、状态表、reducer 和 views。
- 明确 KMS 是 Kernel 上层管理调度层，负责用户消息调度、任务路由、打断/恢复、thinker dispatch、通知和状态解释。
- 明确 Thinker 是 Hermes 执行推理和工具调用的进程。
- 明确 Talker / Observer 是外部交互和展示层。

验证结果：
```text
python -m pytest -o addopts='' -q tests\agent\test_system_prompt.py
7 passed
```

当前边界：
- 本阶段只改 Thinker 的系统提示，不改 dispatch、Gateway、CLI 执行逻辑。
- 这能降低真实模型解释架构时的跑偏概率，但不能替代后续真实 LLM smoke。
- Hermes 真实目录只做本地提交，不推远端。

## 2026-06-22：真实架构词汇 smoke / 任务路由收敛 / 旧表读取冻结

本阶段继续按新版架构文档推进，不改动已经稳定的 dispatch 生命周期，只收敛三个边界问题。

真实 smoke 结论：
- 使用真实 Hermes + DeepSeek 跑 `scripts\live_interrupt_demo.py --real-model --scenario interrupt`。
- 旧任务被打断后标记为 `failed`，新 dispatch 标记为 `completed`。
- 最终回答通过 `ARCHITECTURE_GLOSSARY_CHECK: passed`。
- 模型现在能正确区分：Kernel 是底层状态内核，KMS 是上层管理调度层。

KMS 路由修正：
- 修复“另一个任务当前进度？”被误判为新任务的问题。
- intent classifier 现在会先识别已有 Kernel 上下文中的状态/进度查询，再处理“另一个任务”这类新任务标记。
- Task Context Router 现在先处理“另一个 / 不是这个”指代，再回退到 active task 状态查询。
- 新增回归：当任务 B 正在 active 时，用户问“另一个任务当前进度？”，KMS 会直接回答任务 A 的进度，不打断任务 B。

旧表读取冻结：
- 新增扫描测试，禁止业务层直接 SQL 读旧表：
  - `intent_states`
  - `plan_states`
  - `belief_items`
  - `commitments`
- 旧表名只允许在 `sqlite_store.py` 内部作为兼容存储实现出现。
- `StateSourceAudit` 新增：
  - `legacy_direct_sql_frozen=true`
  - `legacy_tables_removable=false`

验证结果：
```text
python -m pytest -o addopts='' -q tests\test_intent_classifier.py tests\test_task_directory_router.py tests\test_state_source_audit.py
31 passed

python -m pytest -o addopts='' -q tests\gateway\test_interrupt_demo_output.py tests\agent\test_system_prompt.py
13 passed

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
```

当前边界：
- 旧表还不能物理删除，因为仍承担兼容输出和历史数据过渡。
- 本阶段没有做完整历史数据迁移。
- 下一步应把业务层剩余的兼容 getter 调用逐步替换成新版 getter，然后再评估旧表删除。

## 2026-06-22：架构一致性审查与旧表迁移准备

本阶段按新版架构设计文档做了一次对照审查，并继续推进旧状态表迁移准备。

核心变化：
- 新增 `docs/progress/architecture-alignment-audit-2026-06-22.md`，逐项对照设计要求和当前实现。
- `dispatch_context` 改为读取 `task_brief/task_flow`，不再读取旧 `intent/plan` getter。
- `task/coordinators` 的任务快照、暂停、刷新逻辑改为读取 `task_brief/task_flow`。
- `kernel_direct_responder` 的进度回复改为读取 `task_flow`。
- `scripts/live_llm_router_smoke.py` 增加更多真实 router smoke：
  - LLM 解决模糊任务指代；
  - “另一个任务当前进度？”直接由 Kernel 回答；
  - “新任务：...”仍创建新 task。
- 新增 `scripts/migrate_legacy_state_tables.py`：
  - 默认 dry-run；
  - `--write` 才写入；
  - 不删除旧表；
  - 支持 intent/plan/belief/commitment 迁移到 task_brief/task_flow/claim/todo。
- `StateSourceAudit` 新增：
  - `remaining_compat_getter_files`
  - 用于明确旧表为什么还不能物理删除。

当前结论：
- 架构方向和设计文档一致。
- KMS / Kernel / Thinker 的分层已经基本对齐。
- 旧表直接 SQL 读取已冻结。
- 旧表仍不能删除，因为 `pipeline`、`engine`、`sqlite_store` 仍有兼容 getter/双写职责。

## 2026-06-22：真实库迁移执行、新表主读继续收敛、legacy debug 输出

本阶段继续按新版架构文档推进状态模型迁移，目标是让系统默认使用 task-first 新表，而不是继续把旧的 `intent/plan/belief/commitment` 当作主模型。

真实 SQLite 库迁移结果：

```text
DB: data/kernel.db
backup: data/kernel.db.bak-20260622165335

dry-run:
sessions=6, migrated=0, dry_run=6, skipped_existing=0, missing_legacy=18

write:
sessions=6, migrated=6, dry_run=0, skipped_existing=0, missing_legacy=18

post-write dry-run:
sessions=6, migrated=0, dry_run=0, skipped_existing=6, missing_legacy=18
```

代码变化：

- `pipeline` 的 reducer 输入改为优先从新版状态对象读取：
  - `task_brief -> IntentState`
  - `task_flow -> PlanState`
  - `claim_items -> BeliefItem`
  - `todo_obligations -> Commitment`
- `refresh_progress / gate / sync / validate / arbitrate` 都改为基于新版状态对象转换后的输入。
- `engine` 输出新增 `legacy_debug` 区，用来集中放旧命名兼容输出。
- `thinker_view` 的 `current_step` 改为优先从 `task_flow` 计算。
- 顶层 `intent/plan/beliefs/commitments` 暂时保留为兼容别名，避免一次性破坏现有调用方。

新增/增强验证：

- `tests/test_state_primary_read_switch.py` 增加断言：新表字段是主输出，旧命名输出进入 `legacy_debug`。
- 真实 Router smoke 验证：

```text
python scripts\live_llm_router_smoke.py
FINAL_DECISION=select_existing
OTHER_STATUS_ACTION=respond_from_kernel
OTHER_STATUS_REQUIRES_THINKER=False
EXPLICIT_NEW_TASK_ACTION=start_new_task
```

- 真实 Hermes 打断 smoke 验证：

```text
python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch: failed
new dispatch: completed
active_run cleared
```

- 全量测试：

```text
python -m pytest -o addopts='' -q
113 passed
```

旧表删除评估：

- 现在仍不建议物理删除旧表。
- 原因是旧表仍承担兼容输出、旧调用方过渡、历史数据 fallback、双写验证的职责。
- 当前已经可以说“新表是主读模型”，但还不能说“旧表已经无职责”。

下一步：

1. 继续减少顶层旧字段暴露，让外部调用方改用 `task_brief/task_flow/claims/todos`。
2. 为旧字段增加 deprecation 文档和迁移检查。
3. 等旧字段不再被测试和调用方依赖后，再把 `legacy_debug` 也限制到 debug/manager 场景。
4. 最后才评估删除 `intent_states/plan_states/belief_items/commitments`。

## 2026-06-22：旧顶层视图字段退场

本阶段继续推进“旧接口退场”，重点不是新增能力，而是减少旧架构暴露面。

代码变化：

- `thinker_view` 顶层不再输出：
  - `intent`
  - `plan`
  - `beliefs`
  - `commitments`
- `debug_view` 顶层也不再输出上述旧字段。
- 旧命名形状统一进入 `legacy_debug`。
- `thinker_view["claims"]` 按 visibility 过滤，避免把 private claim 暴露给 Thinker 视图。
- `engine._get_raw_state()` 不再默认读取旧兼容 getter。
- 只有生成 `legacy_debug` 时，才调用 `engine._get_legacy_debug_state()` 读取旧形状兼容输出。
- `StateSourceAudit.remaining_compat_getter_files` 移除 `src/kms/pipeline.py`，目前剩余：
  - `src/kernel/engine.py`
  - `src/stores/sqlite_store.py`

测试变化：

- 视图测试改为使用：
  - `task_brief`
  - `task_flow`
  - `claims`
  - `todos`
- 新增断言：`thinker_view/debug_view` 顶层不能再出现旧字段。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_state_primary_read_switch.py tests\test_pipeline_event_flow.py tests\test_architecture_ab_experiment.py
23 passed

python -m pytest -o addopts='' -q tests\test_state_source_audit.py
4 passed
```

当前结论：

- 旧顶层 API 已开始退场。
- `pipeline` 已不再是旧兼容 getter 依赖点。
- 旧表仍不能物理删除，因为 `sqlite_store.py` 还负责历史 fallback 和双写兼容，`engine.py` 仍负责 `legacy_debug`。

最终验证补充：

```text
python -m pytest -o addopts='' -q
113 passed

python scripts\live_llm_router_smoke.py
passed

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch=failed
new dispatch=completed
active_run=empty
```

## 2026-06-22：legacy_debug 收窄到管理/调试视图

本阶段继续收窄旧接口暴露面。

代码变化：

- `thinker_view` 不再输出 `legacy_debug`。
- `legacy_debug` 只保留在：
  - `manager_view`
  - `debug_view`
- `thinker_view` 现在只暴露新版主字段：
  - `task_brief`
  - `task_flow`
  - `claims`
  - `todos`
- 新增扫描测试，禁止 `src/` 业务代码重新调用旧 getter：
  - `get_intent`
  - `get_plan`
  - `get_beliefs`
  - `get_commitments`
- 允许例外仅剩：
  - `src/kernel/engine.py`：生成 `legacy_debug`
  - `src/stores/sqlite_store.py`：历史 fallback / 双写兼容

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_state_primary_read_switch.py tests\test_state_source_audit.py tests\test_pipeline_event_flow.py tests\test_manager_observer_views.py
27 passed

python -m pytest -o addopts='' -q
114 passed

python scripts\live_llm_router_smoke.py
passed

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch=failed
new dispatch=completed
active_run=empty
```

当前结论：

- Thinker 已不再消费旧形状调试输出。
- 旧 getter 重新流入业务代码的风险已由测试拦截。
- 旧表仍不能删，下一步要处理的是 `sqlite_store.py` 的双写和历史 fallback。

## 2026-06-22：测试分层

本阶段先处理测试执行效率问题，不减少覆盖，只减少不必要的全量测试次数。

新增脚本：

```text
scripts/test_tiers.py
scripts/test_fast.py
scripts/test_core.py
scripts/test_full.py
```

分层规则：

| 层级 | 命令 | 用途 |
|---|---|---|
| fast | `python scripts/test_fast.py` | 小改动快速检查 |
| core | `python scripts/test_core.py` | KMS/Kernel/Router/打断链路改动 |
| full | `python scripts/test_full.py` | 阶段完成或提交前全量检查 |

新增文档：

```text
docs/testing.md
```

新增元测试：

```text
tests/test_test_tiers.py
```

它会检查：

- fast/core 引用的测试文件必须存在；
- core 必须包含 fast；
- test runner 必须清空项目 pytest addopts，避免环境默认参数干扰。

当前建议：

- 小改不再默认跑全量；
- 架构主链路改动跑 core；
- 阶段完成再跑 full；
- 真实模型/Hermes 改动单独跑 live smoke。

验证结果：

```text
python -m py_compile scripts\test_tiers.py scripts\test_fast.py scripts\test_core.py scripts\test_full.py
passed

python -m pytest -o addopts='' -q tests\test_test_tiers.py
3 passed

python scripts\test_fast.py
43 passed in 18.68s

python scripts\test_core.py
106 passed in 152.14s

python scripts\test_full.py
117 passed in 154.26s
```

### 测试分层修正

第一次分层后，`core` 和 `full` 的耗时过于接近，说明 `core` 划得太宽。

本次调整：

- 新增 `scripts/test_integration.py`。
- 将重集成测试移入 `integration`：
  - `test_pipeline_event_flow.py`
  - `test_requested_user_scenarios.py`
  - `test_smoke_interrupt.py`
  - `test_architecture_ab_experiment.py`
- `core` 保留 fast + 普通核心集成测试。
- `tests/test_test_tiers.py` 加入 fast，保证分层脚本自身会被快速验证。

按要求本次只重跑 core：

```text
python scripts\test_core.py
74 passed in 52.60s
```

## 2026-06-22：旧状态表写入退场

本阶段把旧状态表从“兼容写入对象”收敛为“历史读取 fallback”。

代码变化：

- `save_intent()` 只写 `task_brief_states`。
- `save_plan()` 只写 `task_flows`。
- `save_belief()` 只写 `claim_items`。
- `save_commitment()` 只写 `todo_obligations`。
- 不再保留旧表双写开关。
- `get_intent / get_plan / get_beliefs / get_commitments` 继续保留旧表读取 fallback，用于历史 DB 兼容。

测试变化：

- 保留 `test_legacy_state_table_writes_are_disabled_by_default`，验证新保存的数据不会进入旧表。
- 删除“恢复旧表双写”的测试，因为该能力已经退场。
- 继续保留新表主读、`legacy_debug`、状态源审查相关回归。

本轮验证：

```text
python -m py_compile src\stores\sqlite_store.py
passed

python scripts\test_integration.py
111 passed in 200.09s

python scripts\live_llm_router_smoke.py
FINAL_DECISION=select_existing
OTHER_STATUS_ACTION=respond_from_kernel
OTHER_STATUS_REQUIRES_THINKER=False
EXPLICIT_NEW_TASK_ACTION=start_new_task

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch=failed
new dispatch=completed
active_run=empty
```

验收范围：

- pipeline event flow；
- 打断 / 恢复；
- requested user scenarios；
- smoke interrupt；
- architecture A/B experiment；
- task directory router；
- manager / observer views；
- 真实 LLM Router；
- 真实 Hermes interrupt。

当前结论：

- 新数据已经只进入新版 task-first 状态表。
- 旧表不再承担新写入职责。
- 旧表仍不能物理删除，因为历史库读取 fallback 还在。
- 下一步应继续观察旧表 fallback 的真实使用情况，再决定是否做物理删表迁移。

## 2026-06-23：旧表 fallback 使用审计

本阶段继续推进旧表退场，但不直接删除旧表。目标是先知道真实运行中是否还会读旧表。

代码变化：

- 新增 `legacy_state_fallback_audits` 表。
- `get_intent()` 只有在读取 `intent_states` 时记录 `task_brief` fallback 命中。
- `get_plan()` 只有在读取 `plan_states` 时记录 `task_flow` fallback 命中。
- `get_beliefs()` 只有在读取 `belief_items` 时记录 `claim` fallback 命中。
- `get_commitments()` 只有在读取 `commitments` 时记录 `todo` fallback 命中。
- `/kms/state-source-audit` 新增：
  - `legacy_fallback_observed`
  - `legacy_fallback_hit_count`
  - `legacy_fallback_hits`

测试变化：

- 新增旧表 fallback 命中审计回归。
- 新增新表主读路径不产生 fallback 命中的断言。
- API 测试改为挂载内存 Kernel，保证审计接口读取真实 store。

验证结果：

```text
python -m py_compile src\stores\sqlite_store.py src\kms\state_source_audit.py src\api\server.py
passed

python -m pytest -o addopts='' -q tests\test_state_primary_read_switch.py tests\test_state_source_audit.py
9 passed in 1.49s

python scripts\test_core.py
76 passed in 35.71s

python scripts\test_integration.py
112 passed in 118.12s

python scripts\live_llm_router_smoke.py
FINAL_DECISION=select_existing
OTHER_STATUS_ACTION=respond_from_kernel
OTHER_STATUS_REQUIRES_THINKER=False
EXPLICIT_NEW_TASK_ACTION=start_new_task

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch=failed
new dispatch=completed
active_run=empty

data/kernel.db fallback audit
LEGACY_FALLBACK_AUDIT_ROWS=0
```

当前结论：

- 旧表 fallback 已经可以被观测。
- 本轮 core / integration / 真实 smoke 未发现真实旧表 fallback 命中。
- 这还不等于可以马上物理删旧表，因为真实历史 DB 可能仍有未覆盖路径。
- 下一步应继续保留审计，积累更多真实运行证据，再做物理删表方案。

## 2026-06-23：fallback audit 查看脚本、KMS 小拆分、Runtime Event Adapter Hermes 事件入口

本阶段按顺序继续推进旧表退场观察、KMS 拆分和 Runtime Event Adapter 接入。

代码变化：

- 新增 `scripts/report_legacy_fallback_audit.py`：
  - 通过 `SqliteStore` 正常连接 DB；
  - 输出 fallback audit 行数和总命中数；
  - 当前真实库输出 `ROWS=0 / HIT_COUNT=0`。
- 新增 `tests/test_legacy_fallback_audit_report.py`，覆盖空状态和有命中两种输出。
- 新增 `src/kms/dispatch_decision.py`：
  - 将 `DispatchDecision` 从 `KmsManager` 拆出；
  - 将 kernel 直接回复和 thinker run 返回对象组装拆成 helper；
  - `KmsManager` 仍保留主调度分支，后续还需要继续拆任务切换部分。
- `RuntimeEventAdapter` 新增 Hermes 常用事件入口：
  - `submit_tool_started`
  - `submit_tool_completed`
  - `submit_tool_failed`
  - `submit_reasoning_summary`
  - `submit_raw_result`

新增/调整测试：

- `tests/test_legacy_fallback_audit_report.py`
- `tests/test_runtime_event_adapter.py`
- `tests/test_task_directory_router.py`
- `tests/test_smoke_interrupt.py`

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_legacy_fallback_audit_report.py tests\test_state_primary_read_switch.py tests\test_state_source_audit.py
11 passed in 2.29s

python -m pytest -o addopts='' -q tests\test_task_directory_router.py tests\test_smoke_interrupt.py
24 passed in 60.87s

python -m pytest -o addopts='' -q tests\test_runtime_event_adapter.py
4 passed in 0.33s

python scripts\test_core.py
78 passed in 37.00s

python scripts\test_integration.py
114 passed in 123.34s

python scripts\live_llm_router_smoke.py
FINAL_DECISION=select_existing
OTHER_STATUS_ACTION=respond_from_kernel
OTHER_STATUS_REQUIRES_THINKER=False
EXPLICIT_NEW_TASK_ACTION=start_new_task

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch=failed
new dispatch=completed
active_run=empty

python scripts\report_legacy_fallback_audit.py
ROWS=0
HIT_COUNT=0
NO_LEGACY_FALLBACK_HITS
```

当前结论：

- fallback audit 已经从“有表”变成“有可运行查看命令”。
- 本轮多次真实链路仍未命中旧表 fallback。
- KMS 没有偏离架构设计：调度仍在 KMS，状态事实仍在 Kernel/store。
- Runtime Event Adapter 更适合真实 Hermes 逐步接入工具事件，但还没有强行改 Hermes 巨型 gateway 主流程。

## 2026-06-23：TaskDispatchPlanner、Hermes kernel dispatch helper、removal-check

本阶段继续按架构文档推进，重点是拆 KMS 内部复杂度，但不把调度逻辑下沉到 Kernel。

代码变化：

- 新增 `src/kms/task/dispatch_planner.py`：
  - 负责 KMS 内部 task 切换计划；
  - 处理 routed task、new task、resume task、默认 interrupt 场景；
  - 只做 KMS 调度计划，不直接做 Kernel reducer。
- `KmsManager` 改为调用 `TaskDispatchPlanner`：
  - `KmsManager` 仍负责用户消息主流程；
  - `TaskDispatchPlanner` 负责生成 task switch plan；
  - `Kernel` 仍只负责状态存储和 views。
- 真实 Hermes 部署目录更新 `hermes_cli/kernel_dispatch.py`：
  - 新增 `async_submit_runtime_event`
  - 新增 `async_submit_tool_started`
  - 新增 `async_submit_tool_completed`
  - 新增 `async_submit_tool_failed`
  - 这些 helper 与主项目 `RuntimeEventAdapter` 的 payload 形状保持一致。
- `scripts/migrate_legacy_state_tables.py` 新增 `--removal-check`：
  - 检查旧表行数；
  - 检查是否还有未迁移 session；
  - 检查 fallback audit 命中数；
  - 只读检查，不删除旧表。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_task_directory_router.py tests\test_smoke_interrupt.py tests\test_requested_user_scenarios.py
30 passed in 96.32s

python -m pytest -o addopts='' -q tests\test_legacy_state_migration.py
2 passed in 1.82s

python -m pytest -o addopts='' -q tests\hermes_cli\test_kernel_dispatch.py
3 passed in 1.30s

python scripts\test_core.py
79 passed in 42.58s

python scripts\test_integration.py
115 passed in 129.03s

python scripts\live_llm_router_smoke.py
FINAL_DECISION=select_existing
OTHER_STATUS_ACTION=respond_from_kernel
OTHER_STATUS_REQUIRES_THINKER=False
EXPLICIT_NEW_TASK_ACTION=start_new_task

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
ARCHITECTURE_GLOSSARY_CHECK: passed
old dispatch=failed
new dispatch=completed
active_run=empty

python scripts\report_legacy_fallback_audit.py
ROWS=0
HIT_COUNT=0
NO_LEGACY_FALLBACK_HITS

python scripts\migrate_legacy_state_tables.py data\kernel.db --removal-check
safe_to_remove=true
unmigrated_sessions=0
fallback_hit_count=0
legacy_rows.intent_states=6
```

架构边界审查：

- 未把任务调度逻辑放进 Kernel。
- 未让 Thinker 自己决定打断或恢复。
- 未把完整聊天记录写入 Kernel。
- 未直接删除旧表。
- Hermes 侧只补共享 helper，Gateway 主流程仍通过 KMS dispatch 生命周期。

当前结论：

- KMS 内部结构进一步收敛，但 `KmsManager.dispatch_user_message()` 仍可继续拆。
- 旧表 removal-check 当前显示没有阻塞项，但仍建议继续观察真实运行，不立即物理删表。
- Runtime Event Adapter 和 Hermes kernel dispatch helper 已具备工具事件接入基础，下一步可以逐步改 Gateway 工具回调使用这些 helper。
## 2026-06-23：DispatchPreparation 拆分与 Hermes Gateway 事件 helper 收敛

本阶段继续按架构设计文档推进，目标是让 KMS 更像调度层，而不是把所有判断都堆在 `KmsManager.dispatch_user_message()` 里；同时让真实 Hermes Gateway 的 runtime event 上报复用统一 helper，避免 Gateway 和 CLI 各写一套 `/kms/request` 逻辑。

通俗说明：

- 改哪里：新增 `src/kms/dispatch_preparation.py`，把“用户消息先属于哪个 user session、路由到哪个 task、应该是什么意图”从 `KmsManager` 里拆出来。
- 为什么改：KMS 是管理调度层，`KmsManager` 应该负责串流程，不应该长期塞满路由、意图、session 查找等细节。
- 改完什么样：`KmsManager` 现在先拿到 `DispatchPreparation`，再决定澄清、Kernel 直接回答、创建/恢复/打断任务、创建 thinker dispatch。
- Hermes 侧改哪里：真实部署目录 `C:\Users\EDY\AppData\Local\hermes\hermes-agent` 的 `gateway/run.py` 改为调用 `hermes_cli.kernel_dispatch.async_submit_runtime_event()`。
- Hermes 侧为什么改：工具事件、summary、raw result 都应该走同一套 runtime event helper，后续 CLI/Gateway 不会分叉。
- Hermes 侧改完什么样：Gateway 的 `ToolStarted / ToolCompleted / ToolFailed / ReasoningSummary / RawResultAvailable / TaskCompleted / TaskFailed` 仍然通过 `_submit_kernel_event()` 入口发出，但底层统一走 helper；stale run 仍返回 `False`，不会污染当前 run。

架构边界审查：

- 没有把调度逻辑放进 Kernel；Kernel 仍只负责状态、事件、视图。
- 没有让 Thinker 自己决定打断或恢复；Hermes 仍只 claim / heartbeat / complete / fail dispatch 并提交 runtime event。
- 没有改变 `respond_from_kernel / interrupt_and_replan / resume_context` 语义。
- 没有删除旧表；旧表物理删除仍等待更长真实链路观察。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_dispatch_preparation.py tests\test_task_directory_router.py tests\test_smoke_interrupt.py
26 passed in 64.40s

cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python -m pytest -o addopts='' -q tests\hermes_cli\test_kernel_dispatch.py tests\gateway\test_busy_session_ack.py::TestKernelRunReporting
15 passed in 3.84s
```

当前结论：

- KMS 分层又前进了一步，`dispatch_preparation` 已经承担前置只读判断。
- Gateway runtime event 已收敛到共享 helper，真实 Hermes 与项目内 Runtime Event Adapter 的协议形状更一致。
- 下一步应继续拆 `KmsManager` 里“创建 run、提交用户消息、创建 task、创建 thinker dispatch”这一段执行编排，或者继续把 Hermes 更深的工具/流式事件引用补齐到 runtime refs。
## 2026-06-23：DispatchExecution 拆分

本阶段继续收敛 `KmsManager`，把“需要 Thinker 执行的调度落地动作”拆到 `DispatchExecutionCoordinator`。

通俗说明：

- 改哪里：新增 `src/kms/dispatch_execution.py`。
- 为什么改：上一轮已经把“先判断用户想干什么”拆出去了，但 `KmsManager` 里还保留了“创建 run、提交用户消息、创建 task、同步 global task、创建 thinker dispatch”等执行细节。
- 改完什么样：`KmsManager` 现在更像总控；真正执行调度的动作由 `DispatchExecutionCoordinator` 负责。

职责边界：

- `DispatchExecutionCoordinator` 仍属于 KMS 层。
- 它不改 Kernel reducer，不把调度逻辑放进 Kernel。
- 它不让 Thinker 自己选择任务；Thinker 仍只消费 KMS 创建的 `thinker_dispatch`。
- `respond_from_kernel / ask_clarification / resume_context / interrupt_and_replan` 语义保持不变。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_dispatch_execution.py tests\test_dispatch_preparation.py tests\test_task_directory_router.py tests\test_smoke_interrupt.py
28 passed in 100.13s
```

当前结论：

- `KmsManager` 已完成第二步拆分：前置准备和执行调度都已分离。
- 下一步如果继续拆，应该拆“Kernel 直接回复 / 澄清回复返回 DispatchDecision”的包装，让 manager 只保留更薄的分支编排。
## 2026-06-23：DispatchResponse 拆分

本阶段继续收敛 `KmsManager`，把“不需要 Thinker 的 KMS 回复包装”拆到 `DispatchResponseCoordinator`。

通俗说明：

- 改哪里：新增 `src/kms/dispatch_response.py`。
- 为什么改：`KmsManager` 里还在直接拼澄清回复、Kernel 直接回复、no-resume 回复对应的 `DispatchDecision`，这会让 manager 继续变胖。
- 改完什么样：`KmsManager` 只判断走哪个分支；真正生成回复、记录 conversation ref、包装 `DispatchDecision` 交给 `DispatchResponseCoordinator`。

覆盖的回复类型：

| 类型 | 行为 |
|---|---|
| `ask_clarification` | 返回澄清问题，不创建 thinker dispatch，不打断 active run |
| `respond_from_kernel` | 从 Kernel 状态直接生成回复，不唤醒 Thinker |
| `no_paused_task_to_resume` | 没有可恢复任务时直接回复，并记录 conversation ref |

架构边界审查：

- 回复包装仍在 KMS 层。
- Kernel 不参与任务路由和回复决策。
- Thinker 不会因为澄清或直接回复被唤醒。
- Conversation ref 仍只保存摘要和 runtime 引用，不保存完整聊天记录。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_dispatch_response.py tests\test_dispatch_execution.py tests\test_dispatch_preparation.py tests\test_task_directory_router.py tests\test_smoke_interrupt.py
31 passed in 84.25s
```

当前结论：

- `KmsManager` 已完成三段核心拆分：preparation、execution、response。
- 下一步不建议继续往 `src/kms` 根目录加散文件，应该考虑先做 `kms/dispatch/` 或 `kms/response/` 目录分组迁移。
## 2026-06-23：KMS dispatch 目录分组迁移

本阶段只做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/dispatch/` 子目录。
- 为什么改：`src/kms` 根目录里 dispatch 相关文件越来越多，看起来像散落脚本；它们其实属于同一条 KMS 调度链路。
- 改完什么样：dispatch 相关模块统一放进 `src/kms/dispatch/`，`KmsManager` 继续作为 KMS 总控。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/dispatch_decision.py` | `src/kms/dispatch/decision.py` |
| `src/kms/dispatch_preparation.py` | `src/kms/dispatch/preparation.py` |
| `src/kms/dispatch_execution.py` | `src/kms/dispatch/execution.py` |
| `src/kms/dispatch_response.py` | `src/kms/dispatch/response.py` |
| `src/kms/dispatch_lifecycle_coordinator.py` | `src/kms/dispatch/lifecycle.py` |
| `src/kms/thinker_dispatch_coordinator.py` | `src/kms/dispatch/thinker_dispatch.py` |

架构边界审查：

- 只是 KMS 内部目录整理。
- 没有把调度逻辑放进 Kernel。
- 没有改变 Thinker dispatch 生命周期。
- 没有改变 Talker / Observer 对外接口。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_dispatch_lifecycle_coordinator.py tests\test_thinker_dispatch_coordinator.py tests\test_dispatch_preparation.py tests\test_dispatch_execution.py tests\test_dispatch_response.py
10 passed in 8.51s
```
## 2026-06-23：KMS routing 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/routing/` 子目录。
- 为什么改：Task Router 相关文件属于 KMS 的任务路由能力，不应该继续散落在 `src/kms` 根目录。
- 改完什么样：`TaskRoutingCoordinator` 和 `task_context_router` 统一归到 routing 包里。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/task_routing_coordinator.py` | `src/kms/routing/task_routing.py` |
| `src/kms/task_context_router.py` | `src/kms/routing/task_context_router.py` |

架构边界审查：

- 仍是 KMS 内部路由能力。
- Kernel 不参与任务选择。
- Thinker 不自己选择 task。
- Talker 不保存或修改任务状态。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_task_directory_router.py tests\test_dispatch_preparation.py tests\test_smoke_interrupt.py
26 passed
```

## 2026-06-23：KMS task 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/task/` 子目录。
- 为什么改：任务暂停、恢复、切换计划、task-local 状态过滤都属于 KMS 的任务调度支撑能力，不应该继续散落在 `src/kms` 根目录。
- 改完什么样：task 相关模块统一归到 task 包里，`KmsManager` 只通过清晰的 coordinator/planner 调用它们。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/task_coordinators.py` | `src/kms/task/coordinators.py` |
| `src/kms/task_dispatch_planner.py` | `src/kms/task/dispatch_planner.py` |
| `src/kms/task_scoped_state.py` | `src/kms/task/scoped_state.py` |

架构边界审查：

- 仍是 KMS 内部任务调度能力。
- Kernel 仍只保存和解释状态。
- Thinker 不参与 task 选择或恢复决策。
- Talker/Observer 不直接修改任务状态。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_task_switch_coordinator.py tests\test_dispatch_execution.py tests\test_task_directory_router.py tests\test_smoke_interrupt.py
29 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS Classify stage 拆分

本阶段继续按架构设计文档的 9 阶段拆 `pipeline.py`，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/pipeline_stages/classify.py`。
- 为什么改：Classify 是独立阶段，负责把事件类型映射到对应状态类别。
- 改完什么样：`pipeline.py` 继续编排阶段，但分类表放在单独 stage 文件里。

移动结果：

| 原内容 | 新位置 |
|---|---|
| `ClassifyResult` | `src/kms/pipeline_stages/classify.py` |
| `classify` | `src/kms/pipeline_stages/classify.py` |

架构边界审查：

- 仍是 KMS pipeline 的 Classify 阶段。
- Kernel reducer 只接收已经分类后的归约调用。
- 事件类别路由不进入 Thinker。

验证结果：

```text
python -m py_compile src\kms\pipeline.py src\kms\pipeline_stages\classify.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_missing_coverage.py tests\test_smoke_interrupt.py
35 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS Validate stage 拆分

本阶段继续按架构设计文档的 9 阶段拆 `pipeline.py`，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/pipeline_stages/validate.py`。
- 为什么改：Validate 是独立阶段，负责 Talker/Thinker 权限、run stale、intent version 和 belief payload 检查。
- 改完什么样：`pipeline.py` 继续编排阶段，但验证规则在单独 stage 文件里。

移动结果：

| 原内容 | 新位置 |
|---|---|
| `ValidateResult` | `src/kms/pipeline_stages/validate.py` |
| `validate` | `src/kms/pipeline_stages/validate.py` |
| `TALKER_FORBIDDEN / THINKER_ALLOWED` | `src/kms/pipeline_stages/validate.py` |

架构边界审查：

- 仍是 KMS pipeline 的 Validate 阶段。
- Kernel 不负责准入策略判断。
- Thinker stale run 防护仍在 KMS。

验证结果：

```text
python -m py_compile src\kms\pipeline.py src\kms\pipeline_stages\validate.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_missing_coverage.py tests\test_smoke_interrupt.py
35 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS Normalize stage 拆分

本阶段开始按架构设计文档的 9 阶段拆 `pipeline.py`，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/pipeline_stages/normalize.py`。
- 为什么改：Normalize 是独立阶段，负责把 Talker/Thinker 原始提交变成结构化事件，不应该继续挤在 `pipeline.py` 主文件里。
- 改完什么样：`pipeline.py` 继续编排 9 阶段，但 Normalize 的实现细节在单独 stage 文件里。

移动结果：

| 原内容 | 新位置 |
|---|---|
| `NormalizeResult` | `src/kms/pipeline_stages/normalize.py` |
| `normalize` | `src/kms/pipeline_stages/normalize.py` |
| `_normalize_from_text` | `src/kms/pipeline_stages/normalize.py` |
| `_build_event` | `src/kms/pipeline_stages/normalize.py` |
| `TALKER_REQUEST_MAP / THINKER_EVENT_MAP` | `src/kms/pipeline_stages/normalize.py` |

架构边界审查：

- 仍是 KMS pipeline 的 Normalize 阶段。
- Kernel 不负责解析用户/Thinker submission。
- Thinker 只提交事件，不决定事件是否接受。

验证结果：

```text
python -m py_compile src\kms\pipeline.py src\kms\pipeline_stages\normalize.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_missing_coverage.py tests\test_smoke_interrupt.py
35 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS runtime execution payload 拆分

本阶段继续拆 `pipeline.py` 内部小块，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/runtime/execution_payload.py`。
- 为什么改：工具执行事件会带 runtime refs，需要转换成 execution reducer 能读的 payload；这属于 Runtime Event Adapter 适配，不是 pipeline 主流程。
- 改完什么样：`pipeline.py` 继续负责 reduce 阶段编排，execution payload 细节放到 runtime 包。

移动结果：

| 原函数 | 新位置 |
|---|---|
| `_merge_execution_payload` | `src/kms/runtime/execution_payload.py::merge_execution_payload` |

架构边界审查：

- KMS 仍负责把 runtime 事件适配成 Kernel 可归约事件。
- Kernel reducer 逻辑不变。
- 工具执行事件落表语义不变。

验证结果：

```text
python -m py_compile src\kms\pipeline.py src\kms\runtime\execution_payload.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_missing_coverage.py tests\test_smoke_interrupt.py
35 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS runtime references 拆分

本阶段继续拆 `pipeline.py` 内部小块，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/runtime/references.py`。
- 为什么改：runtime message/tool/result 引用注册属于 Runtime Event Adapter 边界，不是 pipeline 主流程本身。
- 改完什么样：`pipeline.py` 仍然对外暴露 `register_runtime_references`，但具体实现放到 runtime 包。

移动结果：

| 原函数 | 新位置 |
|---|---|
| `_runtime_ref_summary` | `src/kms/runtime/references.py` |
| `_extract_runtime_ref_values` | `src/kms/runtime/references.py` |
| `register_runtime_references` | `src/kms/runtime/references.py` |

架构边界审查：

- Kernel 仍只保存 runtime refs 索引，不保存完整 transcript。
- KMS pipeline 仍负责事件进入后的引用登记。
- 外部 API 导入路径保持兼容。

验证结果：

```text
python -m py_compile src\kms\pipeline.py src\kms\runtime\references.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_missing_coverage.py tests\test_smoke_interrupt.py
35 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS transport 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/transport/` 子目录。
- 为什么改：独立 KMS service 和远端 client 属于通信边界，不应该和 KMS 调度、路由、判断模块混在根目录。
- 改完什么样：`src/kms_server.py` 继续作为兼容启动入口，但实际 app 来自 `src.kms.transport.server`。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/server.py` | `src/kms/transport/server.py` |
| `src/kms/remote.py` | `src/kms/transport/remote.py` |

架构边界审查：

- 仍是 KMS 通信入口能力。
- Kernel 通过 remote client 调用 KMS，不接管 KMS judge 逻辑。
- 独立 KMS service API 行为不变。

验证结果：

```text
python -m py_compile src\kms_server.py src\kms\transport\server.py src\kms\transport\remote.py src\kms\pipeline.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_state_alias_and_thinker_dispatch.py tests\test_smoke_interrupt.py
31 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS pipeline state alias 拆分

本阶段开始拆 `pipeline.py` 内部小块，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/state/aliases.py`。
- 为什么改：`pipeline.py` 里开头有一组“新表状态转换成旧 reducer 可识别对象”的函数，它们不是主流程，适合单独放出来。
- 改完什么样：`pipeline.py` 继续负责 9 阶段事件处理，但状态适配细节放到 `state/aliases.py`。

移动结果：

| 原函数 | 新位置 |
|---|---|
| `_intent_from_task_brief` | `src/kms/state/aliases.py::intent_from_task_brief` |
| `_plan_from_task_flow` | `src/kms/state/aliases.py::plan_from_task_flow` |
| `_beliefs_from_claims` | `src/kms/state/aliases.py::beliefs_from_claims` |
| `_commitments_from_todos` | `src/kms/state/aliases.py::commitments_from_todos` |

架构边界审查：

- 仍是 KMS pipeline 内部适配层。
- Kernel 仍是状态事实来源。
- reducer 复用方式不变。

验证结果：

```text
python -m py_compile src\kms\pipeline.py src\kms\state\aliases.py

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_pipeline_event_flow.py tests\test_state_primary_read_switch.py tests\test_state_alias_and_thinker_dispatch.py tests\test_manager_observer_views.py tests\test_smoke_interrupt.py
37 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS notification 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/notification/` 子目录。
- 为什么改：Observer/Talker 通知策略是 KMS 的独立输出能力，不应该继续散落在 `src/kms` 根目录。
- 改完什么样：通知生成、去重、节流、优先级策略统一放在 notification 包里。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/notification_coordinator.py` | `src/kms/notification/coordinator.py` |

架构边界审查：

- 仍是 KMS 通知策略能力。
- Kernel 不负责主动推送。
- Thinker 不负责通知策略。
- Talker/Observer 只消费通知。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_notification_coordinator.py tests\test_observer_notifications.py tests\test_manager_observer_views.py tests\test_dispatch_lifecycle_coordinator.py tests\test_smoke_interrupt.py
25 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS audit 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/audit/` 子目录。
- 为什么改：状态来源审计是旧表退场前的检查能力，和用户消息调度、任务路由、通知策略都不是同一类职责。
- 改完什么样：审计模块独立放在 audit 包里，API 仍通过 `/kms/state-source-audit` 暴露。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/state_source_audit.py` | `src/kms/audit/state_source.py` |

架构边界审查：

- 仍是 KMS 审计能力。
- Kernel 不负责判断架构迁移进度。
- Thinker 不参与状态来源审计。
- API 行为不变。

验证结果：

```text
python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_state_source_audit.py tests\test_state_primary_read_switch.py tests\test_legacy_fallback_audit_report.py tests\test_state_alias_and_thinker_dispatch.py tests\test_manager_observer_views.py
17 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

说明：

- 不带 `--basetemp` 的第一次运行失败在 pytest `tmp_path` 创建阶段，原因是当前沙箱不能写 `C:\Users\EDY\AppData\Local\Temp\pytest-of-EDY`。
- 加入项目内临时目录后同一组测试通过，说明不是 audit 迁移导致的代码问题。

## 2026-06-23：KMS context 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/context/` 子目录。
- 为什么改：conversation refs、kernel session 查找、dispatch context 构造都是 KMS 在正式调度前准备上下文的能力，应该放在一起。
- 改完什么样：KMS 主流程先通过 context 包准备上下文，再进入 routing / dispatch / response。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/conversation_ref_coordinator.py` | `src/kms/context/conversation_refs.py` |
| `src/kms/kernel_session_coordinator.py` | `src/kms/context/kernel_session.py` |
| `src/kms/dispatch_context.py` | `src/kms/context/dispatch_context.py` |

架构边界审查：

- 仍是 KMS 上下文准备能力。
- Kernel 只提供状态读取接口。
- Thinker 不参与上下文构造。
- Conversation refs 仍不保存完整聊天记录。

验证结果：

```text
python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_kernel_session_coordinator.py tests\test_dispatch_preparation.py tests\test_kernel_direct_reply_coordinator.py tests\test_thinker_dispatch_coordinator.py tests\test_task_conversation_refs.py tests\test_task_directory_router.py tests\test_smoke_interrupt.py
35 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS decisioning 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/decisioning/` 子目录。
- 为什么改：用户意图判断、规则 judge、LLM 调用、belief 审查都属于 KMS 的判断工具箱，不应该继续散落在 `src/kms` 根目录。
- 改完什么样：调度主流程仍在 KMS，但判断辅助能力集中到 decisioning 包里。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/intent_classifier.py` | `src/kms/decisioning/intent_classifier.py` |
| `src/kms/belief.py` | `src/kms/decisioning/belief.py` |
| `src/kms/judges.py` | `src/kms/decisioning/judges.py` |
| `src/kms/model.py` | `src/kms/decisioning/model.py` |

架构边界审查：

- 仍是 KMS 判断能力。
- Kernel 不调用 LLM 做任务调度。
- Thinker 不决定用户消息意图。
- Router/dispatch 只是调用 decisioning 的结果。

验证结果：

```text
python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider -q tests\test_intent_classifier.py tests\test_requested_user_scenarios.py tests\test_pipeline_event_flow.py tests\test_task_directory_router.py tests\test_dispatch_preparation.py tests\test_smoke_interrupt.py
62 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```

## 2026-06-23：KMS response 目录分组迁移

本阶段继续做结构整理，不改行为。

通俗说明：

- 改哪里：新增 `src/kms/response/` 子目录。
- 为什么改：Kernel 直接回复、直接回复记录、路由澄清回复都属于 KMS 面向用户的响应包装能力，不应该继续散落在 `src/kms` 根目录。
- 改完什么样：response 相关模块统一归到 response 包里，`DispatchResponseCoordinator` 继续负责把回复包装成 KMS dispatch decision。

移动结果：

| 原位置 | 新位置 |
|---|---|
| `src/kms/kernel_direct_responder.py` | `src/kms/response/kernel_direct_responder.py` |
| `src/kms/kernel_direct_reply_coordinator.py` | `src/kms/response/direct_reply.py` |
| `src/kms/route_clarification_coordinator.py` | `src/kms/response/clarification.py` |

架构边界审查：

- 仍是 KMS 内部响应包装能力。
- Kernel 只提供状态，不决定是否直接回复。
- Thinker 不会因为直接回复或澄清被唤醒。
- Talker/Observer 仍只消费返回结果和 notification。

验证结果：

```text
python -m pytest -o addopts='' -q tests\test_kernel_direct_reply_coordinator.py tests\test_route_clarification_coordinator.py tests\test_dispatch_response.py tests\test_dispatch_execution.py tests\test_task_directory_router.py tests\test_smoke_interrupt.py
32 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel -p no:cacheprovider
79 passed
```
