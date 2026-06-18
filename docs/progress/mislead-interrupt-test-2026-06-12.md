# 误导性中断压力测试报告

> 日期：2026-06-12
> 场景：量子计算调研 → 5个语义相似的误导性中断 → 恢复调研

---

## 测试设计

**目的：** 测试 Kernel 在上下文被语义相似的误导信息污染时，能否保持状态纯净。

**原始任务：** 调研 2026 年量子计算突破（IBM/Google 量子比特数和纠错里程碑）

**5 个误导性中断的设计原则：** 每个中断都与原始任务共享关键词，但内容完全无关：

| 中断 | 共享关键词 | 实际内容 |
|---|---|---|
| Quantum 护肤品 | **quantum** | NAD+ 胜肽抗衰老、量子护肤品 |
| IBM 股价 | **IBM** | IBM NYSE 股价 $274.85，52周高 $332.46 |
| Google 新总部 | **Google** | Google 芝加哥总部 $2.8 亿改造 |
| ENIAC 历史 | **computing** | 1946年第一台电子计算机，18000真空管 |
| Surface Pro 12 | **Microsoft** | Surface Pro 12 Intel Panther Lake，$1949起 |

---

## A 组（纯 Thinker）上下文污染分析

5 个中断后的上下文状态：

```
原始调研:
  "IBM Condor 433 qubits, Google Willow 1,000 qubits, quantum error correction..."

污染层:
  "Quantum 护肤品 NAD+ 胜肽 anti-aging..."
  "IBM 股价 $274.85, June 2 high $332.46, 52-week low $212.34..."
  "Google 芝加哥总部 Thompson Center $280M 改造, Helmut Jahn 设计..."
  "ENIAC 1946年, 18000真空管, 40个机柜, 重30吨, John Mauchly..."
  "Surface Pro 12 Intel Panther Lake Core Ultra 7, 64GB RAM, $1,949..."
```

**问题：** 当 Thinker 被问到"IBM 在量子计算方面有什么进展？"时，上下文中同时存在：
- IBM Condor 433 qubits（量子芯片）
- IBM 股价 $274.85（股票）

模型需要区分哪个"IBM"是相关的。同样，"quantum"可能指量子计算，也可能指量子护肤品。

**这是纯 Thinker 的固有弱点：所有信息共用同一个线性上下文，无法物理隔离。**

---

## B 组（Kernel + Thinker）结果

5 个误导性中断后，直接查询 Kernel：

```
Intent: "调研2026年量子计算最新突破：IBM、Google量子比特数和纠错里程碑"
Plan: s1=completed, s2=pending, s3=pending

Evidence (4 items — 零污染):
  e_ibm433:  IBM 433-Qubit Condor
  e_ibm1000: IBM 1,000-qubit processors
  e_msn:     Quantum Computing Advances 2026
  e_rigetti: Quantum Machines Milestone on Rigetti Hardware
```

| Kernel 中的关键词 | 实际内容 |
|---|---|
| quantum | **仅**量子计算（IBM qubits, Google Willow, Rigetti） |
| IBM | **仅**量子芯片（Condor 433/1000+ qubits） |
| Google | **仅** Willow 芯片（quantum error correction） |
| computing | **仅**量子计算 |
| Microsoft | **仅**拓扑量子比特（Majorana 1） |

**没有任何护肤品、股价、办公楼、ENIAC、Surface 的信息混入。**

恢复研究后无缝形成信念：
- Safe: "Google Willow 实现首个 below-threshold 纠错"
- Unsafe: "IBM Condor 量子比特数存在来源冲突"

---

## 对比总结

| 维度 | A组（纯 Thinker） | B组（Kernel + Thinker） |
|---|---|---|
| quantum 关键词 | 量子计算 + 护肤品 | 仅量子计算 |
| IBM 关键词 | 量子芯片 + 股价 $274.85 | 仅量子芯片 |
| Google 关键词 | Willow 芯片 + 芝加哥总部 | 仅 Willow 芯片 |
| computing 关键词 | 量子计算 + 1946年 ENIAC | 仅量子计算 |
| Microsoft 关键词 | 拓扑量子 + Surface Pro | 仅拓扑量子 |
| 恢复研究 | 需分辨哪些 IBM/quantum 相关 | 直接查 Kernel，零分辨成本 |
| 换 Thinker 继续 | 不可行（无从分辨） | GET /views/thinker 秒恢复 |

---

## 结论

Kernel 的核心价值在这个测试中体现得最清晰：**它不是让 Thinker 更聪明，而是物理隔离了不同任务的信息。** 

纯 Thinker 的上下文类比一个白板——量子计算、护肤品、股价、办公楼、计算机历史、笔记本电脑规格全写在上面。分辨哪些属于哪个任务，靠的是模型的判断力。

Kernel 类比一个文件柜——量子计算调研在 3 号文件夹里，护肤品在 7 号，股价在 12 号。它们永远不会混在一起。
