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
| 旧表物理删除 removal-check / drop 工具 | 已完成第一版，真实库未执行 drop |
| KMS pipeline stage 拆分 | 已完成第一轮，Normalize/Validate/Classify/Arbitrate/EventLog/Reduce/Summarize/Gate/Sync 已拆出 |
| 新表-only 主链路测试 | 已完成第一版，58 passed |
| KmsManager DispatchDecision builder 拆分 | 已完成，manager 末尾拼装逻辑已移出 |
| KmsManager response 分支拆分 | 已完成第一版，澄清/直接回复/no-resume 分支已移入 `DispatchResponseCoordinator` |
| 真实 Hermes Gateway runtime helper 深接入 | 已完成第一版，工具事件走专用 helper，interrupted run 会上报 `ActionBlocked` |

## 4. 最近完成的阶段

最近完成的是“KMS pipeline stage 拆分”“旧状态表物理删除准备”和“KmsManager 返回对象拼装拆分”。

代码变化：

- `src/kms/pipeline.py` 保留总编排，阶段细节移到 `src/kms/pipeline_stages/`。
- `Reduce / Summarize / Gate / Sync` 已从大 pipeline 文件中拆出。
- `SqliteStore` 新增 `create_legacy_state_tables=False`，支持新表-only 模式。
- `SqliteStore` 支持 `KERNEL_CREATE_LEGACY_STATE_TABLES=0` 环境变量。
- `scripts/migrate_legacy_state_tables.py` 新增 `--drop-legacy-tables`。
- `scripts/test_new_table_only.py` 可重复验证无 legacy 表主链路。
- `thinker_run_decision_from_execution(...)` 接管 execution result 到 `DispatchDecision` 的转换。
- 默认仍保留 `get_intent / get_plan / get_beliefs / get_commitments` 的旧表读取 fallback。

测试变化：

- 新增新表-only Store 回归。
- 新增 legacy drop 临时库回归。
- 保留新表主读、`legacy_debug`、状态源审查相关回归。

Git：

```text
latest local commits:
92e07da Split KMS gate pipeline stage
9a686bc Split KMS sync pipeline stage
72ab438 Split KMS reduce pipeline stage
push: 暂未执行
```

## 5. 最近验证结果

```text
python -m py_compile src\stores\sqlite_store.py scripts\migrate_legacy_state_tables.py
passed

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel-legacy-drop -p no:cacheprovider -q tests\test_state_primary_read_switch.py tests\test_legacy_state_migration.py tests\test_state_source_audit.py tests\test_legacy_fallback_audit_report.py
14 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel-legacy-drop-core -p no:cacheprovider
80 passed

python scripts\test_new_table_only.py --basetemp .tmp\pytest-agent-state-kernel-new-table-only -p no:cacheprovider
58 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel-legacy-env-core -p no:cacheprovider
81 passed

python -m pytest -o addopts='' --basetemp .tmp\pytest-agent-state-kernel-decision-builder -p no:cacheprovider -q tests\test_dispatch_execution.py tests\test_dispatch_response.py tests\test_manager_observer_views.py tests\test_smoke_interrupt.py
16 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel-decision-builder-core -p no:cacheprovider
81 passed
```

说明：

- 最新 core 为 81 条，因为新增了新表-only Store 回归和环境变量开关回归。
- 新表-only 主链路脚本已验证 pipeline/manager/observer/dispatch/interrupt smoke。
- 真实库没有执行 `--drop-legacy-tables`。

## 6. 当前完成度判断

| 模块 | 粗略完成度 | 说明 |
|---|---:|---|
| KMS / Kernel / Thinker 分层 | 93% | 职责基本清楚，dispatch decision、task dispatch planner、response 分支和 component wiring 已从 manager 拆出 |
| 打断与恢复 | 90% | integration 和真实 Hermes smoke 通过 |
| Thinker dispatch 生命周期 | 85% | claim / heartbeat / complete / fail 已接通 |
| Task Router 多任务路由 | 75% | 支持常见指代，LLM Router 已接入 |
| Kernel 直接回答状态问题 | 80% | progress / evidence / failures / claims / todos 已支持 |
| User Session 多任务管理 | 80% | user_sessions / global_tasks / conversation refs 已有 |
| Observer / Manager / Notification | 75% | API / SSE / WebSocket / policy 第一版可用，连续失败升级已补 |
| 新状态表迁移 | 90% | 新表主读，写入代码已切到新表 |
| 旧表退场 | 90% | 写入代码已移除，读取 fallback 已审计，真实库 removal-check 通过，物理删表未执行 |
| 测试体系 | 86% | fast / core / integration / full 已分层，新表-only 覆盖扩到 111 条 |

当前没有发现完成不了的硬阻塞。剩下主要是收尾、加固和产品化。

