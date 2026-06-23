# KMS 模块边界审查

日期：2026-06-23

## 结论

`src/kms` 下面文件变多了，但当前主要是“职责拆分后的文件数量增加”，不是明显冗余。

现在不建议直接删文件。更合适的下一步是先做目录分组，把同一类模块放到一起，等接口稳定后再考虑合并少数小文件。

## 当前判断

| 模块 | 当前职责 | 判断 | 建议 |
|---|---|---|---|
| `manager.py` | KMS 用户消息总控 | 保留 | 继续变薄，只做分支编排 |
| `dispatch/preparation.py` | 准备路由、session、intent flags | 保留 | 已移动到 `kms/dispatch/` |
| `dispatch/execution.py` | 创建 run、task、thinker dispatch | 保留 | 已移动到 `kms/dispatch/` |
| `dispatch/decision.py` | DispatchDecision 返回模型 | 保留 | 已移动到 `kms/dispatch/` |
| `dispatch/lifecycle.py` | run 激活、stale run、dispatch 底层生命周期 | 保留 | 已移动到 `kms/dispatch/` |
| `dispatch/thinker_dispatch.py` | 创建 thinker dispatch 并记录 conversation ref | 暂留 | 稳定后可评估并入 `dispatch/execution.py` 或 `dispatch/lifecycle.py` |
| `task/dispatch_planner.py` | 规划 active/paused task 切换 | 保留 | 属于 KMS 调度核心 |
| `routing/task_routing.py` | observe user session、读取 global tasks、调用 router | 保留 | 已移动到 `kms/routing/` |
| `routing/task_context_router.py` | 具体路由规则和 LLM route | 保留 | 已移动到 `kms/routing/`，后续可拆 rule/llm/score |
| `task/coordinators.py` | interrupt/resume/task switch | 保留 | 后续可按 `interrupt.py`、`resume.py` 拆分，但不急 |
| `response/kernel_direct_responder.py` | 从 Kernel 状态生成直接回复文本 | 保留 | 属于 KMS 直接回答能力 |
| `dispatch/response.py` | 包装澄清、Kernel 直接回复、no-resume 回复 | 保留 | 已移动到 `kms/dispatch/` |
| `response/direct_reply.py` | 记录 Kernel 直接回复 conversation ref | 暂留 | 作为 `DispatchResponseCoordinator` 的底层 helper |
| `response/clarification.py` | 生成澄清问题并记录引用 | 暂留 | 作为 `DispatchResponseCoordinator` 的底层 helper |
| `context/conversation_refs.py` | conversation refs 统一写入 | 保留 | 不应分散到多个模块 |
| `notification/coordinator.py` | observer/talker 通知策略 | 保留 | 独立职责明确 |
| `task/scoped_state.py` | task-local 状态过滤 | 保留 | 支撑直接回复和视图 |
| `state/aliases.py` | task-first 状态到 reducer 旧对象形状的适配 | 保留 | 属于 KMS pipeline 内部兼容层 |
| `runtime/references.py` | runtime message/tool/result 引用索引 | 保留 | 属于 Runtime Event Adapter 边界 |
| `runtime/execution_payload.py` | runtime refs 到 execution reducer payload 的适配 | 保留 | 属于 Runtime Event Adapter 边界 |
| `pipeline_stages/normalize.py` | Normalize 阶段：submission 到 CognitiveEvent | 保留 | 属于 KMS 9 阶段 pipeline |
| `pipeline_stages/validate.py` | Validate 阶段：权限、版本和事件完整性检查 | 保留 | 属于 KMS 9 阶段 pipeline |
| `pipeline_stages/classify.py` | Classify 阶段：事件类别路由 | 保留 | 属于 KMS 9 阶段 pipeline |
| `pipeline_stages/arbitrate.py` | Arbitrate 阶段：candidate 提升和 judge 仲裁 | 保留 | 属于 KMS 9 阶段 pipeline |
| `pipeline_stages/event_log.py` | EventLog 前置：event metadata 分配 | 保留 | 属于 KMS 9 阶段 pipeline |
| `pipeline_stages/summarize.py` | Summarize 阶段：刷新和生成进度摘要 | 保留 | 属于 KMS 9 阶段 pipeline |
| `pipeline_stages/gate.py` | Gate 阶段：Talker 输出可见性和安全检查 | 保留 | 属于 KMS 9 阶段 pipeline |
| `audit/state_source.py` | 新旧状态来源审计 | 保留 | 旧表退场前需要 |
| `pipeline.py` | KMS 事件 pipeline | 保留但偏大 | 后续单独拆，不和 dispatch 混在一起 |
| `decisioning/intent_classifier.py` | 用户消息意图判断 | 保留 | 属于 KMS 判断能力 |
| `decisioning/belief.py` / `decisioning/judges.py` / `decisioning/model.py` | 评审、模型调用、judge | 保留 | 属于 KMS 判断能力 |
| `transport/server.py` / `transport/remote.py` | KMS 服务和远端 client | 保留 | 和 runtime 接口相关 |

