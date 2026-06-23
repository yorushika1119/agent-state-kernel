# 当前工作记忆与交接说明（2026-06-23）

这份文档用于在新会话或后续工作中快速恢复上下文。它记录当前项目状态、架构判断、已完成事项、剩余任务和协作偏好。

## 1. 关键路径

| 内容 | 路径 |
|---|---|
| 主项目 | `C:\program1\agent-state-kernel` |
| 真实 Hermes 部署目录 | `C:\Users\EDY\AppData\Local\hermes\hermes-agent` |
| 架构设计文档 | `C:\program1\Runtime-side Agent State Kernel 功能设计.md` |
| 当前架构文档 | `C:\program1\agent-state-kernel\docs\current-agent-architecture-2026-06-22.md` |
| 主进度文档 | `C:\program1\agent-state-kernel\docs\progress\architecture-gap-analysis-2026-06-18.md` |
| 测试说明 | `C:\program1\agent-state-kernel\docs\testing.md` |

## 2. 架构定位

当前项目目标是 Runtime-side Agent State Kernel 架构。

核心分层：

```text
Talker / Hermes
  -> KMS
  -> Kernel
  -> Thinker / Hermes Agent
```

通俗理解：

| 组件 | 职责 |
|---|---|
| Talker / Hermes | 面向用户，负责接收消息和展示回复 |
| KMS | 管流程，负责路由、打断、恢复、dispatch、通知 |
| Kernel | 管状态，负责事件、状态表、归约和 views |
| Thinker | 做任务，真正调用模型和工具执行 |

重要边界：

- KMS 是 Kernel 的上层。
- Kernel 不应该替 KMS 做任务调度决策。
- Kernel 不保存完整聊天记录，只保存摘要、状态和 runtime 引用。
- Thinker 通过 `thinker_dispatch` 生命周期接任务，不应绕过 KMS 直接跑主流程。

## 3. 已完成的主线能力

| 能力 | 当前状态 |
|---|---|
| 用户新请求打断旧任务 | 已完成 |
| stale run 防污染 | 已完成 |
| paused task 恢复 | 已完成 |
| thinker_dispatch claim / heartbeat / complete / fail | 已完成 |
| Hermes Gateway/CLI 接入 thinker_dispatch | 已完成第一版 |
| proxy mode dispatch 生命周期 | 已完成第一版 |
| Task Router | 已完成第一版 |
| LLM Router | 已接入并通过真实 smoke |
| Kernel 直接回答状态问题 | 已支持，不需要唤醒 Thinker |
| User Session / Global Task Directory | 已完成第一版 |
| task conversation refs | 已完成第一版，只存摘要和 runtime ref |
| manager_view / observer_view / talker_view | 已完成第一版 |
| NotificationCoordinator | 已完成第一版，含去重、节流、优先级和 SSE |
| 测试分层 | fast / core / integration / full 已建立 |
| 新状态表主读 | 已完成 |
| 旧状态表写入退场 | 已完成 |
| 旧表 fallback 使用审计 | 已完成第一版，含查看脚本，本轮真实链路未命中 |
| KmsManager dispatch decision 小拆分 | 已完成第一版 |
| KmsManager task dispatch planner 小拆分 | 已完成第一版 |
| Runtime Event Adapter Hermes 工具事件入口 | 已完成第一版 |
| 真实 Hermes kernel dispatch 工具事件 helper | 已完成第一版 |
| 旧表物理删除 removal-check | 已完成第一版 |

## 4. 最近完成的阶段

最近完成的是“旧状态表写入退场”。

代码变化：

- 删除旧写入开关 `KMS_WRITE_LEGACY_STATE_TABLES`。
- 删除旧表写入逻辑。
- `save_intent()` 只写 `task_brief_states`。
- `save_plan()` 只写 `task_flows`。
- `save_belief()` 只写 `claim_items`。
- `save_commitment()` 只写 `todo_obligations`。
- 保留 `get_intent / get_plan / get_beliefs / get_commitments` 的旧表读取 fallback。

测试变化：

- 删除“恢复旧表双写”的测试。
- 保留“新保存的数据不会进入旧表”的回归。
- 保留新表主读、`legacy_debug`、状态源审查相关回归。

Git：

```text
commit: 2c31253 Remove legacy state table writes
push: 成功
```

## 5. 最近验证结果

```text
python -m py_compile src\stores\sqlite_store.py
passed

python scripts\test_integration.py
111 passed in 200.09s

python scripts\live_llm_router_smoke.py
passed

python scripts\live_interrupt_demo.py --real-model --scenario interrupt
old dispatch=failed
new dispatch=completed
active_run=empty
```

说明：