## 7. 仍未完成的主要事项

| 下一步 | 原因 |
|---|---|
| 扩大新表-only integration / real smoke | 新表-only 已扩到 111 条，后续还要继续覆盖真实 DB 的长期运行场景 |
| 继续拆 `KmsManager` 主流程 | response 分支也已拆出，但 dispatch 主流程仍可继续收敛 |
| Runtime Event Adapter 深度接 Hermes | Gateway 工具事件和 ActionBlocked 已接入，后续可补更多主流程事件 |
| Observer notification 推送产品化 | SSE / WebSocket 第一版有了，后续还要补连接管理和前端消费示例 |
| Notification 高级策略 | 连续失败升级已有，后续还可补更细的去重、节流、优先级规则 |
| 旧表物理删除 | 工具有了，最后阶段再对真实库执行 |

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
| Hermes interrupt | `python scripts/live_tool_interrupt_smoke.py` |

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
- `scripts/migrate_legacy_state_tables.py --drop-legacy-tables` 已可在安全时删除 legacy 状态表。

建议下一步：

- 暂时不要直接删除真实库旧表。
- 继续保留 fallback 审计。
- 继续拆 `KmsManager` 主流程中剩余的编排细节。
- 让真实 Gateway 工具回调逐步调用新的 kernel dispatch helper。
- 补真实 DB 长时间运行后的旧表 removal-check 复核，再考虑物理删表。

## 11. 2026-06-23 本轮收尾状态

本轮继续沿着新版架构文档推进，没有改变核心分层：

| 本轮事项 | 当前结果 |
|---|---|
| 新表-only 主链路覆盖 | `scripts/test_new_table_only.py` 扩到 109 条，通过 |
| 真实 LLM Router smoke | 在 `KERNEL_CREATE_LEGACY_STATE_TABLES=0` 下通过 |
| 真实 Hermes tool interrupt smoke | 在 `KERNEL_CREATE_LEGACY_STATE_TABLES=0` 下通过，旧 dispatch failed，新 dispatch completed |
| KmsManager 拆分 | component wiring 已拆到 `src/kms/manager_components.py` |
| Runtime ActionBlocked | 项目内 adapter 和真实 Hermes helper 均已支持 |
| Observer/Talker 推送 | Notification WebSocket 第一版已支持 |
| Notification 策略 | 同 task 第三次 `task_failed` 会升级为 urgent/critical |

最终验证记录：

```text
python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel-final-core -p no:cacheprovider
83 passed

python scripts\test_new_table_only.py --basetemp .tmp\pytest-agent-state-kernel-final-new-table-only -p no:cacheprovider
109 passed
```

当前判断：

- 没有发现偏离架构设计文档。
- 没有发现完成不了的硬阻塞。
- 剩余主要是继续收敛 KMS 主流程、补真实 Gateway 更深接入、最后评估真实库旧表物理删除。

## 12. 2026-06-23 Gateway Helper 深接入与 KmsManager Response 拆分

本轮继续按架构文档推进，没有改变分层：

| 本轮事项 | 当前结果 |
|---|---|
| 真实 Hermes Gateway helper 深接入 | 工具开始/完成/失败走专用 helper，打断时补 `ActionBlocked` |
| KmsManager response 分支拆分 | 澄清、Kernel 直接回复、no-resume 回复移入 `DispatchResponseCoordinator` |
| 真实 DB removal-check | `safe_to_remove=true`，`fallback_hit_count=0`，但未删表 |
| 真实 tool interrupt smoke | 通过，旧 dispatch failed，新 dispatch completed |

验证记录：

```text
cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python -m pytest -o addopts='' -q tests\gateway\test_busy_session_ack.py tests\gateway\test_proxy_mode.py tests\hermes_cli\test_kernel_dispatch.py
65 passed, 1 skipped

python -m pytest -o addopts='' -q tests\gateway\test_interrupt_demo_output.py
7 passed

python scripts\test_core.py --basetemp .tmp\pytest-agent-state-kernel-gateway-helper-core -p no:cacheprovider
83 passed

python scripts\test_new_table_only.py --basetemp .tmp\pytest-agent-state-kernel-gateway-helper-new-table-only -p no:cacheprovider
111 passed

KERNEL_CREATE_LEGACY_STATE_TABLES=0 python scripts\live_tool_interrupt_smoke.py
passed
```

注意：

- live tool interrupt smoke 输出里有 `ModelCall HTTP 404`，我确认这不是 Hermes -> Kernel 上报失败，而是 KMS 内部真实模型调用返回 404 后走了降级路径。
- 我不确定这是当前 DeepSeek base_url 配置问题，还是该 smoke 场景里不需要 LLM 路由导致的可忽略噪声；本轮没有强行改模型调用。
- 真实库旧表仍未物理删除。
