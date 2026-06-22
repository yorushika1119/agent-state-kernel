# Live Smoke Tests

这些脚本用于验证真实运行链路，不属于默认 `pytest` 全量测试。

默认测试只验证确定性逻辑：

```powershell
python -m pytest -o addopts='' tests -q
```

## 1. LLM Router smoke

用途：验证规则路由低置信时，真实 DeepSeek fallback 能否把模糊用户输入路由到正确 task。

前置条件：

- `.env` 中配置 `DEEPSEEK_API_KEY`
- 本脚本会启用 `KmsManager(enable_llm_router=True)`

命令：

```powershell
python scripts\live_llm_router_smoke.py
```

预期关键输出：

```text
RULE_DECISION=ask_clarification
FINAL_DECISION=select_existing
FINAL_TASK_ACTION=continue_paused_task
```

含义：

- 规则 router 单独判断时不确定，需要澄清。
- LLM router fallback 选择了已有 task。
- KMS 最终把任务切回被选中的 paused task。

## 2. Tool interrupt smoke

用途：验证 Hermes Gateway 打断真实长运行工具进程后，旧 run 的迟到结果不会污染用户可见输出。

前置条件：

- 已存在真实 Hermes 部署目录，默认路径：
  `C:\Users\EDY\AppData\Local\hermes\hermes-agent`
- 如路径不同，设置 `HERMES_AGENT_ROOT`

命令：

```powershell
python scripts\live_tool_interrupt_smoke.py
```

预期关键输出：

```text
interrupt_received=True
process_was_terminated=True
late_tool_result_attempted=True
```

含义：

- 旧任务工具进程确实被启动。
- 新用户请求触发 interrupt。
- 旧工具进程被终止。
- 旧 run 的迟到结果被拒绝。

## 运行策略

| 场景 | 建议 |
|---|---|
| 改普通规则或 reducer | 跑相关 pytest |
| 改 KMS 调度、router、dispatch | 跑相关 pytest + 全量 pytest |
| 改真实 Hermes / LLM 集成 | 额外跑对应 live smoke |
| push GitHub 前 | 至少跑全量 pytest |

live smoke 会访问真实模型或真实 Hermes 目录，耗时和结果可能受环境影响，不放进默认测试集。
