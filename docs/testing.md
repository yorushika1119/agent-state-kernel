# 测试分层

项目测试按使用场景分层，避免每次小改都跑全量。

| 层级 | 命令 | 什么时候跑 |
|---|---|---|
| fast | `python scripts/test_fast.py` | 小改动、局部重构、提交前快速自检 |
| kms-fast | `python scripts/test_kms_fast.py` | 只改 KMS 小组件、分类、response、dispatch helper 时 |
| kms-dispatch | `python scripts/test_kms_dispatch.py` | 改 KMS 调度、任务切换、conversation refs 时 |
| kms-integration | `python scripts/test_kms_integration.py` | 改任务路由、打断恢复、KMS 用户场景时 |
| core | `python scripts/test_core.py` | KMS/Kernel/Router 的普通核心改动 |
| integration | `python scripts/test_integration.py` | pipeline、打断、恢复、用户场景等重集成链路 |
| full | `python scripts/test_full.py` | 阶段性完成、准备提交或怀疑有跨模块影响时 |

真实 smoke 不放进默认测试层：

| smoke | 命令 | 用途 |
|---|---|---|
| LLM Router | `python scripts/live_llm_router_smoke.py` | 验证真实模型能处理模糊任务路由 |
| Hermes interrupt | `python scripts/live_interrupt_demo.py --real-model --scenario interrupt` | 验证真实 Hermes 打断链路 |

旧表迁移相关验证：

| 场景 | 命令 | 用途 |
|---|---|---|
| core 默认不写旧表 | `python scripts/test_core.py` | 验证核心链路只写新表 |
| integration 默认不写旧表 | `python scripts/test_integration.py` | 验证 pipeline/打断/恢复重链路只写新表 |

建议节奏：

1. 平时小改先跑 `fast`。
2. 只改 KMS 小组件时跑 `kms-fast`。
3. 改 KMS 调度、任务切换、conversation refs 时跑 `kms-dispatch`。
4. 改任务路由、打断恢复、用户场景时跑 `kms-integration`。
5. 改状态、视图、通知、Router 跨模块时跑 `core`。
6. 改 pipeline、打断、恢复、用户场景时跑 `integration`。
7. 一个阶段完成后跑 `full`。
8. 真实模型或 Hermes 相关改动后，再单独跑 smoke。

当前原则是不删测试，只减少不必要的全量测试次数。

如果只是改 `KmsManager` 拆分、dispatch response、task coordinator 这类局部逻辑，优先跑：

```powershell
python scripts\test_kms_fast.py
python scripts\test_kms_dispatch.py
```

如果涉及 `Task Context Router`、`smoke_interrupt` 这类较重链路，再跑：

```powershell
python scripts\test_kms_integration.py
```

只有涉及新表-only、legacy 退场或 reducer 主链路时，才跑：

```powershell
python scripts\test_new_table_only.py
```

最近一次验证结果：

```text
python scripts/test_core.py --basetemp .tmp\pytest-agent-state-kernel-gateway-helper-core -p no:cacheprovider
83 passed

python scripts/test_new_table_only.py --basetemp .tmp\pytest-agent-state-kernel-gateway-helper-new-table-only -p no:cacheprovider
111 passed

python scripts/test_kms_fast.py --basetemp .tmp\pytest-agent-state-kernel-kms-fast-2 -p no:cacheprovider
32 passed in 15.39s

python scripts/test_kms_dispatch.py --basetemp .tmp\pytest-agent-state-kernel-kms-dispatch-2 -p no:cacheprovider
40 passed in 28.66s

python scripts/test_kms_integration.py --basetemp .tmp\pytest-agent-state-kernel-kms-integration -p no:cacheprovider
64 passed in 172.49s

python scripts/test_core.py --basetemp .tmp\pytest-agent-state-kernel-test-tier-core -p no:cacheprovider
86 passed
```
