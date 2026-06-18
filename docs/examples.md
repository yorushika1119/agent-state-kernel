# Kernel 场景示例

> 展示 Kernel 在实际多步调研任务中如何记录和派生结构化认知状态。

---

## 场景 1：Meta Llama 版本调研（含冲突检测）

**任务：** 调研 Meta 最新开源模型 Llama 的版本演变。

**规模：** 21 事件 / 5 证据 / 3 信念 / 2 次工具调用

### 输入：搜索"Meta Llama latest open source AI model release 2026"

搜索结果 5 条，包含 Llama 4（2025.4）和 Llama 5/Muse Spark（2026.4）。

### 提交到 Kernel 后的证据

| 证据 ID | 来源 | 可靠性 | 提取的关键事实 |
|---|---|---|---|
| ev_wiki_llama | wikipedia.org | high | Llama 4 Maverick/Scout 于 2025年4月5日 发布；Muse Spark 于 2026年4月替代 Llama |
| ev_aiwiki_llama4 | aiwiki.ai | high | 首个原生多模态 MoE；含 31 条引用来源 |
| ev_news_llama5 | financialcontent.com | medium | Zuckerberg 2026年4月8日 宣布 Llama 5 发布 |
| ev_blog_llama4specs | ai-coding-flow.com | medium | Scout: 405B 参数, 10M context；Maverick: 70B；200+ 语言 |
| ev_deep_specs | aiwiki.ai（全文） | high | Scout 实际: 109B/17B active；Maverick: ~400B/17B active；Behemoth 从未发布；存在 benchmark 操纵争议 |

### 注意冲突

ev_blog_llama4specs 声称 Scout=405B/Maverick=70B。

ev_deep_specs（同一 AI Wiki 的全文深度提取）揭示实际为 Scout=109B/17B active、Maverick=~400B/17B active。

**Kernel 自动处理：b_params_conflict 被标记为 conflicting，置信度 0.30。**

### 派生信念

| 信念 ID | 状态 | 置信度 | 结论 | 关联证据 |
|---|---|---|---|---|
| b_llama4_release | ✅ verified | 0.95 | Llama 4 于 2025.4.5 发布，Meta 首个原生多模态 MoE | ev_wiki_llama, ev_aiwiki_llama4, ev_deep_specs |
| b_llama5_muse | ✅ verified | 0.90 | Meta 2026.4.8 发布 Muse Spark，结束 Llama 开源策略 | ev_wiki_llama, ev_news_llama5, ev_deep_specs |
| b_params_conflict | ⚠️ conflicting | 0.30 | 参数规格存在来源冲突：blog 称 405B/70B，权威称 109B/400B | 支持: ev_blog_llama4specs / 冲突: ev_deep_specs, ev_wiki_llama |

### Talker 视图（对外可说的话）

| safe_facts | 可对用户说 |
|---|---|
| ✅ | Llama 4 于 2025年4月5日 发布，是 Meta 首个原生多模态 MoE 模型 |
| ✅ | Meta 于 2026年4月8日 发布 Muse Spark 替代 Llama，结束开源策略 |

| unsafe_claims | 不可对用户说（自动拦截） |
|---|---|
| ❌ | Llama 4 参数规格存在来源冲突：tech blog 称 405B/70B，权威来源称 109B/400B |

### 查询方式

```
curl http://127.0.0.1:8420/kms/sessions/ask_bc4b569cc4e6/views/thinker
```

---

## 场景 2：GPT-5 vs Claude 4 编码能力深度对比（最复杂）

**任务：** 5 步骤深度调研，判断 GPT-5 和 Claude 4 哪个编码能力更强。

**规模：** 33 事件 / 9 证据 / 3 信念 / 4 次工具调用（含 1 次失败）

### 5 个步骤

1. s1: 搜索编码基准测试（5 条结果，SWE-Bench 分数从 68% 到 88.6%）
2. s2: 深度提取两个来源的完整对比表
3. s3: 搜索反面意见（Overchat 声称 Opus 4.8 达 88.6%，DEV.to 说两者差距缩小）
4. s4: 尝试提取不存在 URL → **工具失败**（Blocked: URL targets private network）
5. s5: 交叉验证形成信念

### 核心冲突：SWE-Bench 分数矛盾

