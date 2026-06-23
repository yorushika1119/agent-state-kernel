# 旧状态表物理删除评估（2026-06-23）

## 结论

暂时不删除旧状态表。

当前已经完成：

- 新数据只写新版 task-first 表。
- 旧表写入代码已移除。
- 旧表 fallback 读取已可审计。
- 本轮 core / integration / 真实 smoke 未发现 fallback 命中。

但这还不等于可以安全删表。原因是一次验证只能覆盖当前测试和 smoke 路径，不能证明所有历史 DB、手工调试路径、未覆盖的老数据恢复路径都不再需要旧表。

## 涉及旧表

| 旧表 | 新表 | 当前状态 |
|---|---|---|
| `intent_states` | `task_brief_states` | 不再写入，只读 fallback |
| `plan_states` | `task_flows` | 不再写入，只读 fallback |
| `belief_items` | `claim_items` | 不再写入，只读 fallback |
| `commitments` | `todo_obligations` | 不再写入，只读 fallback |

## 删除前置条件

必须同时满足：

| 条件 | 验收方式 |
|---|---|
| fallback audit 连续多轮真实运行 0 命中 | `python scripts/report_legacy_fallback_audit.py` |
| 真实历史 DB 已完成迁移或确认无旧数据依赖 | `scripts/migrate_legacy_state_tables.py --write` 后复查 |
| core / integration / real smoke 通过 | 分层测试和真实 smoke |
| `legacy_debug` 不再需要旧 getter | 检查 `engine.py` 和相关测试 |
| 有 DB 备份 | 删除前复制 `data/kernel.db` |

## 当前验证记录

```text
python scripts/test_core.py
79 passed in 42.58s

python scripts/test_integration.py
115 passed in 129.03s

python scripts/live_llm_router_smoke.py
passed

python scripts/live_interrupt_demo.py --real-model --scenario interrupt
old dispatch=failed
new dispatch=completed
active_run=empty

python scripts/report_legacy_fallback_audit.py
ROWS=0
HIT_COUNT=0
NO_LEGACY_FALLBACK_HITS

python scripts/migrate_legacy_state_tables.py data/kernel.db --removal-check
safe_to_remove=true
unmigrated_sessions=0
fallback_hit_count=0
legacy_rows.intent_states=6
```

## 风险

| 风险 | 影响 |
|---|---|
| 历史 DB 仍只有旧表数据 | 旧 session 的状态无法恢复 |
| 未覆盖路径调用旧 getter | 删除后运行时报 SQL 错误 |
| `legacy_debug` 仍需要旧形状输出 | 管理/调试视图信息变少 |
| 迁移脚本漏迁部分旧数据 | 部分历史 claim/todo 丢失 |

## 推荐删除流程

1. 连续多轮真实运行后确认 fallback audit 仍为 0。
2. 对生产/真实 DB 执行一次 dry-run 迁移检查。
3. 备份 DB。
4. 移除旧 getter fallback 代码。
5. 移除 `legacy_debug` 对旧 getter 的依赖。
6. 再跑 core / integration / real smoke。
7. 最后再做物理删表迁移。

## 下一步建议

短期不要删表。

下一步更合适的是：

- 继续保留 fallback audit；
- 把 `KmsManager` 继续拆小；
- 让 Runtime Event Adapter 更完整地接 Hermes 事件；
- 等真实运行积累更多 0 命中证据后，再进入删表实施阶段。
