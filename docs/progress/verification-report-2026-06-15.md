# Runtime-side Agent State Kernel — 验证报告

> 日期：2026-06-15  
> 场景：2026年AI芯片市场调研（NVIDIA B300 / AMD MI400 / Intel Gaudi 4）  
> 对照：A组（纯 Hermes Thinker）vs B组（Thinker + KMS + Kernel）

---

## 一、测试方法

| | A组：纯 Thinker | B组：Thinker + KMS + Kernel |
|---|---|---|
| 架构 | 用户 → Thinker(搜索→回答) | 用户 → Talker → KMS → Kernel |
| 状态管理 | Thinker 上下文内记忆 | KMS 9阶段流水线 + SQLite持久化 |
| 证据处理 | Thinker 自行判断可信度 | KMS 5 Judge自动评分 + 信念审查 |
| 发言控制 | Thinker 自行判断该说什么 | Visibility Gate 双层拦截 |

**测试步骤：**

```
1. 搜索AI芯片市场数据（2次 web_search，真实网页）
2. 提取5条证据：NVIDIA市场份额、AMD MI400规格、B300定价、Intel Gaudi、ROCm生态
3. 形成4条信念：市场主导、性价比、Intel定位、过度自信
4. 输出用户可见结论
```

**B组额外步骤：** Talker自然语言提交 → Normalize解析 → KMS 9阶段流水线 → Judge评分 → BeliefReview审查 → Summarize生成 → Gate发言检查。

---

## 二、时间对比

| 阶段 | A组（纯 Thinker） | B组（Kernel） | 差异 |
|---|---|---|---|
| 会话初始化 | 0（无） | 250ms | Kernel创建session + SessionCreated事件 |
| 意图解析 | 0（Thinker自己理解） | 1390ms | DeepSeek Normalize: 自然语言→结构化 |
| 计划提交 | 0（Thinker隐式规划） | 110ms | PlanProposed → KMS → PlanAccepted |
| 证据收集 | ~5,000ms | 7,968ms | KMS 5 Judge评分 + 模型Judge |
| 信念形成 | ~5,000ms | 6,125ms | BeliefReviewJudge自动审查 |
| 自然语言摘要 | 0（Thinker直接输出） | 1,032ms | DeepSeek Summarize |
| Gate发言检查 | 0（无此机制） | 2,265ms | 规则+DeepSeek语义双层 |
| **总耗时** | **~10s** | **28.3s** | **+18.3s** |

---

## 三、证据评分对比

A组：Thinker自行判断来源可信度，无结构化的评分标准。

B组（KMS自动评分）：

| 证据ID | 来源 | KMS评分 | 依据 |
|---|---|---|---|
| ev_amd_mi400 | data-gate.ch | **medium** | 独立技术分析站点 |
| ev_pricing | spheron.network | **medium** | 云服务商技术博客 |
| ev_nvidia_dom | gpuinsights.net | low | 域名匹配为个人/小团队站点 |
| ev_intel_gaudi | gpuinsights.net | low | 同上 |
| ev_rocm | gpunex.com | low | 域名匹配为GPU交易/中介类 |

**差距：** A组没有"评分低但事实正确"的认知——low评分不意味着证据错误，只是来源权威性不足；B组把评分作为信念置信度的参考维度，而非唯一判断依据。

---

## 四、信念审查对比（关键差异）

B组的 BeliefReviewJudge 自动审查了4条信念：

| 信念 | Thinker原始 | KMS审查后 | 变化 |
|---|---|---|---|
| NVIDIA主导，AMD追赶 | verified / 0.90 | verified / 0.90 | ✅ 通过 |
| AMD性价比优势 | verified / 0.88 | **likely / 0.88** | ⚠️ 降级：supporting evidence中有一条low评分来源，不足以标记为verified |
| Intel Gaudi定位 | likely / 0.70 | **verified / 0.70** | ✅ 升级：单一来源，但结论保守，一致性好 |
| **"NVIDIA是唯一正确选择"** | **verified / 0.95** | **conflicting / 0.40** | 🚫 **严重纠正**：与其他verified belief直接矛盾 |

A组的输出中没有第4条（"NVIDIA唯一正确"）——因为我是人类，会主动过滤。但如果换成自动化Agent：
- **A组**：Thinker可能直接输出"现在选NVIDIA就对了"
- **B组**：这条被KMS截断为unsafe_claim，conflicting/0.40，Talker看不到也说不出

---

## 五、Talker输出对比

### A组输出（纯Thinker，我直接用自然语言回答）

```
2026年AI芯片市场仍然是NVIDIA绝对主导——86%数据中心GPU营收。
但格局在松动：2024年NVIDIA占90%，两年跌到86%。

NVIDIA B300: 288GB HBM3e, FP4 15PF, 1400W, GB300 NVL72报价370-400万美元
AMD MI400: CDNA 4 + 3nm, 256GB HBM3e, 比B300便宜25-30%
Intel Gaudi 4: 性价比路线, 推理场景

结论：NVIDIA还是默认选择，但AMD在推理场景已经足够好且便宜25-40%。
```