- integration 变为 111 条，是因为删除了“恢复旧表双写”的测试。
- 真实 LLM Router smoke 证明模糊任务路由仍可工作。
- 真实 Hermes interrupt smoke 证明旧 run 被打断后不会污染新回复。

## 6. 当前完成度判断

| 模块 | 粗略完成度 | 说明 |
|---|---:|---|
| KMS / Kernel / Thinker 分层 | 92% | 职责基本清楚，dispatch decision 和 task dispatch planner 已从 manager 拆出 |
| 打断与恢复 | 90% | integration 和真实 Hermes smoke 通过 |
| Thinker dispatch 生命周期 | 85% | claim / heartbeat / complete / fail 已接通 |
| Task Router 多任务路由 | 75% | 支持常见指代，LLM Router 已接入 |
| Kernel 直接回答状态问题 | 80% | progress / evidence / failures / claims / todos 已支持 |
| User Session 多任务管理 | 80% | user_sessions / global_tasks / conversation refs 已有 |
| Observer / Manager / Notification | 65% | API / SSE / policy 第一版可用 |
| 新状态表迁移 | 90% | 新表主读，写入代码已切到新表 |
| 旧表退场 | 86% | 写入代码已移除，读取 fallback 已审计且有查看脚本，removal-check 已有，物理删表未做 |
| 测试体系 | 80% | fast / core / integration / full 已分层 |

当前没有发现完成不了的硬阻塞。剩下主要是收尾、加固和产品化。

## 7. 仍未完成的主要事项

| 下一步 | 原因 |
|---|---|
| 继续观察旧表 fallback 审计数据 | 决定后续能不能物理删除旧表 |
| 继续拆 `KmsManager` 主流程 | dispatch decision 和 task dispatch planner 已拆出，但主流程仍可继续分段 |
| Runtime Event Adapter 深度接 Hermes | 工具/summary/raw result 方法已有，真实 Hermes 共享 helper 已补，gateway 主流程还可继续逐步接入 |
| Observer notification WebSocket | SSE 第一版有了，WebSocket 未做 |
| Notification 高级策略 | 当前只是第一版，复杂升级/优先级还可增强 |
| 旧表物理删除 | 最后阶段再做，需要确认历史数据迁移和 fallback 使用情况 |

## 8. 测试策略

当前测试分层：

| 层级 | 命令 | 使用场景 |
|---|---|---|
| fast | `python scripts/test_fast.py` | 小改动、局部重构 |
| core | `python scripts/test_core.py` | KMS / Kernel / Router 普通核心改动 |
| integration | `python scripts/test_integration.py` | pipeline、打断、恢复、用户场景等重链路 |
| full | `python scripts/test_full.py` | 阶段性完成或怀疑跨模块影响 |

真实 smoke 不进默认测试层：

| smoke | 命令 |
|---|---|
| LLM Router | `python scripts/live_llm_router_smoke.py` |
| Hermes interrupt | `python scripts/live_interrupt_demo.py --real-model --scenario interrupt` |

## 9. 协作偏好

后续工作默认遵守：

- 中文回答。
- 不确定的事情明确说“不确定”。
- 改代码前用大白话说明：改哪里、为什么改、改完什么样。
- 代码保持简洁，不写过度防御逻辑。
- 新功能必须补测试。
- 大任务完成后 git commit 并 push GitHub。
- push 失败不重试，继续后续工作。
- 发现冗余代码或功能时，先请求删除，得到同意后再删。
- 不要把 API key 等敏感信息写进文档。

## 10. 下一步建议

上一轮建议的 fallback 使用审计已经完成第一版：

```text
legacy_state_fallback_audits
```

当前结果：

- core / integration 通过。
- 真实 LLM Router smoke 通过。
- 真实 Hermes interrupt smoke 通过。
- `data/kernel.db` 本轮 fallback audit 行数为 0。
- `scripts/report_legacy_fallback_audit.py` 已可直接查看命中情况。
- `DispatchDecision` 已从 `KmsManager` 拆出到 `src/kms/dispatch_decision.py`。
- `TaskDispatchPlanner` 已从 `KmsManager` 拆出到 `src/kms/task/dispatch_planner.py`。
- `RuntimeEventAdapter` 已支持 Hermes 常见工具事件和 summary/raw result 事件提交。
- 真实 Hermes 的 `hermes_cli/kernel_dispatch.py` 已补工具事件 helper。
- `scripts/migrate_legacy_state_tables.py --removal-check` 已可检查删表前置条件。

建议下一步：

- 暂时不要物理删除旧表。
- 继续保留 fallback 审计。
- 累积更多真实运行数据后，再做旧表物理删除方案。
- 下一步优先继续拆 `KmsManager` 主流程，或开始让真实 Gateway 工具回调逐步调用新的 kernel dispatch helper。
