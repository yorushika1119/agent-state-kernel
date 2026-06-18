# 复杂场景 A/B 对比：GPT-5 vs Claude 4 编码能力调研

> 日期：2026-06-12
> 场景：5步骤深度调研，覆盖多源搜索、深度提取、反面意见、工具失败、冲突仲裁

---

## 场景设计

| 步骤 | 操作 | 预期挑战 |
|---|---|---|
| s1 | web_search 编码基准测试 | 多来源数据冲突 |
| s2 | web_extract 深度提取 | 不同来源声明不同分数 |
| s3 | web_search 反面意见 | 发现更新的模型版本数据 |
| s4 | web_extract 不存在的URL | 工具失败 |
| s5 | 交叉验证形成信念 | SWE-Bench 分数范围 72.7%-88.6% |

---

## B 组（Kernel + Thinker）结果

| 指标 | 值 |
|---|---|
| 事件总数 | **33** |
| 证据 | **9 条**（含来源URL、可靠性、提取事实） |
| 信念 | **3 条**（1 verified + 1 conflicting + 1 likely） |
| 执行记录 | **4 条**（3 success + 1 failed） |
| 计划进度 | 5/5 completed |
| Talker safe_facts | 2 条 |
| Talker unsafe_claims | 1 条（SWE-Bench分数冲突） |

### 关键发现被 Kernel 捕获

1. **SWE-Bench 分数冲突**：n1n.ai 说 Claude 72.7%，TLDL 说 80.9%，Overchat 说 Opus 4.8 达 88.6%。差异超 15 个百分点。
   → Kernel: ConflictDetected 事件 + b_scores_conflict 信念（置信度 0.35，归入 unsafe_claims）

2. **工具失败记录**：s4 的 web_extract 对不存在的 URL 失败
   → Kernel: ToolFailed 事件，execution ledger 记录为 [failed]

3. **多源验证**：9 条证据来自 9 个独立 URL，每条可追溯
   → Kernel: Evidence Store 中每条有 evidence_id、source、reliability

---

## 时间对比

| 阶段 | A 组（纯 Thinker） | B 组（Kernel + Thinker） | 差异 |
|---|---|---|---|
| web_search ×2 | ~6-10s | ~6-10s | 相同 |
| web_extract ×2 | ~8-16s | ~8-16s | 相同 |
| Kernel 事件提交 | 0 | **+1.9s**（33次API调用） | 新增 |
| 模型推理 | ~5-10s | ~5-10s | 相同 |
| **总计** | **~20-35s** | **~22-37s** | **+5-8%** |

---

## 能力对比

| 能力维度 | A 组（纯 Thinker） | B 组（Kernel + Thinker） |
|---|---|---|
| 多源数据整合 | 模型在上下文中整理 | 9 条 Evidence 结构化存储，按 reliability 分级 |
| 分数冲突发现 | 依赖模型"注意到"并说出来 | ConflictDetected 事件 + b_scores_conflict（0.35）自动标记 |
| 工具失败溯源 | 失败信息在对话里 | ToolFailed 事件归档，execution ledger 可查询 |
| Talker 安全表达 | 无 Talker 层 | 自动生成：safe=2条，unsafe=1条（冲突数据不外泄） |
| 事后审计 | 翻对话记录 | 查 /views/thinker，33 个事件完整回放 |
| 结论可追溯 | 模型说"Claude编码更强" | 信念 b_claude_coding_lead 关联 6 条具体证据 ID |
| 模型版本演化 | Overchat 的 Opus 4.8 数据容易丢失 | ev_overchat 证据独立存储，不会因为上下文压缩丢失 |

---

## 结论

1. **时间开销**：33 个事件的 Kernel 开销为 +1.9s，占 5-8%。优化空间（批量提交、连接池）可将开销降至 <0.5s。

2. **能力增益**：Kernel 主要解决了三个纯 Thinker 做不到的事：
   - **冲突显式化**：SWE-Bench 72.7% vs 80.9% vs 88.6% 的差异被结构化为 conflicting belief
   - **失败结构化**：ToolFailed 事件独立于对话流存在
   - **安全分层**：自动识别哪些结论可以直接说、哪些需要标注、哪些不能说

3. **验证了文档设计**：5 步骤 × 9 证据 × 3 信念 × 1 失败的全链路证明 Kernel 的事件驱动架构在实际多步调研中有效运行。
