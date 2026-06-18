# 指定场景测试报告：状态直答与恢复第一次任务

> 日期：2026-06-18  
> 目标：验证用户指定的两种 KMS 调度场景。

---

## 1. 测试文件

新增回归测试：

```text
tests/test_requested_user_scenarios.py
```

运行命令：

```text
python -m pytest -q tests/test_requested_user_scenarios.py
```

结果：

```text
3 passed
```

与已有打断 smoke 测试一起验证：

```text
python -m pytest -q tests/test_requested_user_scenarios.py tests/test_smoke_interrupt.py
```

结果：

```text
11 passed
```

---

## 2. 场景一：第二次提问由 kernel 直接回复，不打断 thinker

### 用户流程

```text
USER#1: 请执行第一个长任务，整理实时打断机制材料
USER#2: 现在完成到哪一步了？
```

### 左右对比

| 验证项 | 老架构：纯 Hermes Thinker | 新架构：Thinker + KMS + Kernel |
|---|---|---|
| 第二次问题处理方式 | 进入 thinker 对话流 | KMS 判断为状态查询 |
| 是否由 kernel 直接回复 | 否 | 是，返回 `respond_from_kernel` |
| 是否需要 thinker 处理第二次问题 | 是 | 否，`requires_thinker = false` |
| 第二次问题使用的 run | 仍混在同一 thinker 流里 | 复用第一次 run，不创建新 run |
| 是否打断第一次任务 | 无运行时保证 | 不打断 |
| 是否产生 `RunInterrupted` | 没有结构化事件 | 不产生 |
| 第一次 thinker 后续写入 | 没有 run 级校验 | 第一次 run 的 `ToolCompleted` 被接受 |
| 第一次 run 是否可完成 | 没有 kernel run 状态 | `complete_run` 成功 |
| 可证明性 | 只能看 transcript | 可查 `active_run_id`、event log 和 run 状态 |

老架构下，“现在完成到哪一步了？”可能也能被 thinker 回答，但这是临时对话行为。新架构能明确证明：这次提问由 kernel 状态回答，并且没有打断第一次请求的 thinker。

### 实际验证细节

测试名：

```text
test_status_question_responds_from_kernel_and_original_thinker_continues
```

验证点：

| 验证项 | 结果 |
|---|---|
| 第二次请求 action | `respond_from_kernel` |
| 第二次请求 task_action | `respond_from_kernel` |
| 是否需要 thinker | `false` |
| 返回 run_id | 仍是第一次请求的 run |
| active_run_id | 仍是第一次请求的 run |
| 是否产生 `RunInterrupted` | 否 |
| 第一次 run 后续 `ToolCompleted` | 接受 |
| 第一次 run `complete_run` | 成功 |

### 结论

该场景通过。  
KMS 能识别状态查询，直接从 kernel 回复，并保证第一次请求的 thinker 任务继续正常执行。  
相比老架构，新架构的优势是“状态查询不占用 thinker、不打断 run、可由 kernel 状态回答”。

---

## 3. 场景二：第二次无关请求打断，第三次继续第一次任务

### 用户流程

```text
USER#1: 请研究任务 A：实时打断机制的状态恢复方案
USER#2: 请改成回答一个无关请求：解释一下 Python 装饰器
USER#3: 继续刚才的任务
```

### 左右对比

| 验证项 | 老架构：纯 Hermes Thinker | 新架构：Thinker + KMS + Kernel |
|---|---|---|
| 第二次无关请求处理方式 | 进入 thinker 对话流 | KMS 返回 `interrupt_and_replan` |
| 第一次任务是否被结构化暂停 | 否 | 是，保存为 paused task snapshot |
| 第二次请求是否创建独立 task | 否 | 是，`task_action = start_new_task` |
| 是否记录被打断 run | 否 | 是，`last_interrupted_run_id = first.run_id` |
| 第三次“继续刚才”判断方式 | thinker 从 transcript 里猜 | KMS 识别为 `resume_previous_task` |
| 第三次恢复动作 | 无结构化恢复 | `task_action = continue_paused_task` |
| 恢复的是哪个任务 | 不能稳定确认 | 明确恢复第一次任务 A 的 `task_id` |
| 是否有 `resume_context` | 否 | 是，包含第一次任务目标 |
| 是否能排除恢复成第二次任务 | 不能稳定确认 | 可以，`resume_context.goal` 不包含“Python 装饰器” |
| 恢复后的 run | 没有 continuation run 概念 | 新 continuation run |
| 恢复后写入 | 没有 run 级校验 | continuation run 写入被接受 |
| 第二次任务迟到写入 | 不会被 stale run 机制拒绝 | 被拒绝，原因包含 `Stale thinker run` |

