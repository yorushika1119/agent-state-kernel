# 测试分层

项目测试按使用场景分层，避免每次小改都跑全量。

| 层级 | 命令 | 什么时候跑 |
|---|---|---|
| fast | `python scripts/test_fast.py` | 小改动、局部重构、提交前快速自检 |
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
| core 关闭旧表写入 | `KMS_WRITE_LEGACY_STATE_TABLES=0 python scripts/test_core.py` | 验证核心链路只写新表 |
| integration 关闭旧表写入 | `KMS_WRITE_LEGACY_STATE_TABLES=0 python scripts/test_integration.py` | 验证 pipeline/打断/恢复重链路只写新表 |

建议节奏：

1. 平时小改先跑 `fast`。
2. 改调度、状态、视图时跑 `core`。
3. 改 pipeline、打断、恢复、用户场景时跑 `integration`。
4. 一个阶段完成后跑 `full`。
5. 真实模型或 Hermes 相关改动后，再单独跑 smoke。

当前原则是不删测试，只减少不必要的全量测试次数。

最近一次验证结果：

```text
python scripts/test_fast.py
43 passed in 18.68s

python scripts/test_core.py
74 passed in 52.60s

python scripts/test_integration.py
待重新测量

KMS_WRITE_LEGACY_STATE_TABLES=0 python scripts/test_integration.py
111 passed in 114.55s

python scripts/test_full.py
117 passed in 154.26s
```
