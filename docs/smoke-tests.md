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

## 3. Real Hermes model interrupt smoke

用途：验证真实 Hermes Gateway + 真实 DeepSeek 模型 + KMS/Kernel dispatch 的完整打断链路。

前置条件：

- 真实 Hermes 部署目录存在：
  `C:\Users\EDY\AppData\Local\hermes\hermes-agent`
- Hermes 配置中有可用模型和 API key。
- 当前脚本使用内存 Kernel，不需要单独启动 `uvicorn`。

命令：

```powershell
cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python scripts\live_interrupt_demo.py --real-model --scenario interrupt
```

预期关键输出：

```text
KERNEL_AFTER_USER#1: action=start_new_task
KERNEL_AFTER_USER#2: action=interrupt_and_replan
THINKER_DISPATCHES:
  first dispatch status=failed
  second dispatch status=completed
TASK_CONVERSATION_REFS:
  assistant source=gateway_streamed_reply message_ref_id=...
  # proxy mode 时 source 可能是 gateway_proxy_streamed_reply
ACTIVE_RUN_AFTER_DONE: empty
```

含义：

- 第一条长任务被第二条用户消息打断。
- 新请求获得新的 thinker dispatch。
- 旧 dispatch 不再成为最终回复。
- 最终回复的外部 `message_id` 会回传到 Kernel 的 task conversation refs。

## 4. Hermes proxy mode dispatch check

用途：验证 Gateway 开启 proxy mode 时，远端 thinker 的结果仍按 KMS dispatch 生命周期回写。

当前自动化覆盖：

```powershell
cd C:\Users\EDY\AppData\Local\hermes\hermes-agent
python -m pytest -o addopts='' tests\gateway\test_proxy_mode.py -q
```

预期关键行为：

```text
proxy claim dispatch
proxy streamed reply -> already_sent=True
conversation ref source=gateway_proxy_streamed_reply
stale proxy generation -> interrupted=True
```

含义：

- proxy mode 不绕过 KMS dispatch。
- proxy 流式回复已交付时不会重复发送最终消息。
- 旧 proxy run 被打断后不会误标成成功完成。

## 运行策略

| 场景 | 建议 |
|---|---|
| 改普通规则或 reducer | 跑相关 pytest |
| 改 KMS 调度、router、dispatch | 跑相关 pytest + 全量 pytest |
| 改真实 Hermes / LLM 集成 | 额外跑对应 live smoke |
| push GitHub 前 | 至少跑全量 pytest |

live smoke 会访问真实模型或真实 Hermes 目录，耗时和结果可能受环境影响，不放进默认测试集。