老架构下，第三次“继续刚才的任务”只能依赖 thinker 理解上下文。新架构可以明确证明恢复的是第一次任务 A，因为恢复动作绑定的是第一次任务的 `task_id` 和 `resume_context`。

### 实际验证细节

测试名：

```text
test_unrelated_second_request_then_resume_first_task
```

验证点：

| 验证项 | 结果 |
|---|---|
| 第二次请求 action | `interrupt_and_replan` |
| 第二次请求 task_action | `start_new_task` |
| 第二次请求是否创建新 task | 是 |
| 暂停的 task | 第一次任务 A |
| 第三次请求 action | `interrupt_and_replan` |
| 第三次请求 task_action | `continue_paused_task` |
| 第三次请求 reason | `resume_previous_task` |
| 恢复的 task_id | 第一次任务 A 的 task_id |
| `resume_context.goal` | 包含“实时打断机制” |
| `resume_context.goal` | 不包含“Python 装饰器” |
| 恢复后的 active_task_id | 第一次任务 A |
| 恢复后的 active_run_id | 新 continuation run |
| continuation run 写入 | 接受 |
| 第二次无关任务迟到写入 | 拒绝，原因包含 `Stale thinker run` |

### 结论

该场景通过。  
KMS 可以在第二次无关请求后暂停第一次任务，并在第三次“继续刚才”时恢复第一次任务。  
相比老架构，新架构的优势是“第一次任务有 task_id 和 snapshot，恢复时有明确 `resume_context`，不是靠模型猜”。

## 4. 场景三：kernel 已有信息时直接回复

### 用户流程

```text
USER#1: 请研究任务 A：整理状态治理材料
USER#2: 刚才哪里失败了？
USER#3: 目前有什么证据？
USER#4: 当前 run 是哪个？
USER#5: 请改成处理另一个无关任务
USER#6: 上一个任务还能继续吗？
```

### 左右对比

| 验证项 | 老架构：纯 Hermes Thinker | 新架构：Thinker + KMS + Kernel |
|---|---|---|
| 查询失败原因 | 需要 thinker 看 transcript | KMS 从 execution ledger 直接回复 |
| 查询证据 | 需要 thinker 回忆或总结 | KMS 从 evidence store 直接回复 |
| 查询 active run | 老架构没有 kernel run 状态 | KMS 从 session link 直接回复 |
| 查询是否可恢复 | 老架构没有 paused task snapshot | KMS 从 task snapshot 直接回复 |
| 是否打断当前 thinker | 无运行时保证 | 不打断，返回 `respond_from_kernel` |

### 实际验证细节

测试名：

```text
test_kernel_answerable_queries_cover_failures_evidence_run_and_resume_state
```

验证点：

| 用户问题 | 新架构结果 |
|---|---|
| `刚才哪里失败了？` | `respond_from_kernel`，回复包含失败工具 `local.read` |
| `目前有什么证据？` | `respond_from_kernel`，回复包含 `ev_kernel_design` |
| `当前 run 是哪个？` | `respond_from_kernel`，回复包含当前 active run |
| `上一个任务还能继续吗？` | `respond_from_kernel`，回复包含“可以继续” |

### 结论

该场景通过。  
这说明 `kernel_answerable_query` 应该作为 KMS dispatch 的一等意图，而不是只把“进度查询”硬编码成特殊情况。

---

## 5. 总结

这些测试证明：

1. 老架构在两个场景里只能依赖 thinker transcript，没有结构化调度保证。
2. 新架构中，状态查询不会误打断正在执行的 thinker。
3. 新架构中，无关新请求会打断并暂停旧任务。
4. 用户要求继续时，KMS 能恢复被暂停的第一次任务。
5. 恢复后旧 run 仍受 stale run 保护，迟到写入不会污染当前状态。
6. kernel 已有信息时，KMS 可以直接回复失败、证据、run 和恢复状态查询。

当前仍需注意：

- “继续刚才”目前仍依赖规则 marker。
- 语义更模糊的表达还需要后续 LLM judge 或更强分类器支持。
