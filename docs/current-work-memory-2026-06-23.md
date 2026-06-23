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
| KMS / Kernel / Thinker 分层 | 90% | 职责基本清楚 |
| 打断与恢复 | 90% | integration 和真实 Hermes smoke 通过 |
| Thinker dispatch 生命周期 | 85% | claim / heartbeat / complete / fail 已接通 |
| Task Router 多任务路由 | 75% | 支持常见指代，LLM Router 已接入 |
| Kernel 直接回答状态问题 | 80% | progress / evidence / failures / claims / todos 已支持 |
| User Session 多任务管理 | 80% | user_sessions / global_tasks / conversation refs 已有 |
| Observer / Manager / Notification | 65% | API / SSE / policy 第一版可用 |
| 新状态表迁移 | 90% | 新表主读，写入代码已切到新表 |
| 旧表退场 | 80% | 写入代码已移除，读取 fallback 和物理删表未做 |
| 测试体系 | 80% | fast / core / integration / full 已分层 |

当前没有发现完成不了的硬阻塞。剩下主要是收尾、加固和产品化。

## 7. 仍未完成的主要事项

| 下一步 | 原因 |
|---|---|
| 观察旧表 fallback 是否仍被真实使用 | 决定后续能不能物理删除旧表 |
| 继续拆 `KmsManager` | 当前仍偏大，调度分支还集中 |
| Runtime Event Adapter 深度接 Hermes | 通用封装已有，真实 Hermes 事件还可接得更完整 |
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

建议下一步先做：

```text
旧表 fallback 使用审计
```

原因：

- 旧表写入已经退场。
- 现在真正阻碍物理删表的是读取 fallback。
- 需要知道真实运行中是否还会读到旧表。

可行做法：

- 在旧表 fallback 读取路径增加轻量统计或日志。
- 跑 core / integration / 真实 smoke。
- 如果没有 fallback 命中，再评估物理删表迁移。
