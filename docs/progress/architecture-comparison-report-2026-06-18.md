# 新老架构对比报告：纯 Hermes Thinker vs Thinker + KMS + Kernel

> 日期：2026-06-18  
> 结论：新架构在状态管理和实时打断上已经体现出明确优势。  
> 验证方式：确定性 A/B 实验测试，避免依赖真实网络和真实 LLM。

---

## 1. 对比对象

| 组别 | 架构 | 说明 |
|---|---|---|
| 老架构 | 纯 Hermes Thinker | 用户消息直接进入 thinker，状态主要存在于模型上下文、对话文本和工具输出中。 |
| 新架构 | Hermes Thinker + KMS + Kernel | 用户消息先进入 KMS，KMS 根据 kernel 状态决定是否打断、直答状态、开始新任务或恢复旧任务。 |

本报告比较的是运行时架构能力，不比较模型本身的推理能力。

---

## 2. 实验覆盖

本次对比覆盖两个核心目标：

1. 状态管理  
   看执行过程是否能被结构化记录、查询、回放和用于恢复。

2. 实时打断  
   看用户新消息到来时，系统是否能区分“查询状态”和“切换任务”，并防止旧任务结果污染新任务。

对应测试文件：

```text
tests/test_architecture_ab_experiment.py
```

验证结果：

```text
python -m pytest -q tests/test_architecture_ab_experiment.py
3 passed

python -m pytest -q tests/test_architecture_ab_experiment.py tests/test_smoke_interrupt.py
11 passed
```

---

## 3. 状态管理对比

### 场景

用户发起一个调研任务，thinker 执行过程中产生：

- 计划步骤
- 工具调用
- 证据
- 基于证据的信念
- 当前进度

### 老架构表现

纯 Hermes Thinker 可以在回答中描述这些内容，但它们主要散落在 transcript 中。

| 能力 | 老架构 |
|---|---|
| 查询当前计划 | 不能结构化查询 |
| 查询当前步骤 | 不能稳定查询 |
| 查询工具调用记录 | 需要翻对话 |
| 查询证据来源 | 需要翻对话 |
| 查询结论置信度 | 依赖模型是否写出来 |
| 事后回放 | 没有事件账本 |
| 换 thinker 继续 | 基本不可迁移 |

老架构的问题不是 thinker 完全做不到任务，而是任务做完后缺少稳定的状态沉淀。

### 新架构表现

新架构会把 thinker 的执行过程写入 kernel，并通过 views 暴露。

| 能力 | 新架构 |
|---|---|
| 查询当前计划 | `thinker_view.plan` |
| 查询当前步骤 | `thinker_view.current_step` |
| 查询工具调用记录 | `thinker_view.executions` |
| 查询证据来源 | `thinker_view.evidence` |
| 查询结论置信度 | `thinker_view.beliefs` |
| 查询用户可说进度 | `talker_view` |
| 事后回放 | event log |
| 换 thinker 继续 | 可基于 kernel 状态继续 |

### 状态管理结论

新架构把“模型工作过程”变成了“可查询的运行时状态”。  
这带来的优势是：

- 状态不只存在于上下文里。
- 证据、工具、计划、结论可以分开管理。
- 后续可以基于 snapshot 和 event log 恢复任务。
- Talker 可以从安全视图中组织回答，而不是直接依赖 thinker 记忆。

---

## 4. 状态查询不打断对比

### 场景

长任务执行中，用户问：

```text
现在完成到哪一步了？
```

### 老架构表现

纯 Hermes Thinker 没有独立调度层。用户新消息会进入同一个 thinker 流程，容易和当前长任务争抢执行上下文。

| 指标 | 老架构 |
|---|---|
| 是否知道这是状态查询 | 依赖 thinker 当前判断 |
| 是否绕过 thinker | 否 |
| 是否保持原任务继续 | 不稳定 |
| 是否能从状态账本直答 | 否 |

### 新架构表现

KMS 判断这是状态查询，返回：

```text
respond_from_kernel
```

测试中验证到：

| 指标 | 新架构 |
|---|---|
| KMS action | `respond_from_kernel` |
| `requires_thinker` | `false` |
| active run | 保持为原 run |
| `RunInterrupted` | 不产生 |
| 回复来源 | kernel 状态 |

