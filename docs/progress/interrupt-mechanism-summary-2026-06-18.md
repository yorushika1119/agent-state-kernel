# Hermes + KMS + Kernel 实时打断机制总结

> 日期：2026-06-18  
> 范围：梳理当前“类似 Codex 的实时打断机制”实现状态、架构边界、实际效果、已知问题和后续计划。

---

## 1. 一句话结论

当前已经实现了一个可工作的“软打断”机制：用户在 Hermes 正在执行任务时发起新请求，Hermes 会先把新消息交给 KMS 判断；KMS 根据 kernel 中的状态决定是打断当前 thinker、直接从 kernel 回复、开始新任务，还是恢复被暂停的任务。

但它还不是最终形态。当前最大短板是：KMS 的请求判断仍然主要依赖规则和关键词，不是真正稳定的语义理解。

---

## 2. 架构定位

| 模块 | 定位 | 负责什么 | 不负责什么 |
|---|---|---|---|
| Talker | 用户交互层 | 接收用户问题、展示回答、以后负责自然语言表达 | 不直接执行复杂任务 |
| KMS | kernel 上层管理层 | 判断用户新请求类型、决定是否打断、是否直接回复、是否恢复旧任务 | 不保存底层状态账本 |
| Kernel | 状态内核 | 管理 session、run、event log、reducer、views、task snapshot | 不判断“用户这句话要不要打断” |
| Thinker / Hermes | 执行器 | 根据 KMS 决策执行任务、调用工具、产生结果和事件 | 不拥有最终调度权 |

这条边界很关键：管理和调度应该在 KMS，状态落表和视图生成应该在 kernel。之前的实现已经把这部分从 kernel 中拆到了 `src/kms`。

---

## 3. 当前请求流

```text
用户新消息
  ↓
Hermes Gateway / CLI
  ↓
调用 KMS: /kms/dispatch-user-message
  ↓
KMS 读取 kernel 当前状态
  ↓
KMS 返回 DispatchDecision
  ↓
Hermes 根据决策执行：
  - interrupt_and_replan: 打断当前任务，启动新 run
  - start_new_task: 开始新任务
  - respond_from_kernel: 直接回复，不打断 thinker
  - continue_paused_task: 带 resume_context 继续旧任务
  ↓
Hermes 执行期间持续上报事件到 kernel
  ↓
kernel 落表、reducer 更新状态、views 对外提供当前视图
```

---

## 4. 已完成内容

### 4.1 KMS / Kernel 分层

- 新增 `src/kms` 包，KMS 逻辑从 kernel 中拆出。
- 新增 KMS dispatch 能力，由 KMS 返回 `DispatchDecision`。
- kernel 保持为状态内核，只负责记录、归约、查询。

### 4.2 Hermes 侧接入

- Hermes Gateway 已接入 KMS 决策。
- Hermes CLI 已接入 KMS 决策。
- Hermes 可以消费这些 action：
  - `interrupt_and_replan`
  - `start_new_task`
  - `respond_from_kernel`
  - `continue_paused_task`
- `respond_from_kernel` 支持在 thinker 忙碌时直接返回 kernel 状态，不打断当前任务。
- `continue_paused_task` 支持把 `resume_context` 注入 thinker，让 thinker 基于旧任务状态继续。

### 4.3 事件桥接

Hermes 已经能把执行过程中的关键事件上报给 kernel：

- `ToolStarted`
- `ToolCompleted`
- `ToolFailed`
- `ReasoningSummary`
- `RawResultAvailable`
- `TaskCompleted`
- `TaskFailed`

普通工具事件会进入 thinker 视图。失败事件会进入 risks，也会影响 sync view 的 blocking reason。`ReasoningSummary` 和 `RawResultAvailable` 当前主要用于 debug 视图。

### 4.4 打断和旧 run 保护

- 新请求可以打断当前 run。
- 被打断的 run 会被标记为 interrupted / paused。
- 新 run 接管后，旧 run 的后续写入会被 kernel 拒绝，避免旧结果污染新任务。
- 当前是软打断，不是操作系统级强杀。

### 4.5 恢复能力

当前“继续刚才的任务”不是恢复旧线程，而是：

1. KMS 找到之前被暂停的任务快照。
2. KMS 生成新的 run。
3. KMS 把旧任务状态整理成 `resume_context`。
4. Hermes 把 `resume_context` 注入 thinker。
5. Thinker 根据状态继续执行。