**特征：** 流畅、有结论、无结构化约束、无审计追踪。

---

### B组最终自然语言输出（Talker 对用户说的话）

```
当前已确认NVIDIA仍主导AI芯片市场但份额从90%降至86%，
同时AMD在推理端快速追赶。

具体来看：

  ✅ NVIDIA仍主导AI芯片市场(86%营收),但份额从90%降至86%, AMD在推理端快速追赶
  ⚠️ [待确认] AMD MI400性价比优势明显: 比B300便宜25-30%, 训练性能达85-90%, 推理持平甚至更快
     （还在验证中，不能当最终结论）

涉及的关键数据：
  • NVIDIA B300: TSMC 4NP, 288GB HBM3e, FP16 20PF
  • AMD MI400: CDNA 4架构, TSMC 3nm, 256GB HBM3e, FP16 18PF
  • AMD MI400定价比B300低25-30%
  • NVIDIA 86%数据中心GPU营收(2026), 但2024年占90%
  • Intel Gaudi 4: 18A工艺, 性价比推理路线
  • B300云现货$2.45/hr起; GB300 NVL72机架$370-400万
  • AMD MI355X在Llama 3.1 405B推理上比B200快30%
  • ROCm 6.0: PyTorch和JAX原生支持

有一件事我不能确认：
  🚫 NVIDIA是唯一正确的AI芯片选择, 没有竞争对手能构成威胁
     这个说法和我手里的证据矛盾，所以我先不说。
```

**Talker内部约束：**
- `summary_for_talker`：DeepSeek 从 4 条信念中压缩出的摘要段
- `safe_facts` → 可以直接告诉用户的事实
- `unsafe_claims` → Talker 不能说的内容（被标记后物理隔离）
- `allowed_actions`：["report_progress", "ask_clarifying_question"]
- `forbidden_actions`：空（无额外禁止项）

**Gate 发言检查：**
- "NVIDIA已经没有对手了" → 🚫 拦截
  理由：与 verified belief 矛盾——NVIDIA 份额从 90% 降至 86%，AMD 在追
- "NVIDIA主导但AMD有优势" → ✅ 允许

> **注：当前 Talker 模块未实现。** Talker 生成自然语言的逻辑是人工翻译 `talker_view` 中的 `summary_for_talker`、`safe_facts`、`unsafe_claims`。等 Talker 模型就绪后，它会自动读这些结构化输入并生成对话。

**特征：** 自然语言、有 safe/unsafe 边界、有 Gate 检查、"NVIDIA 唯一正确"被自动拦截、有审计追踪(event log)。

---

## 六、核心差异总结

| 维度 | A组（纯 Thinker） | B组（Kernel） |
|---|---|---|
| 总耗时 | ~10s | 28.3s |
| 证据评分 | 人工判断，无标准 | KMS 自动，domain + DeepSeek |
| 信念审查 | 靠人谨慎 | BeliefReviewJudge 自动纠正 3/4 条 |
| 发言控制 | 靠人判断 | Gate 双层拦截（规则+语义） |
| 审计追踪 | 无 | Event Log + SQLite 12表 |
| 中断恢复 | 不可（上下文丢失=状态丢失） | 可（rebuild 事件流恢复全部状态） |
| **多花18s买到什么** | — | **不需要聪明人在旁边也知道什么能信、什么能说** |

---

## 七、B组 Architecturally Showed

1. **KMS写入主权**：Thinker提交 `PlanProposed` → event log先记录candidate → KMS生成 `PlanAccepted`（actor=kernel_manager）→ Reducer才落派生状态。PlanProposed 和 PlanAccepted 在事件流中物理分离，可审计。

2. **BeliefReviewJudge实战**：Thinker说"verified/0.95" → KMS对比证据后降为"conflicting/0.40"。Thinker不能自定置信度。

3. **Visibility Gate实战**：Talker试图说"NVIDIA已经没有对手了" → Gate对比当前beliefs → 发现与verified belief矛盾 → 拦截。不是说不能说"NVIDIA好"，而是不能忽略AMD在追的事实。

4. **Safe/Unsafe分离**：AMD性价比是safe_fact但标记`[待确认]`；"NVIDIA唯一正确"直接进unsafe_claims。Talker输出中物理隔离。

5. **Progress闭环**：每次事件提交后自动refresh_progress，Gate不依赖Talker先访问view。

---

## 八、局限性

- B组多花的18s中，~10s是DeepSeek模型调用（Normalize 1.4s + Judge 5s + Summarize 1s + Gate 2.3s）。如果换成本地小模型或用规则替代，可压缩到~12s。
- 证据评分依赖域名模式匹配（low评了gpuinsights.net），实际该站点是深度技术站点。这是§5.7五维评分的简化——当前只有"权威性"一维，缺"时效性"和"可验证性"维度。
- Talker模块未实现：当前Talker输出是由我模拟构造的，而非真实Talker模型生成的对话。