### 状态查询结论

新架构可以做到“问进度不打断任务”。  
这是老架构很难稳定保证的，因为老架构没有独立于 thinker 的状态层和调度层。

---

## 5. 实时打断对比

### 场景

用户先启动任务 A：

```text
请研究任务 A
```

任务 A 未完成时，用户改口：

```text
请改成研究任务 B
```

随后任务 A 的旧工具结果迟到。

### 老架构表现

纯 Hermes Thinker 没有 kernel 级 run generation，也没有 stale run 拒绝机制。

| 指标 | 老架构 |
|---|---|
| 是否记录旧 run 被打断 | 否 |
| 是否切换 active run 账本 | 否 |
| 是否拒绝旧 run 后续写入 | 否 |
| 旧结果污染风险 | 有 |

老架构可以尝试在提示词里要求 thinker 忽略旧任务，但这不是运行时强约束。

### 新架构表现

KMS 生成新 run，并将旧 run 标记为 interrupted / paused。

测试中验证到：

| 指标 | 新架构 |
|---|---|
| 第二条消息 action | `interrupt_and_replan` |
| active run | 切换到任务 B 的 run |
| `last_interrupted_run_id` | 指向任务 A 的 run |
| `last_interrupting_run_id` | 指向任务 B 的 run |
| 旧 run 事件 | 被拒绝 |
| 拒绝原因 | `Stale thinker run` |
| 新 run 事件 | 正常接受 |

### 实时打断结论

新架构已经具备软打断能力：

- 能识别同一 session 内的新请求。
- 能切换 active run。
- 能记录旧 run 被谁打断。
- 能拒绝旧 run 的后续状态写入。

这比纯 thinker 靠上下文自觉忽略旧结果更可靠。

---

## 6. 总体对比

| 维度 | 老架构：纯 Hermes Thinker | 新架构：Thinker + KMS + Kernel |
|---|---|---|
| 状态存储 | 对话上下文和工具输出 | kernel event log + derived views |
| 状态查询 | 翻对话、靠模型记忆 | 查询 `thinker_view` / `talker_view` |
| 计划管理 | 模型内部维护 | `PlanState` |
| 证据管理 | 散落在上下文 | `EvidenceItem` |
| 结论管理 | 模型文本表达 | `BeliefItem` + confidence |
| 进度表达 | 模型临时总结 | `ProgressState` |
| 状态查询打断风险 | 高 | 低，命中时 `respond_from_kernel` |
| 实时打断 | 依赖 thinker 配合 | KMS 调度 + kernel 记录 |
| 旧结果防污染 | 弱 | stale run 拒绝 |
| 恢复任务 | 依赖上下文 | 基于 task snapshot / resume_context |

---

## 7. 当前优势

新架构的主要优势不是让 thinker 更聪明，而是让 thinker 的工作过程有了运行时治理：

1. 状态可见  
   当前任务、步骤、证据、风险和执行记录可以被查询。

2. 状态可控  
   KMS 可以根据状态决定直答、打断、继续或新建任务。

3. 状态可恢复  
   被暂停任务可以基于 snapshot 生成 continuation run。

4. 旧结果可隔离  
   旧 run 迟到事件不会再污染 kernel 状态。

---

## 8. 结论边界

这次对比已经能证明：

- 新架构在状态管理上优于纯 thinker。
- 新架构在状态查询不打断方面优于纯 thinker。
- 新架构在软打断和旧 run 状态防污染方面优于纯 thinker。

但还不能证明：

- KMS 的语义分类已经足够稳定。
- 长时间卡住的底层工具调用一定能被立即强杀。
- 所有 streaming / final / exception / timeout 输出路径都已经完全防止旧结果泄漏。

---

## 9. 最终判断

更准确的结论是：

```text
新架构已经在状态管理和软打断调度上具备明确优势。
它解决的是纯 thinker 缺少结构化状态、缺少调度层、缺少旧 run 隔离的问题。
下一步重点应放在 KMS 语义分类、respond_from_kernel 直答质量和旧输出抑制压测上。
```