也就是说，旧 run 本身不会复活；继续任务是“基于结构化状态重新启动一个 continuation run”。

---

## 5. 当前打断机制有哪些功能

| 功能 | 当前状态 | 说明 |
|---|---|---|
| 同 session 内实时打断 | 已实现 | 用户新请求到来时，可以中断当前 thinker 执行 |
| 新请求重新规划 | 已实现 | `interrupt_and_replan` 会启动新 run 处理新请求 |
| 直接从 kernel 回复 | 已实现 | 例如查询进度时，可以 `respond_from_kernel`，不打断 thinker |
| 恢复被暂停任务 | 已实现基础版 | 显式说“继续刚才”等语句时，KMS 会生成 continuation run |
| 事件桥接 | 已实现基础版 | Hermes 工具、推理摘要、结果、失败事件能落到 kernel |
| 旧 run 防污染 | 已实现 | 新 run 接管后，旧 run 后续事件会被拒绝 |
| 真实并行多 thinker | 未实现 | 当前同一 session 内仍是单活跃 thinker 模式 |
| 语义级意图理解 | 未完善 | 当前主要依赖规则和 marker |

---

## 6. 实际效果对比

### 场景：长任务执行中，用户问“现在进度怎么样？”

| 架构 | 行为 | 用户看到的效果 |
|---|---|---|
| 纯 Hermes | Hermes 容易把这句话当成新请求，从而打断当前任务 | 先提示 interrupt，然后回答“进度”这个新请求 |
| Hermes + KMS + Kernel | KMS 判断这是状态查询，返回 `respond_from_kernel` | 直接从 kernel 回复当前状态，原 thinker 继续执行 |

示例效果：

```text
USER#1: first long task
KMS: start_new_task

USER#2: what is the progress
KMS: respond_from_kernel

ASSISTANT:
等待任务开始。thinker is still working on the current task.

后台原任务继续执行，完成后输出：
FINAL:first long task
```

这个对比不能夸大。它说明新架构已经具备“状态查询不打断”的能力，但当前判断依赖规则命中。如果用户换一种说法，KMS 可能仍然误判。

---

## 7. 与 Codex 思路的相似点

当前实现借鉴的是 Codex 类交互里的核心思路：

- 用户可以在模型工作时继续输入。
- 新输入不是简单排队，而是进入调度层。
- 调度层决定是否取消当前工作、是否开始新工作、是否基于已有状态回答。
- 旧任务状态不只靠模型上下文记忆，而是有结构化状态可恢复。

不同点是：Codex 的实现更完整，有更成熟的任务生命周期、取消传播、流式输出抑制和恢复策略。我们当前还处在基础可用阶段。

---

## 8. 已验证测试

