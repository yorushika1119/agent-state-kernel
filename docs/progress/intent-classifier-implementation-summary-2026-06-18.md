# KMS DispatchIntent 实现总结

> 日期：2026-06-18  
> 目标：参考 Codex turn control 思路，把 KMS 用户消息调度从散落 marker 判断推进到结构化 intent 分类。

---

## 1. 是否偏离功能设计

结论：没有偏离。

对照 `Runtime-side Agent State Kernel 功能设计.md`：

| 设计要求 | 当前改动 |
|---|---|
| KMS 是唯一状态解释层 | 用户消息先进入 KMS intent classifier |
| KMS 负责 Classify | 新增 `DispatchIntent` 分类层 |
| Reducer 不处理自然语言歧义 | 分类仍在 `src/kms`，没有放进 reducer |
| Kernel 以事件和派生状态为源 | 直答查询读取 derived state，不绕过 kernel |
| Talker / Thinker 不直接改状态 | Hermes 仍通过 KMS dispatch 决策，不直接写 kernel |

这次改动更贴近设计文档，因为它把“自然语言到调度动作”的解释集中到了 KMS 内部。

---

## 2. 参考 Codex 的点

Codex 中有明确的 turn control：

- `turn/steer`
- `turn/interrupt`
- `TurnStatus: Completed / Interrupted / Failed / InProgress`
- `expected_turn_id` 用于确认操作目标是当前 active turn

我们没有照搬 Codex，而是借鉴了一个原则：

```text
自然语言不要直接变成运行时动作，
先归一成结构化 control intent，再生成 dispatch decision。
```

---

## 3. 本次新增内容

新增文件：

```text
src/kms/intent_classifier.py
```

核心结构：

```python
@dataclass(frozen=True)
class DispatchIntent:
    intent: str
    confidence: float
    source: str
    reason: str = ""
    kernel_answer_kind: str = ""
```

当前支持的 intent：

| intent | 说明 |
|---|---|
| `kernel_answerable_query` | kernel 已有信息可直接回答 |
| `new_task` | 明确新任务 |
| `resume_previous_task` | 继续已暂停任务 |
| `same_task_steer` | 当前任务补充或调整 |
| `uncertain` | 未命中规则，暂时不做语义猜测 |

新增 DeepSeek fallback：

```text
classify_dispatch_intent_with_llm()
```

执行顺序：

```text
规则 fast path
  ↓
高置信命中则直接返回
  ↓
规则不确定时调用 LLM intent judge
  ↓
LLM 低置信度则回退规则结果
```

DeepSeek 只输出结构化 `DispatchIntent`，不直接执行调度动作。

---

## 4. 本次改造内容

修改文件：

```text
src/kms/manager.py
```

改造点：

1. 移除 manager 内部散落的 marker 判断。
2. `dispatch_user_message()` 先调用 `classify_dispatch_intent()`。
3. `respond_from_kernel` 不再只覆盖“进度查询”，而是扩展为 `kernel_answerable_query`。
4. `_build_kernel_direct_response()` 支持更多直答类型：
   - `progress`
   - `failures`
   - `evidence`
   - `resume`
   - `run`

---

## 5. 新增测试

修改文件：

```text
tests/test_requested_user_scenarios.py
```

新增测试：

```text
test_kernel_answerable_queries_cover_failures_evidence_run_and_resume_state
```

覆盖：

| 用户问题 | 预期 |
|---|---|
| 刚才哪里失败了？ | `respond_from_kernel` |
| 目前有什么证据？ | `respond_from_kernel` |
| 当前 run 是哪个？ | `respond_from_kernel` |
| 上一个任务还能继续吗？ | `respond_from_kernel` |

---

## 6. 验证结果

运行：

```text
python -m pytest -q tests/test_intent_classifier.py tests/test_requested_user_scenarios.py tests/test_smoke_interrupt.py tests/test_architecture_ab_experiment.py tests/test_pipeline_event_flow.py
```

结果：

```text
37 passed
```

---

## 7. DeepSeek key 处理

已移除代码里的默认 DeepSeek key。

现在只从环境变量读取：

```text
DEEPSEEK_API_KEY
```

如果没有设置 key，LLM fallback 会静默回退到规则分类。

---

## 8. 当前边界

当前仍是确定性规则 fast path，不是完整语义分类。

已经实现：

- 规则 fast path
- mockable LLM intent judge
- 低置信度回退
- DeepSeek 环境变量读取

还没有实现：

- 低置信度澄清
- 真正 Codex 风格的 same-turn steer
- streaming / final 输出全路径抑制压测

下一步适合继续做：

```text
规则 fast path + LLM intent judge + 低置信度保守策略
```

但在此之前，`DispatchIntent` 这个结构化中间层已经到位。
