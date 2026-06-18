# A/B 对比实验报告：纯 Thinker vs Kernel + Thinker

> 日期：2026-06-12
> 方法：同一任务在两组中执行，A组为纯Hermes对话，B组为Hermes+Kernel自动记录

---

## 场景 1：信息冲突调研 — Apple Vision Pro 销量

**任务：** 调研 Vision Pro 销量，来源之间存在矛盾。

### A 组（纯 Thinker）

对话中产生了 5 条搜索结果（KEUTEK、AR Insider、TechStory、Accio、TrendSpider），含明确矛盾：
- AR Insider: 22.4万台（2024预测）
- 其他机构: 42万台（2倍差异）
- KEUTEK/IDC: 250万台（低价版假设）
- TechStory: Apple 砍产
- Accio: 企业市场热卖

**A 组事后能回答什么：**
- 能找到用了什么工具吗？→ 翻对话记录，靠人眼找 `web_search` 调用
- 矛盾数据在哪？→ 靠人读对话，模型可能注意到了但没说
- 哪些来源？→ 对话里有 URL，但和结论之间的对应关系靠模型"记住"

### B 组（Kernel + Thinker）

| 指标 | 值 |
|---|---|
| 事件数 | 19 |
| 证据 | 5 条（含来源URL+可靠性评级+提取事实） |
| 信念 | 2 条：b_vp_weak (verified, 0.88) + b_vp_conflict (conflicting, 0.40) |
| Talker safe_facts | 1 条（销量低于预期） |
| Talker unsafe_claims | 1 条（预测分歧正在验证中） |
| 计划进度 | s1→s2→s3 全部 completed |

**B 组相比 A 组多出：**
- 每条证据有独立 ID、来源 URL、可靠性评级、提取事实列表
- 冲突被显式标记为 conflicting，置信度降为 0.40
- Talker 视图自动隔离了 unsafe_claims（不会对用户说出不确定的矛盾数据）
- 19 个事件可完整回放，知道每一步发生了什么

---

## 场景 2：工具调用失败 + 重试

**任务：** 提取一个不存在域名的网页 → 失败 → 改用搜索重试。

### A 组（纯 Thinker）

对话中工具调用失败会返回错误 JSON。模型看到错误后决定换方案。但：
- 失败原因（DNS解析失败）在对话中，事后要靠翻记录找到
- 重试了几次？靠翻对话
- 最终成功的方案是什么？靠模型"记住"上下文

### B 组（Kernel + Thinker）

| 指标 | 值 |
|---|---|
| 事件数 | 11 |
| 执行记录 | 2 条：act_fail_1 (web_extract, failed) + act_retry_1 (web_search, success) |
| 失败事件 | ToolFailed（含错误信息） |
| 重试事件 | ToolRetried（含切换方案说明） |

**B 组相比 A 组多出：**
- 失败被结构化为 ToolFailed 事件（含具体错误）
- 重试被记录为 ToolRetried 事件
- 执行账本清晰：act_fail_1 [failed] → retry → act_retry_1 [success]
- 事后审计不需要翻对话，直接查 executions 表

---

## 场景 3：搜索结果为空/无相关性

**任务：** 搜索一个不存在的内容，结果无相关信息。

### A 组（纯 Thinker）

搜索返回了 3 条无关页面（Privy API文档、Toyota车辆页、二手车列表）。模型在对话中判断"无有效结果"。但：
- 为什么认为它们无关？模型没解释，靠信任
- 有没有可能漏掉了有用信息？无法验证
- 空结果算不算"完成"？取决于模型自己判断

### B 组（Kernel + Thinker）

| 指标 | 值 |
|---|---|
| 事件数 | 8 |
| 证据 | 0 条（正确——没有从无关页面提取假证据） |
| 信念 | 0 条（正确——没有无证据支持的信念） |
| 执行 | 1 条（web_search，success） |
| Talker 状态 | completed，safe_facts=0 |

**B 组相比 A 组多出：**
- 证据数为 0，明确表示"没有可提取的事实"
- 信念数为 0，不会因为"模型猜了点什么"而产生虚假信念
- Talker 状态 completed 但 safe_facts=0——正确地表达了"任务完成了，但没有什么可报告的"

---

## 综合对比

| 维度 | A 组（纯 Thinker） | B 组（Kernel + Thinker） |
|---|---|---|
| 事后可追溯 | 翻对话记录，靠人理解 | 查 /views/thinker，结构化输出 |
| 证据管理 | 散落在对话中，和推理混在一起 | 独立 Evidence Store，每条有来源 URL + 可靠性 |
| 冲突检测 | 依赖模型注意到并说出来 | 显式标记 conflicting，置信度降权 |
| 失败/重试 | 混在对话流里 | ToolFailed/ToolRetried 结构化事件 |
| 空结果安全 | 模型可能编造结论 | evidence=0, belief=0, safe_facts=0 |
| 进度表达 | 模型自己判断"完成了" | Progress Synthesizer 自动合成，safe/unsafe 分离 |
| 崩溃恢复 | 不可恢复 | 事件流可回放 |
| 切换到其他 Thinker | 不可迁移 | 换 Thinker 只需新 Adapter（几十行代码） |

---

## 结论

Kernel 解决的不是"Thinker 做不了"的问题，而是"Thinker 做完之后留不下结构化痕迹"的问题。三个场景中 Thinker 都能完成任务，但加了 Kernel 后多了一层：

1. **证据层**：工具结果不再是一次性消费，变成可索引的 Evidence
2. **冲突层**：矛盾的来源不再靠模型"注意到"，而是被结构化为 conflicting belief
3. **安全层**：Talker 视图自动过滤不该说的话（空结果不编造、冲突不宣称）
4. **恢复层**：19/11/8 个事件可回放，崩溃不丢状态