同一模型 Claude 4.6 在不同来源的 SWE-Bench Verified 分数：

| 来源 | 声明分数 | 可靠性 |
|---|---|---|
| n1n.ai | 72.7% | high |
| TLDL | 80.9% | medium |
| Overchat (Opus 4.8) | 88.6% | medium |

差异超过 15 个百分点。Kernel: b_scores_conflict → conflicting, 置信度 0.35。

### 派生信念

| 信念 | 状态 | 置信度 | 关联证据数 |
|---|---|---|---|
| Claude 4.6/4.8 在编码基准中整体领先 GPT-5 | ✅ verified | 0.82 | 6 条 |
| SWE-Bench 分数在不同来源间差异显著 | ⚠️ conflicting | 0.35 | 5 条 |
| 实际体验中 Claude 重构略优，GPT 生态广度略优 | 🟡 likely | 0.75 | 3 条 |

### 工具失败记录

act_s4_fail（web_extract）→ status: failed，错误: "Blocked: URL targets a private or internal network address"
→ **执行账本中保留，不同于纯 Thinker 中错误信息散落在对话流**

### 查询方式

```
curl http://127.0.0.1:8420/kms/sessions/ask_af06b914f135/views/thinker
```

---

## 场景 3：层层递进 + 中断恢复

**任务：** 四步调研 AI 编程工具定价，中间插入两个无关任务后恢复。

### 流程

```
Layer 1: 搜索四款工具定价（Copilot/Codex/Claude Code/Cursor）
Layer 2: 深度提取定价细节
    ↓
[中断1] 查北京天气
[中断2] 写 Python 脚本
    ↓
Layer 3: 恢复调研 — 发现 Cursor $20 隐藏限制
Layer 4: 形成最终定价结论
```

### 中断后状态保留验证

经过两个完全无关的中断后，查询 Kernel —— **4 条证据完整保留，plan 停在 s3=pending：**

| 证据 ID | 内容片段 |
|---|---|
| ev_l1_yingtu | Claude Code 需要 Claude Pro $20/月 |
| ev_l1_oflight | 四工具价格对比表：Copilot $10, Cursor $20, Codex $20-200, Claude Code $20-200 |
| ev_l2_oflight_deep | 9 维度详细对比 + SWE-Bench 分数 |
| ev_l2_beam | 所有工具分层定价（Free/Pro/Business/Enterprise）+ 隐藏成本分析 |

### 纯 Thinker 对比

此时纯 Thinker 的对话上下文里混着：定价调研数据、北京天气 34°C/22°C、Python 脚本输出。没有结构化分隔，依赖模型自己分辨哪些属于哪个任务。

**Kernel 的优势：数据在 SQLite 里，和对话上下文完全隔离。两个中断不会污染定价调研的证据。**

### 查询方式

```
curl http://127.0.0.1:8420/kms/sessions/ask_74e3f014d98f/views/thinker
```

---

## 场景 4：工具失败 + 空结果（边界情况）

### 4a：工具失败后重试

Session `ask_4623515f1cee`：先尝试提取不存在域名 → DNS 失败 → 改用搜索重试。

Kernel 执行账本保留完整链路：
- act_fail_1: web_extract [failed] — "DNS resolution failed"
- ToolRetried 事件
- act_retry_1: web_search [success]

### 4b：空搜索结果

Session `ask_77bc89943a70`：搜索不存在的内容，返回 3 条无关页面。

Kernel 结果：
- Evidence: 0 条（正确——没有从无关页面提取假证据）
- Beliefs: 0 条（正确——没有无证据支持的信念）
- Talker: status=completed, safe_facts=0（正确——任务完成但没有可报告的事实）

---

## 数据存储

所有会话数据存储在同一个 SQLite 文件中：

```
C:\program1\agent-state-kernel\data\kernel.db
```

**10 张表，141 条事件，24 条证据，11 条信念，15 条执行记录。**

每条信念可追溯到具体证据 ID，每条证据包含来源 URL 和可靠性评级。

查看方式：
- 浏览器：`http://127.0.0.1:8420/docs`（Swagger 交互式文档）
- 命令行：`curl http://127.0.0.1:8420/kms/sessions/{session_id}/views/thinker`
- 直接打开：VS Code + SQLite Viewer 插件打开 kernel.db
