# 真实链路测试报告：Hermes Thinker + KMS + Kernel

> 日期：2026-06-18  
> 目标：在确定性 A/B 测试之后，继续验证真实 Hermes Gateway 链路中的状态管理和实时打断能力。

---

## 1. 本次测试范围

本次测试不再只跑 `agent-state-kernel` 内部模拟实验，而是进入 Hermes 工程：

```text
C:\Users\EDY\AppData\Local\hermes\hermes-agent
```

覆盖三类测试：

1. Hermes Gateway + in-memory kernel demo
2. Hermes 独立 live interrupt demo 脚本
3. Hermes Gateway + 真实 kernel HTTP 服务 live 测试

其中模型仍使用可控 fake agent，不调用真实 LLM。  
这样做是为了测试运行时架构，而不是测试模型回答质量。

---

## 2. 测试一：Hermes Gateway 可见输出 demo

### 命令

```text
python -u -m pytest -o addopts='' tests\gateway\test_interrupt_demo_output.py -q -s
```

### 测试能力

验证真实 GatewayRunner 路径中：

- 第一条用户消息创建新任务。
- 第二条用户消息触发打断。
- 旧任务不会输出最终结果。
- 新任务能输出最终结果。

### 关键输出

```text
USER#1: first long task
KERNEL_AFTER_USER#1: {'action': 'start_new_task', 'session_id': 'ask_7349b57ac06b', 'run_id': 'run_7eec1c6bf21c'}
USER#2: interrupt and answer only this
KERNEL_AFTER_USER#2: {'action': 'interrupt_and_replan', 'session_id': 'ask_7349b57ac06b', 'run_id': 'run_3407583f691c'}
ASSISTANT_OUTPUTS:
  [1] ⚡ Interrupting current task. I'll respond to your message shortly.
  [2] FINAL:interrupt and answer only this
ACTIVE_RUN_AFTER_DONE:
```

### 结果

```text
1 passed
```

### 结论

该测试证明：

- Gateway 能消费 KMS 的 `interrupt_and_replan`。
- 旧任务 `FINAL:first long task` 没有输出。
- 新任务 `FINAL:interrupt and answer only this` 正常输出。

---

## 3. 测试二：独立 live interrupt demo 脚本

### 初次运行发现的问题

命令：

```text
python -u scripts\live_interrupt_demo.py
```

初次结果：

```text
KERNEL_AFTER_USER#1: {'action': None, 'session_id': None, 'run_id': None}
KERNEL_AFTER_USER#2: {'action': None, 'session_id': None, 'run_id': None}
ASSISTANT_OUTPUTS:
  [1] FINAL:first long task
  [2] FINAL:interrupt and answer only this
Kernel dispatch failed ... KMS manager not initialized
```

原因：

```text
scripts/live_interrupt_demo.py
```

只初始化了：

- `kernel_api_server._store`
- `kernel_api_server._engine`

但没有初始化：

- `kernel_api_server._kms_manager`

因此 `/kms/dispatch-user-message` 无法工作。

### 修复

已修改：

```text
C:\Users\EDY\AppData\Local\hermes\hermes-agent\scripts\live_interrupt_demo.py
```

补上：

```text
from src.kms import KmsManager
kernel_api_server._kms_manager = KmsManager(store, engine)
```

并在 finally 中恢复原值。

### 修复后运行结果

```text
USER#1: first long task
KERNEL_AFTER_USER#1: {'action': 'start_new_task', 'session_id': 'ask_cc104550c9ae', 'run_id': 'run_972c764955b6'}
USER#2: interrupt and answer only this
KERNEL_AFTER_USER#2: {'action': 'interrupt_and_replan', 'session_id': 'ask_cc104550c9ae', 'run_id': 'run_3fc22c1305b1'}
ACTIVE_RUN_DURING_SECOND_TURN: run_3fc22c1305b1
ASSISTANT_OUTPUTS:
  [1] ⚡ Interrupting current task. I'll respond to your message shortly.
  [2] FINAL:interrupt and answer only this
RUNNER_ACTIVE_RUN_MAP: {}
KERNEL_SESSION: ask_cc104550c9ae
ACTIVE_RUN_AFTER_DONE:
```