当前已有测试覆盖：

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/test_smoke_interrupt.py` | kernel 侧打断 smoke 测试 |
| `tests/test_pipeline_event_flow.py` | Hermes 事件落表和视图表现 |
| `tests/gateway/test_busy_session_ack.py` | Hermes Gateway 忙碌状态和 KMS 决策消费 |
| `tests/gateway/test_interrupt_demo_output.py` | 可见的“用户打断旧任务，模型回复新请求”demo |
| `tests/cli/test_cli_init.py` | Hermes CLI 对 KMS 决策的消费 |

最近一次验证结果：

```text
Hermes CLI tests: 55 passed
Hermes Gateway busy tests: 29 passed, 1 skipped
Hermes interrupt demo: 1 passed
Kernel pipeline event flow: 16 passed
Kernel interrupt smoke: 4 passed, 1 skipped
```

---

## 9. 当前主要问题

### 9.1 KMS 判断还是规则驱动

这是最大问题。

当前 KMS 通过一些 marker 判断用户请求属于哪一类，例如：

- 新任务
- 继续旧任务
- 查询当前状态
- 当前任务的补充说明

这种方式能跑通 demo，但泛化能力弱。用户换一种表达，就可能误判。

### 9.2 “不打断”容易被误判

在 talker + thinker + kernel 架构下，确实存在这种情况：

用户问的问题，kernel 已经能回答，不需要 thinker 介入；但如果 KMS 没识别出来，就可能错误打断正在执行的 thinker。

例如：

```text
用户：现在做到哪里了？
用户：刚才那个任务有失败吗？
用户：你现在在查什么？
用户：目前有什么证据？
```

这些都更适合从 kernel 直接回复，而不是打断 thinker。

### 9.3 恢复旧任务仍是基础版

当前恢复依赖 task snapshot 和 `resume_context`，不是把旧线程挂起后原地继续。

这条路线是合理的，但后续需要让 `resume_context` 更完整，包括：

- 已完成步骤
- 当前步骤
- 剩余步骤
- 已收集证据
- 已知失败
- 上次被什么请求打断

### 9.4 软打断不等于强杀

如果 thinker 正在执行一个长时间卡住的工具调用，当前机制不能保证立即停止底层进程。

它能做的是：

- 告诉 Hermes 当前 run 已经被打断。
- 让新 run 接管 session。
- 拒绝旧 run 的后续写入。
- 抑制旧结果污染新任务。

### 9.5 旧结果泄漏风险还需要继续压测

正式 demo 已经通过，但之前高频实验中观察到过旧任务结果偶尔泄漏的风险。

后续应重点检查：

- Gateway 的 run generation 判断
- agent interrupt 传播
- stream 输出抑制
- final 输出抑制
- 旧 run 写入被拒绝后的用户可见输出处理

### 9.6 Gateway / CLI 有重复逻辑

Gateway 和 CLI 都各自消费 KMS 决策。现在功能能跑，但后续容易行为漂移。

可以抽一个共享 helper，让二者走同一套 KMS decision handling。

---

## 10. 后续计划

### P0：把 KMS 判断升级为语义分类

目标：稳定区分这些类型：

| 类型 | 行为 |
|---|---|
| 状态查询 | `respond_from_kernel`，不打断 thinker |
| 明确新任务 | `interrupt_and_replan` 或 `start_new_task` |
| 当前任务补充说明 | 合并到当前任务上下文，必要时重规划 |
| 继续刚才任务 | `continue_paused_task` |
| 无关闲聊 | 优先直接回复，不影响 thinker |

可以先做轻量版本：

1. 规则判断保留为 fast path。
2. 规则不确定时调用 LLM judge。
3. LLM judge 输出结构化分类和置信度。
4. 低置信度时不自动打断，先走保守策略。

### P1：完善 `respond_from_kernel`

目标：让 kernel 直答更像真正的状态助手。

需要增强：

- 当前 run 状态
- 当前步骤
- 最近工具调用
- 是否有失败
- 已完成多少
- 是否可以继续旧任务
- 是否正在等待 thinker

### P1：完善恢复上下文

目标：让 thinker 恢复旧任务时拿到更完整的任务状态，而不是只拿到粗略摘要。

建议 `resume_context` 包含：

- paused run id
- interrupted reason
- original user goal
- plan steps
- completed steps
- current step
- pending steps
- evidence summary
- recent failures
- last assistant visible output

### P2：强化旧 run 输出抑制

目标：不只拒绝旧事件写入，也要保证旧 run 的最终文本不会再显示给用户。

需要覆盖：

- streaming chunk
- tool result
- final answer
- exception path
- timeout path

### P2：统一 Gateway / CLI 决策消费

目标：减少重复逻辑，避免 Gateway 和 CLI 行为不一致。

可以新增共享函数，例如：

```text
handle_kms_dispatch_decision(decision, session_state, agent_runner)
```

### P2：补 A/B 实际效果报告

目标：形成可展示的表格，比较纯 Hermes 与 Hermes + KMS + Kernel 的实际行为。

建议至少覆盖：

| 场景 | 预期差异 |
|---|---|
| 长任务中查询进度 | 新架构不打断 |
| 长任务中发起明确新任务 | 新架构打断并切新 run |
| 新任务完成后说继续刚才 | 新架构基于 snapshot 恢复 |
| 工具失败后查询失败原因 | 新架构从 kernel 直接回复 |
| 证据冲突后询问结论 | 新架构能显示 safe/unsafe 边界 |

---

## 11. 当前阶段判断

当前不是“从零新增打断机制”，而是在完善已经跑通的打断机制。

已经完成的是：

- KMS 接管调度入口。
- Hermes 能消费 KMS 决策。
- Kernel 能记录打断、事件、旧 run 状态。
- 基础 interrupt / direct response / resume 都能跑通。

还没有完全完成的是：

- KMS 语义判断。
- 复杂恢复。
- 旧输出完全抑制。
- 实际 A/B 对比脚本。
- Talker 正式接入。

所以更准确的阶段描述是：

```text
基础闭环已完成，正在从“规则可用”升级到“语义可靠、可展示、可压测”的阶段。
```

