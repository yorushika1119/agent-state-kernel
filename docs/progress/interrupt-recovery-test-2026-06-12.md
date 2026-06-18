# 层层递进 + 中断恢复 A/B 对比

> 日期：2026-06-12
> 场景：4层递进调研，中间插入2个无关中断，测试上下文恢复能力

---

## 场景结构

```
Layer 1: 搜索四款AI编程工具定价
Layer 2: 深度提取定价细节
    ↓
[中断1] 查北京天气（完全无关）
[中断2] 写Python脚本（完全无关）
    ↓
Layer 3: 恢复调研 — 验证隐藏成本和价格冲突
Layer 4: 形成最终定价对比结论
```

---

## B 组（Kernel + Thinker）结果

### 中断后恢复检查

中断后直接查询 Kernel——**4 条证据完整保留，零丢失：**

| 证据 ID | 内容 | 来源 |
|---|---|---|
| ev_l1_yingtu | Claude Code 需要 $20 Pro / $100-200 Max | yingtu.ai |
| ev_l1_oflight | Copilot $10, Cursor $20, Codex $20-200, Claude Code $20-200 | Oflight |
| ev_l2_oflight_deep | 9维度对比表 + SWE-Bench 分数 | Oflight 全文 |
| ev_l2_beam | 所有工具分层定价 + 隐藏成本分析 | Beam 全文 |

计划状态：s1=completed, s2=completed, s3=pending, s4=pending（精确停在中断点）

### 恢复后继续

Layer 3-4 从 s3 继续，无缝衔接：
- 发现 Cursor Pro $20 的隐藏限制（仅 500 次快速请求/月）→ ConflictDetected
- 新增证据 ev_l3_hidden（Beam 隐藏成本）
- 形成 3 条最终信念（2 verified + 1 likely）

### 最终状态

| 指标 | 值 |
|---|---|
| 事件总数 | 23 |
| 证据 | 5 条（Layer 1-2: 4条 + Layer 3: 1条） |
| 信念 | 3 条 |
| 执行记录 | 2 条 |
| 计划进度 | 4/4 completed |
| Talker safe_facts | 3 条 |
| Talker unsafe_claims | 0 条 |
| 时间 | Kernel 事件提交 ~1.6s |

---

## A 组（纯 Thinker）能力分析

此时对话上下文里堆积了：

| 内容来源 | 内容 |
|---|---|
| Layer 1-2 | 4条搜索结果 + 2篇提取全文（定价数据） |
| 中断1 | 北京天气 34°C/22°C、降水量54mm |
| 中断2 | Python 脚本输出 |

回到 Layer 3 时：
- ✅ 模型可以在上下文中找到之前的定价数据——但和天气数据混在一起
- ❌ 没有结构化的 evidence_id → 要查"Copilot 有几个价格层级"得翻对话
- ❌ 没有版本化的 plan 状态——"上次做到哪一步了？"依赖模型自己判断
- ❌ 中断后如果上下文过长触发 compression，定价数据可能被压缩丢失
- ❌ 换 Thinker（比如从 Hermes 切换到 Codex）后完全无法恢复

---

## 核心差异

| 维度 | A 组（纯 Thinker） | B 组（Kernel + Thinker） |
|---|---|---|
| 中断后数据保留 | 靠上下文记忆，和天气/脚本混在一起 | SQLite 独立存储，零污染 |
| 恢复到哪一步 | 模型自己判断 | plan 状态精确到 step_id |
| 上下文压缩影响 | 可能丢失细节 | 不受影响（存数据库） |
| 换 Thinker 继续 | 不可行 | GET /views/thinker 即可接管 |
| 发生冲突 | 模型可能注意也可能忽略 | ConflictDetected 事件显式记录 |
| 事后查询 | 翻全部对话 | evidence/belief/plan 独立查询 |

---

## 结论

Kernel 在中断恢复场景的价值最明显：**纯 Thinker 的上下文是线性的、会被稀释的；Kernel 的状态是结构化的、隔离的。** 两个完全无关的中断不会污染 Kernel 里的调研数据，就像你不会因为中间接了电话就忘了正在写的文档——因为文档在硬盘上，不在你脑子里。
