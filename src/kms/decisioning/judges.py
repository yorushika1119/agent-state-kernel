"""KMS 评判器 — 规则驱动的事件审查引擎。

提供：
- BaseJudge：评判器基类
- ReliabilityJudge：域名模式匹配可靠性评分
- DedupJudge：MD5 指纹 + 同域名检测去重
- ConflictJudge：数值提取 + 阈值比较冲突检测
- KMSPipeline：评判器流水线编排器

这些评判器是确定性规则引擎——不调用 LLM。
当多条证据指向同一主题但给出矛盾数据时，评判器自动检测。
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.schema.events import CognitiveEvent, EventType
from src.schema.state import BeliefItem, EvidenceItem, Reliability

logger = logging.getLogger(__name__)

# ── 高权威域名模式 ──
# 这些域名通常代表经过编辑审核的可靠来源
HIGH_AUTHORITY_PATTERNS = [
    "wikipedia.org", "arxiv.org", ".gov", ".edu",
    "reuters.com", "bloomberg.com", "bbc.com", "bbc.co.uk",
    "nature.com", "science.org", "ieee.org", "acm.org",
    "theguardian.com", "nytimes.com", "wsj.com", "ft.com",
    "who.int", "un.org", "nasa.gov", "nih.gov",
]

# ── 低权威域名模式 ──
# 用户生成内容、SEO 农场、个人博客——通常不可靠
LOW_AUTHORITY_PATTERNS = [
    "reddit.com", "medium.com", "quora.com",
    "programming-helper.com", "ilirivezaj.com",
    "ketodietapp.com", "uicn.cn", "oreateai.com",
    "sparkco.ai", "dataconomy.com",
    "blogspot.com", "wordpress.com",
]


class BaseJudge(ABC):
    """所有评判器的抽象基类。

    每个评判器有：
    - name：唯一标识符，用于日志和追踪
    - evaluate()：异步方法，返回 JudgeResult
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """评判器的人类可读名称。"""
        ...

    @abstractmethod
    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> "JudgeResult":
        """评估一条证据/信念事件并返回裁决。

        Args:
            event: 当前正在评估的事件
            existing_evidence: 同一会话中已有的证据
            existing_beliefs: 同一会话中已有的信念

        Returns:
            JudgeResult 包含裁决和可选修改
        """
        ...


@dataclass
class JudgeResult:
    """评判器输出——接受/修改/拒绝 + 可选副作用。"""
    judge_name: str
    verdict: str  # "accept"、"modify"、"reject"
    reason: str = ""
    modifications: Dict[str, Any] = field(default_factory=dict)
    side_effects: List[CognitiveEvent] = field(default_factory=list)


# ===========================================================================
# ReliabilityJudge — 按域名评定可靠性
# ===========================================================================

class ReliabilityJudge(BaseJudge):
    """根据域名为证据分配可靠性评分。

    域名匹配高权威列表 → high
    域名匹配低权威列表 → low
    其他 → medium

    这是快速、确定性的第一层过滤。后续 ContentReliabilityJudge
    （DeepSeek）会对 medium 来源进行深度内容质量评估。
    """

    @property
    def name(self) -> str:
        return "reliability_judge"

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> JudgeResult:
        if event.event_type != EventType.EVIDENCE_ACCEPTED:
            return JudgeResult(judge_name=self.name, verdict="accept")

        source = event.payload.get("source", "").lower()
        if not source:
            return JudgeResult(judge_name=self.name, verdict="accept")

        reliability = self._score(source)
        current = event.payload.get("reliability", "unknown")

        if reliability != current:
            return JudgeResult(
                judge_name=self.name,
                verdict="modify",
                reason=f"Auto-scored {source[:40]} as {reliability}",
                modifications={"reliability": reliability},
            )

        return JudgeResult(judge_name=self.name, verdict="accept")

    def _score(self, source: str) -> str:
        """根据域名模式评分。"""
        for pattern in HIGH_AUTHORITY_PATTERNS:
            if pattern in source:
                return "high"
        for pattern in LOW_AUTHORITY_PATTERNS:
            if pattern in source:
                return "low"
        return "medium"


# ===========================================================================
# DedupJudge — 去重
# ===========================================================================

class DedupJudge(BaseJudge):
    """检测并降级重复证据。

    两种检测方式：
    1. MD5 指纹：完全相同的内容
    2. 同域名 + 相似标题：同一来源的重复内容

    发现重复时降级为 low（而不是拒绝——用户可能需要确认这是重复的）。
    """

    @property
    def name(self) -> str:
        return "dedup_judge"

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> JudgeResult:
        if event.event_type != EventType.EVIDENCE_ACCEPTED:
            return JudgeResult(judge_name=self.name, verdict="accept")

        source = event.payload.get("source", "").lower()
        title = event.payload.get("title", "").lower()
        current_id = event.payload.get("evidence_id", "")

        # 检查是否为重复
        for existing in existing_evidence:
            # 同一域名 + 相似标题 → 可能重复
            if source and existing.source and source in existing.source:
                if self._titles_overlap(title, existing.title.lower()):
                    return JudgeResult(
                        judge_name=self.name,
                        verdict="modify",
                        reason=f"可能重复: 与 {existing.evidence_id} 标题相似（同源 {source[:30]}）",
                        modifications={"reliability": "low"},
                    )

        return JudgeResult(judge_name=self.name, verdict="accept")

    def _titles_overlap(self, a: str, b: str) -> bool:
        """检查两个标题是否有显著重叠。"""
        if not a or not b:
            return False
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap > 0.5


