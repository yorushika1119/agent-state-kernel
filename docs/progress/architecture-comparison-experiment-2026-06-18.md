# 新老架构对比实验设计：纯 Hermes Thinker vs Thinker + KMS + Kernel

> 日期：2026-06-18  
> 目的：用同一组任务对比老架构和新架构，证明新架构在状态管理和实时打断上有可观察优势。

---

## 1. 对比对象

| 组别 | 架构 | 定义 |
|---|---|---|
| A 组：老架构 | 纯 Hermes Thinker | 用户消息直接进入 thinker。状态主要留在模型上下文、对话文本和工具输出里。 |
| B 组：新架构 | Hermes Thinker + KMS + Kernel | 用户消息先进入 KMS；KMS 读取 kernel 状态后决定新任务、打断、直答状态或恢复任务。执行事件写入 kernel。 |

这个实验不比较模型聪明程度，只比较运行时架构能力。

---

## 2. 实验原则

1. 两组使用同一套用户输入和同一套模拟工具结果。
2. 不依赖真实网络和真实 LLM，避免实验结果被外部波动影响。
3. 老架构只保留 Hermes 可自然拥有的信息：输入、输出、当前 thinker run。
4. 新架构使用真实 `KmsManager`、`KernelEngine`、event reducer 和 views。
5. 结论只基于可观测指标，不夸大当前还没实现的能力。

---

## 3. 实验一：状态管理能力

### 场景

用户要求调研一个有多步计划和证据来源的任务：

```text
请调研实时打断机制的状态管理优势
```

Thinker 执行期间产生：

- 计划：收集资料、验证风险、输出结论。
- 工具调用：搜索/读取资料。
- 证据：一条结构化事实。
- 信念：一条基于证据的结论。

### A 组预期

纯 Hermes 可以在最终回答中描述这些内容，但状态散落在对话和工具输出里。

可观察结果：

| 指标 | 结果 |
|---|---|
| 是否有结构化 plan | 否 |
| 是否有 evidence id | 否 |
| 是否有 belief/confidence | 否 |
| 是否可查询当前步骤 | 否 |
| 是否可从 event log 重放 | 否 |

### B 组预期

新架构会把同样过程落到 kernel。

可观察结果：

| 指标 | 结果 |
|---|---|
| `thinker_view.plan` | 有 |
| `thinker_view.evidence` | 有 evidence id、来源、事实 |
| `thinker_view.beliefs` | 有 claim、status、confidence |
| `thinker_view.executions` | 有工具执行记录 |
| `talker_view` | 能合成当前进度 |

### 判定标准

如果 B 组能通过结构化查询拿到计划、证据、信念、执行和进度，而 A 组只能依赖对话文本，则说明新架构在状态管理上有优势。

---

## 4. 实验二：状态查询不打断

### 场景

第一条消息启动长任务：

```text
请执行一个长调研任务
```

任务运行中，用户问：

```text
现在完成到哪一步了？
```

### A 组预期

纯 Hermes 没有独立调度层。新消息会进入 thinker 处理，容易被当成新的用户请求，和当前任务争抢同一个模型执行流。

可观察结果：

| 指标 | 结果 |
|---|---|
| 状态查询是否绕过 thinker | 否 |
| 原任务是否有独立 active run 保护 | 否 |
| 是否能从结构化状态直答 | 否 |

### B 组预期

KMS 识别这是状态查询，返回 `respond_from_kernel`。

可观察结果：

| 指标 | 结果 |
|---|---|
| KMS action | `respond_from_kernel` |
| `requires_thinker` | `false` |
| active run | 保持为原 run |
| `RunInterrupted` 事件 | 不产生 |

### 判定标准

如果 B 组能直接从 kernel 回复状态，并且不打断原 thinker，则说明新架构在“状态查询不干扰执行”上有优势。

---

## 5. 实验三：实时打断和旧结果防污染

### 场景

第一条消息启动任务 A：

```text
请研究任务 A
```

任务 A 未完成时，用户发起任务 B：

```text
请改成研究任务 B
```

随后任务 A 的旧工具结果迟到。

### A 组预期

纯 Hermes 缺少 kernel 级 run generation 和 stale run 拒绝机制。旧任务结果可能仍进入可见输出或污染上下文。

可观察结果：

| 指标 | 结果 |
|---|---|
| 是否记录旧 run 被打断 | 否 |
| 是否有 active run 切换账本 | 否 |
| 是否拒绝旧 run 写入 | 否 |
| 旧结果泄漏风险 | 有 |

### B 组预期

KMS 生成新 run，kernel 记录 `RunInterrupted`，旧 run 后续事件被拒绝。

可观察结果：

| 指标 | 结果 |
|---|---|
| 第二条消息 action | `interrupt_and_replan` |
| `last_interrupted_run_id` | 指向任务 A 的 run |
| `active_run_id` | 指向任务 B 的 run |
| 旧 run 事件提交 | 被拒绝，原因包含 `Stale thinker run` |
| 新 run 事件提交 | 接受 |

### 判定标准

如果 B 组能切换 active run 并拒绝旧 run 事件，而 A 组不能，则说明新架构在实时打断和旧结果防污染上有优势。

---

## 6. 建议落地方式

新增一个确定性回归测试文件：

```text
tests/test_architecture_ab_experiment.py
```

覆盖三条断言：

1. 新架构能把执行过程变成结构化状态；老架构只有 transcript。
2. 新架构对状态查询返回 `respond_from_kernel`，不打断当前 run。
3. 新架构对明确新请求返回 `interrupt_and_replan`，并拒绝旧 run 后续写入。

---

## 7. 当前结论边界

这个实验能证明：

- 新架构的状态管理比纯 thinker 更可查询、可追溯、可恢复。
- 新架构能在状态查询时避免误打断。
- 新架构能在实时打断后防止旧 run 写入污染 kernel 状态。

这个实验还不能证明：

- KMS 语义判断已经足够稳定。
- 底层长时间工具调用一定能被强杀。
- 所有 streaming/final 输出路径都已经完全抑制旧结果。

所以结论应写成：

```text
新架构已经在状态管理和软打断调度上具备明确优势，但仍需继续强化语义分类和旧输出抑制压测。
```
