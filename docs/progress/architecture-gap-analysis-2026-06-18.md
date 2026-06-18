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

- `src/kms/dispatch_context.py`

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

- `src/kms/kernel_direct_responder.py`
  - 负责从 Kernel 状态直接构造回复；
  - 覆盖 progress / failures / evidence / resume / run；
  - 不唤醒 Thinker。
- `src/kms/task_coordinators.py`
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