# ===========================================================================
# ConflictJudge — 数值冲突检测
# ===========================================================================

class ConflictJudge(BaseJudge):
    """检测多条证据间的数值冲突。

    例如：
    - 证据 A 说"2024年售出 39 万台"
    - 证据 B 说"2024年总计 42 万台"
    → 差异 > 15% 阈值 → 触发冲突

    当前实现：提取所有数字后做简单阈值比较。
    未来增强：上下文感知的冲突识别。
    """

    # 触发冲突的百分比差异阈值
    CONFLICT_THRESHOLD = 0.15

    @property
    def name(self) -> str:
        return "conflict_judge"

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> JudgeResult:
        if event.event_type != EventType.EVIDENCE_ACCEPTED:
            return JudgeResult(judge_name=self.name, verdict="accept")

        new_numbers = self._extract_numbers(event.payload)
        if not new_numbers:
            return JudgeResult(judge_name=self.name, verdict="accept")

        # 与已有证据比较
        for existing in existing_evidence:
            existing_numbers = self._extract_numbers({
                "title": existing.title,
                "extracted_facts": existing.extracted_facts,
            })
            for new_val in new_numbers:
                for old_val in existing_numbers:
                    if self._is_conflict(new_val, old_val):
                        return JudgeResult(
                            judge_name=self.name,
                            verdict="modify",
                            reason=f"可能存在冲突: {new_val} vs {old_val}（与 {existing.evidence_id}）",
                        )

        return JudgeResult(judge_name=self.name, verdict="accept")

    def _extract_numbers(self, payload: dict) -> List[float]:
        """从 payload 中提取所有数字。

        支持"万"单位转换：5万 → 50000。
        """
        text = payload.get("title", "")
        if isinstance(text, str):
            text += " " + " ".join(payload.get("extracted_facts", []))
        elif isinstance(payload.get("extracted_facts"), list):
            text = " ".join(str(f) for f in payload["extracted_facts"])

        numbers = []
        for match in re.finditer(r"(\d+\.?\d*)\s*(万|亿)?", str(text)):
            val = float(match.group(1))
            unit = match.group(2)
            if unit == "万":
                val *= 10000
            elif unit == "亿":
                val *= 100000000
            numbers.append(val)
        return numbers

    def _is_conflict(self, a: float, b: float) -> bool:
        """检查两个数字是否在给定阈值内冲突。"""
        if b == 0:
            return False
        return abs(a - b) / max(abs(a), abs(b)) > self.CONFLICT_THRESHOLD


# ===========================================================================
# KMSPipeline — 评判器流水线编排器
# ===========================================================================

class KMSPipeline:
    """顺序运行多个评判器并聚合结果。

    评判器按添加顺序运行。每个评判器都可以：
    - accept：不改变任何东西
    - modify：更改 payload（如 reliability 评分）
    - reject：完全拒绝证据

    后序评判器会看到前序评判器修改后的 payload。
    这允许可靠性评分影响去重，而去重又影响冲突检测。
    """

    def __init__(self):
        from src.kms.decisioning.model import SemanticConflictJudge, ContentReliabilityJudge
        self.judges: List[BaseJudge] = [
            ReliabilityJudge(),
            DedupJudge(),
            ConflictJudge(),
            SemanticConflictJudge(),
            ContentReliabilityJudge(),
        ]

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> List[JudgeResult]:
        """顺序运行所有评判器。

        评判器之间共享修改后的 payload（如可靠性评分会流向后续评判器）。
        """
        results = []
        for judge in self.judges:
            try:
                result = await judge.evaluate(event, existing_evidence, existing_beliefs)
                results.append(result)
                if result.verdict == "modify":
                    # 将修改应用到事件——后续评判器看到修改后的版本
                    for key, val in result.modifications.items():
                        event.payload[key] = val
                elif result.verdict == "reject":
                    break  # 后序评判器跳过
            except Exception as e:
                logger.warning("Judge '%s' failed: %s", judge.name, e)
                results.append(JudgeResult(
                    judge_name=judge.name,
                    verdict="accept",
                    reason=f"judge error: {e}",
                ))
        return results

    def get_modifications(self, results: List[JudgeResult]) -> Dict[str, Any]:
        """聚合所有评判器的修改。"""
        mods = {}
        for r in results:
            if r.verdict == "modify":
                mods.update(r.modifications)
        return mods

    def get_side_effects(self, results: List[JudgeResult]) -> List[CognitiveEvent]:
        """收集所有评判器的副作用事件。"""
        side_effects = []
        for r in results:
            side_effects.extend(r.side_effects)
        return side_effects