## 不建议现在删除的原因

| 原因 | 说明 |
|---|---|
| 架构还在迁移 | 现在先拆清职责，比减少文件数更重要 |
| 测试刚稳定 | 直接合并文件容易制造无意义回归 |
| 旧表还未物理删除 | 部分审计/兼容模块仍有价值 |
| Hermes 真实链路仍在接入 | dispatch 相关模块还可能继续调整 |

## 后续推荐结构

建议后续做“移动目录”，不是马上删代码：

```text
src/kms/
  dispatch/
    decision.py
    preparation.py
    execution.py
    lifecycle.py
    thinker_dispatch.py
  routing/
    task_routing.py
    task_context_router.py
  context/
    conversation_refs.py
    kernel_session.py
    dispatch_context.py
  response/
    kernel_direct_responder.py
    direct_reply.py
    clarification.py
  task/
    dispatch_planner.py
    coordinators.py
    scoped_state.py
  state/
    aliases.py
  runtime/
    references.py
    execution_payload.py
  pipeline_stages/
    normalize.py
    validate.py
    classify.py
    arbitrate.py
    event_log.py
    summarize.py
    gate.py
  notification/
    coordinator.py
  audit/
    state_source.py
  decisioning/
    intent_classifier.py
    belief.py
    judges.py
    model.py
  transport/
    server.py
    remote.py
```

## 可合并候选

这些不是现在必须做，只是后续观察点：

| 候选 | 原因 | 建议时机 |
|---|---|---|
| `thinker_dispatch_coordinator.py` | 文件较小，和 dispatch execution 强相关 | dispatch 生命周期完全稳定后 |
| `response/direct_reply.py` + `response/clarification.py` | 都是在包装 KMS 直接返回并记录 conversation ref | response 接口稳定后 |
| `task_context_router.py` 内部规则 | 文件较大，规则/LLM/候选评分混在一起 | Router 行为继续扩展时 |
| `pipeline.py` | 文件很大，包含 normalize/reduce/gate/sync | 旧表物理删除后 |

## 防偏离检查

后续整理时必须守住：

| 规则 | 说明 |
|---|---|
| 调度不进 Kernel | Kernel 只保存状态和生成视图 |
| Thinker 不自己选任务 | Thinker 只消费 KMS 下发的 dispatch |
| Talker 不直接改状态 | Talker 只展示和提交用户消息 |
| KMS 不保存完整聊天记录 | 只保存 conversation refs 和摘要 |
| 先移动再合并 | 避免一边移动目录一边改行为 |

## 下一步建议

`DispatchResponseCoordinator` 已完成，`src/kms/dispatch/`、`src/kms/routing/`、`src/kms/task/`、`src/kms/response/`、`src/kms/notification/` 和 `src/kms/audit/` 目录分组也已完成。下一步不要继续增加散落文件，建议回到功能主线：补真实 runtime 事件桥接和旧表物理退场前置检查。