### 结论

修复后独立 demo 正常证明：

- 第一条消息进入 `start_new_task`。
- 第二条消息进入 `interrupt_and_replan`。
- active run 在第二轮期间切换到新 run。
- 旧任务最终结果没有泄漏。

---

## 4. 测试三：Gateway KMS 决策测试

### 命令

```text
python -u -m pytest -o addopts='' tests\gateway\test_busy_session_ack.py::TestBusySessionKernelDispatch -q -s
```

### 测试能力

覆盖：

- `respond_from_kernel`：状态直答，不打断 thinker。
- `interrupt_and_replan`：KMS 决策强制打断当前 thinker。
- `start_new_task`：明确新任务时重置并重新 dispatch。

### 结果

```text
3 passed
```

### 结论

Gateway busy-session 逻辑能正确消费 KMS 决策。

---

## 5. 测试四：真实 kernel HTTP live 测试

### 测试方式

临时启动真实 kernel HTTP 服务：

```text
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8420
```

然后在 Hermes 中设置：

```text
HERMES_GATEWAY_LIVE_KERNEL_URL=http://127.0.0.1:8420
```

运行 live 测试：

```text
python -u -m pytest -o addopts='' tests\gateway\test_busy_session_ack.py::TestBusySessionKernelLiveDispatch::test_live_kernel_interrupt_and_stale_run_rejection -q -s
```

测试结束后停止 kernel 服务。

### 测试能力

这个测试走真实 HTTP，不是内存 ASGITransport。它验证：

- Gateway 能调用真实 kernel `/kms/dispatch-user-message`。
- 第一条消息返回 `start_new_task`。
- 第二条消息返回 `interrupt_and_replan`。
- Gateway 调用当前 running agent 的 `interrupt()`。
- 旧 run 的 `RawResultAvailable` 被 kernel 拒绝。
- 新 run 的 `RawResultAvailable` 被 kernel 接受。
- 当前 run 可以正常 complete。

### 结果

```text
1 passed
```

### 结论

真实 HTTP 链路通过，说明 Hermes Gateway 与 kernel 服务之间的 KMS 调度和 stale run 拒绝机制可工作。

---

## 6. 本次真实测试总结果

| 测试 | 命令 | 结果 |
|---|---|---|
| Gateway 可见输出 demo | `test_interrupt_demo_output.py` | `1 passed` |
| 独立 live demo 脚本 | `scripts/live_interrupt_demo.py` | 先失败，修复后通过 |
| Gateway KMS 决策测试 | `TestBusySessionKernelDispatch` | `3 passed` |
| Gateway + 真实 kernel HTTP live 测试 | `TestBusySessionKernelLiveDispatch::test_live_kernel_interrupt_and_stale_run_rejection` | `1 passed` |

---

## 7. 真实测试结论

本次真实测试能证明：

1. 新架构的实时打断链路可以跑通。
2. Hermes Gateway 能正确消费 KMS 返回的 `interrupt_and_replan`。
3. 新 run 能接管 session。
4. 旧 run 迟到结果不会写入 kernel。
5. 状态查询和打断决策不是只存在于 kernel 内部测试里，Hermes Gateway 链路也能消费。

---

## 8. 当前仍需继续验证的边界

本次测试还不能证明：

- 真实 LLM 长任务在所有工具调用阶段都能被及时停止。
- 所有 streaming chunk 都能被完全抑制。
- 所有 final / exception / timeout 路径都没有旧结果泄漏。
- KMS 语义分类对自然表达足够稳定。

下一步更适合继续做：

```text
真实 LLM + 短工具循环 + 中途用户插话 + streaming 输出抑制压测
```

---

## 9. 阶段判断

```text
确定性测试证明了机制正确；
真实 Gateway 测试证明了 Hermes 接入链路可工作；
真实 HTTP live 测试证明了跨进程 kernel 调度可工作。

下一阶段重点是压测真实模型和真实工具调用下的输出抑制边界。
```
