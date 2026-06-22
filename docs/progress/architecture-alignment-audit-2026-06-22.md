# 架构一致性审查：Runtime-side Agent State Kernel

日期：2026-06-22

结论：当前实现没有偏离新版架构设计文档，已经进入“主体分层对齐 + 旧实现兼容收敛”阶段。还不能说完全完成，因为旧状态表仍作为兼容输出和 reducer 过渡入口存在。

## 总览

| 设计要求 | 当前实现 | 一致性 | 后续动作 |
|---|---|---|---|
| Kernel 是底层状态内核，不是普通 memory | Kernel 负责事件、状态表、reducer、views | 已对齐 | 保持 Kernel 不直接接管 runtime transcript |
| KMS 是 Kernel 上层管理服务 | KMS 负责 dispatch、routing、interrupt/resume、notification、direct reply | 已对齐 | 继续减少 `KmsManager` 内部直接细节 |
| Thinker 通过 KMS/Kernel 协议执行 | Hermes Gateway/CLI 已接入 dispatch claim/heartbeat/complete/fail | 已对齐 | 继续补 proxy/runtime adapter 场景 |
| User Session 可绑定多个 task | 已有 `user_sessions/global_tasks/task_context_routes` | 已对齐 | 增加更多自然表达路由 smoke |
| Task Context Router 决定每句话归属哪个 task | 已支持“刚才/另一个/状态查询/明确新任务” | 基本对齐 | 持续补真实 LLM/router 回归 |
| Kernel 可直接回答状态问题，不打断 Thinker | direct responder 已支持 progress/evidence/failures/claims/todos | 已对齐 | 扩大更多用户说法覆盖 |
| 新状态表成为主读模型 | `task_brief/task_flow/claim/todo` 已主读 | 基本对齐 | 替换剩余兼容 getter 读者 |
| 旧状态表最终迁移/删除 | 已冻结业务层直接 SQL 读旧表，新增迁移脚本 | 部分对齐 | 完成历史迁移和兼容 getter 移除后再删 |
| Observer/Talker 只消费过滤后的状态 | 已有 observer/manager views、notification API、SSE | 基本对齐 | 后续做更完整 UI/推送 broker |
| Notification / Wakeup Orchestrator | 已有 coordinator、去重、节流、优先级基础策略 | 部分对齐 | 继续做升级策略和更强推送 |

## 当前已完成

| 能力 | 状态 |
|---|---|
| 用户打断旧任务，新 dispatch 接管 | 已完成并有真实 Hermes smoke |
| 旧 run stale 输出不写回用户可见结果 | 已完成 |
| KMS / Kernel / Thinker 词汇注入真实 Hermes prompt | 已完成 |
| 真实模型回答 KMS/Kernel 分工 | 已通过 smoke |
| `另一个任务当前进度？` 不打断 active task | 已完成 |
| 旧表直接 SQL 读取冻结 | 已完成 |
| 旧表到新表迁移 dry-run/write 脚本 | 已完成 |

## 仍未完成

| 问题 | 为什么还没完成 |
|---|---|
| 旧表物理删除 | `pipeline`、`engine`、`sqlite_store` 仍保留兼容 getter 和双写入口 |
| 完整历史数据迁移 | 本轮只新增迁移脚本和测试，还没有对真实库执行迁移 |
| Router 完整语义覆盖 | 规则和 LLM router 已可用，但自然语言表达空间还需要继续扩样 |
| Talker/Observer 产品化 | 当前是 API/SSE/视图层，还不是完整外部产品 |
| Notification 高级策略 | 目前只有基础去重/节流/优先级 |

## 下一步建议

| 优先级 | 下一步 |
|---|---|
| 1 | 对真实 SQLite 库先跑迁移脚本 dry-run |
| 2 | 把 `pipeline` 的 reducer 输入逐步改成新版状态对象 |
| 3 | 把 `engine` 的兼容输出改成新表主输出 + legacy debug 区 |
| 4 | 增加更多 router smoke：这个任务、另一个、不要打断、继续原来的 |
| 5 | 再评估是否可物理删除旧表 |

## 2026-06-22 补充审查：状态主读切换推进结果

| 项目 | 当前结果 | 结论 |
|---|---|---|
| 真实 SQLite 迁移 | 已备份并执行 `scripts/migrate_legacy_state_tables.py --write`，6 个 session 写入新版状态表 | 已完成一次真实库迁移，不删除旧表 |
| Pipeline reducer 输入 | 已改为从 `task_brief/task_flow/claim_items/todo_obligations` 转换出 reducer 所需旧形状对象 | 符合“新表主读，旧 reducer 逐步收敛”的过渡策略 |
| Engine 输出 | 已新增 `legacy_debug`，新版字段继续作为主输出；旧顶层字段暂时保留兼容 | 没有偏离架构，但仍处于兼容期 |
| Router smoke | 真实 LLM router smoke 通过，模糊任务可由 LLM 选择，状态查询可直接 Kernel 回答 | 方向正确 |
| Hermes interrupt smoke | 真实 Hermes + DeepSeek 打断 smoke 通过，旧 dispatch failed，新 dispatch completed | 方向正确 |
| 旧表物理删除 | 暂不删除 | 仍有兼容 getter、双写、历史 fallback 和旧调用方过渡职责 |

最新判断：

- 项目实现逻辑仍然贴合新版架构设计文档。
- 当前已经从“影子新表”推进到“新表主读 + 旧表兼容输出”。
- 还没到最终形态，因为旧命名字段仍在 API 视图顶层保留。
- 后续要把调用方逐步迁到新版字段，再收窄旧字段到 `legacy_debug`，最后评估删旧表。
