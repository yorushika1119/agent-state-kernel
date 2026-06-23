"""KMS 流水线 — 9 阶段认知事件处理。

严格匹配 Kernel 功能设计文档 §4.2 和 §7：

  Intake   → 接收原始提交
  Normalize→ 将自由文本转换为结构化 CognitiveEvent
  Validate → 策略规则（权限、版本、格式）
  Classify → 确定事件类别以进行路由
  Arbitrate→ 运行评判器（可靠性、去重、冲突、语义、内容）
  EventLog → 写入追加日志（在 Reducer 之前，保证一致性）
  Reduce   → 调用 State Reducer 更新派生状态
  Summarize→ 为 Talker 合成进度视图（懒加载）
  Gate     → 生成可见性决策（Talker 能否说这个？）
  Sync     → 生成外部同步视图（Multica 等）

KMS 是认知状态变更的唯一切入点。Talker 和 Thinker 不能
直接写入状态——它们提交候选事件，KMS 决定接受、修改或拒绝。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.kernel.state_reducer import (
    reduce_beliefs,
    reduce_commitments,
    reduce_evidence,
    reduce_execution,
    reduce_intent,
    reduce_plan,
    synthesize_progress,
)
from src.kms.decisioning.model import DEEPSEEK_API_KEY
from src.kms.runtime.references import register_runtime_references
from src.kms.state.aliases import (
    beliefs_from_claims,
    commitments_from_todos,
    intent_from_task_brief,
    plan_from_task_flow,
)
from src.schema.events import (
    Actor,
    CognitiveEvent,
    EventSubmission,
    EventType,
    RuntimeRef,
    Visibility,
)
from src.schema.state import (
    BeliefItem,
    BeliefStatus,
    EvidenceItem,
    ExecutionAction,
    ProgressState,
    SyncView,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# 第 1 阶段：Intake — 接收原始提交
# ===========================================================================

@dataclass
class IntakeResult:
    """Intake 阶段的输出。"""
    accepted: bool
    reason: Optional[str] = None
    submission: Optional[EventSubmission] = None


def intake(submission: EventSubmission) -> IntakeResult:
    """接收来自 Talker 或 Thinker 的事件。

    验证基本形态：必须有 session_id、component、request_type。
    """
    if not submission.session_id:
        return IntakeResult(False, "Missing session_id")
    if submission.component not in ("talker", "thinker"):
        return IntakeResult(False, f"Unknown component: {submission.component}")
    if not submission.request_type:
        return IntakeResult(False, "Missing request_type")
    return IntakeResult(True, submission=submission)


# ===========================================================================
# 第 2 阶段：Normalize — 自由文本 → 结构化事件
# ===========================================================================

# Talker 请求类型 → EventType 映射（Talker 只能发这些）
TALKER_REQUEST_MAP = {
    "GET_TALKER_CONTEXT": None,       # 只读
    "SUBMIT_USER_MESSAGE": EventType.INTENT_UPDATED,
    "REGISTER_USER_INTENT_UPDATE": EventType.INTENT_UPDATED,
    "REGISTER_COMMITMENT_PROPOSAL": EventType.COMMITMENT_CREATED,
    "ASK_CAN_SAY": None,              # 只读
    "ASK_CAN_DO": None,               # 只读
}

# Thinker 请求类型 → EventType 映射
THINKER_EVENT_MAP = {
    # 核心类型（§6.2）
    "PlanProposed": EventType.PLAN_PROPOSED,
    "BeliefProposed": EventType.BELIEF_PROPOSED,
    "EvidenceCandidateFound": EventType.EVIDENCE_CANDIDATE_FOUND,
    "ToolStarted": EventType.TOOL_STARTED,
    "ToolCompleted": EventType.TOOL_COMPLETED,
    "ToolFailed": EventType.TOOL_FAILED,
    "ToolRetried": EventType.TOOL_RETRIED,
    "StepStarted": EventType.STEP_STARTED,
    "ConflictDetected": EventType.CONFLICT_DETECTED,
    "VerificationWarningRaised": EventType.VERIFICATION_WARNING_RAISED,
    "TaskCompleted": EventType.TASK_COMPLETED,
    "TaskFailed": EventType.TASK_FAILED,
    "IntentUpdated": EventType.INTENT_UPDATED,
    "SessionCancelled": EventType.SESSION_CANCELLED,
    # 扩展 Thinker 协议（§6.2）
    "ReplanRequest": EventType.REPLAN_REQUEST,
    "RiskAssessment": EventType.RISK_ASSESSMENT,
    "ReasoningSummary": EventType.REASONING_SUMMARY,
    "RawResultAvailable": EventType.RAW_RESULT_AVAILABLE,
    "ActionBlocked": EventType.ACTION_BLOCKED,
    "VerificationResult": EventType.VERIFICATION_RESULT,
    "CompletionCheck": EventType.COMPLETION_CHECK,
}

CANDIDATE_TO_FINAL_EVENT = {
    EventType.PLAN_PROPOSED: EventType.PLAN_ACCEPTED,
    EventType.BELIEF_PROPOSED: EventType.BELIEF_UPDATED,
    EventType.EVIDENCE_CANDIDATE_FOUND: EventType.EVIDENCE_ACCEPTED,
}


@dataclass
class NormalizeResult:
    """Normalize 阶段的输出。"""
    accepted: bool
    event: Optional[CognitiveEvent] = None
    reason: Optional[str] = None
    is_read_only: bool = False  # GET_TALKER_CONTEXT、ASK_CAN_SAY 等只读请求


# ── DeepSeek 的 Normalize 系统提示词 ──
# 核心职责：将 Talker 的自然语言转换为结构化事件

NORMALIZE_SYSTEM = """You are a cognitive event classifier for an AI agent Kernel. Given a natural language user message sent through the Talker, classify what kind of event it represents and extract structured payload.

Respond ONLY with a JSON object, no markdown, no explanation.

Event types and their payloads:
- "IntentUpdated": payload has "goal" (string, the user's stated objective) and optionally "constraints" (array of strings)
- "BeliefProposed": payload has "belief_id" (derive from content), "claim" (string), "status" ("verified"/"likely"/"unverified"), "confidence" (0.0-1.0)
- "PlanProposed": payload has "plan_id", "plan" with "steps" array
- "EvidenceCandidateFound": payload has "evidence_id", "evidence_type", "source", "title", "extracted_facts" (array of strings), "reliability"
- "ToolStarted": payload has "action_id", "tool", "input_summary"
- "TaskCompleted": payload has "step_id"
- "CommitmentCreated": payload has "commitment_id", "description"

Rules:
1. If user describes a task to do or a question to answer → IntentUpdated
2. If user states a fact or opinion → BeliefProposed
3. If user gives a URL or reference → EvidenceCandidateFound
4. If user asks to check/verify something → IntentUpdated with constraints
5. If message is just greeting/thanks/noise → IntentUpdated with empty goal
6. Always preserve the original meaning in Chinese if the input is Chinese

Examples:
Input: "帮我查一下最近AI公司融资情况"
Output: {"event_type":"IntentUpdated","payload":{"goal":"查询最近AI公司融资情况","constraints":[]}}

Input: "我觉得这个数据不对，上次说是50万，这次变42万了"
Output: {"event_type":"IntentUpdated","payload":{"goal":"验证数据一致性","constraints":["上次说是50万","这次变42万"]}}

Input: "你好"
Output: {"event_type":"IntentUpdated","payload":{"goal":"greeting"}}"""


async def normalize(submission: EventSubmission) -> NormalizeResult:
    """将原始提交转换为结构化 CognitiveEvent。

    两条路径：
    1. 结构化（Thinker）：带映射 request_type → 直接映射
    2. 原始文本（Talker）：自然语言 → DeepSeek 分类 → CognitiveEvent

    这是唯一需要 LLM 调用的阶段——后续所有阶段都是确定性的。
    """
    component = submission.component
    request_type = submission.request_type

    # ── 路径 1：Talker 原始自然语言 ──
    # Talker 发送 request_type="raw"，payload 里带 text 字段
    if component == "talker" and request_type == "raw":
        raw_text = submission.payload.get("text", "")
        if not raw_text:
            return NormalizeResult(False, reason="Raw request missing 'text' in payload")
        return await _normalize_from_text(submission, raw_text)

    # ── 路径 2：结构化（已有逻辑）──
    if component == "talker":
        event_type = TALKER_REQUEST_MAP.get(request_type)
        if event_type is None:
            # 只读请求（ASK_CAN_SAY、GET_TALKER_CONTEXT）——不做 Normalize
            return NormalizeResult(True, is_read_only=True)
        actor = Actor.TALKER

    elif component == "thinker":
        # 从 Thinker 映射表查找，或直接用 request_type 作为 EventType
        event_type = THINKER_EVENT_MAP.get(request_type)
        if event_type is None:
            try:
                event_type = EventType(request_type)
            except ValueError:
                return NormalizeResult(False, reason=f"Unknown event type: {request_type}")
        actor = Actor.THINKER
    else:
        return NormalizeResult(False, reason=f"Unknown component: {component}")

    # 构建 RuntimeRef
    runtime_refs = RuntimeRef()
    merged_runtime_refs = {}
    if isinstance(submission.payload.get("runtime_refs"), dict):
        merged_runtime_refs.update(submission.payload["runtime_refs"])
    if submission.runtime_refs:
        merged_runtime_refs.update(submission.runtime_refs)
    if merged_runtime_refs:
        runtime_refs = RuntimeRef(**merged_runtime_refs)

    # 构建 CognitiveEvent（event_id 由流水线后续分配）
    event = CognitiveEvent(
        event_id="",
        kernel_session_id=submission.session_id,
        run_id=submission.run_id,
        event_type=event_type,
        actor=actor,
        source_component=component,
        payload=dict(submission.payload),
        runtime_refs=runtime_refs,
        visibility=Visibility.SHARED,
        intent_version=submission.intent_version,
    )

    return NormalizeResult(True, event=event)


async def _normalize_from_text(submission: EventSubmission, text: str) -> NormalizeResult:
    """使用 DeepSeek 将自然语言解析为结构化 CognitiveEvent。

    包含跟进消息检测：
    - 短文本（<15 字符）或包含跟进指示词（"比"、"呢"、"上次"等）
    - 且不是新任务（不含"帮我"、"查一下"等）
    → 作为 constraint 追加到现有意图，不替换 goal
    """
    if not DEEPSEEK_API_KEY:
        return _build_event(submission, EventType.INTENT_UPDATED, {"goal": text})

    try:
        from src.kms.decisioning.model import ModelCall
        model = ModelCall()

        # ── 跟进消息检测 ──
        FOLLOWUP_INDICATORS = ["比", "呢", "上次", "刚才", "之前", "那个", "这个", "咋样"]
        is_new_task = any(kw in text for kw in ["帮我", "查一下", "搜索", "找一下", "什么是", "多少钱"])
        is_followup = (len(text) < 15 or any(kw in text for kw in FOLLOWUP_INDICATORS)) and not is_new_task

        if is_followup and submission.intent_version > 0:
            # 跟进消息作为约束追加，不替换原有意图
            return _build_event(submission, EventType.INTENT_UPDATED, {"constraints": [text]})

        # ── 调用 DeepSeek 分类 ──
        result = await model.ask_json(
            system=NORMALIZE_SYSTEM,
            user=f"Input: \"{text}\"\nOutput:",
            max_tokens=200,
        )

        if result is None:
            return _build_event(submission, EventType.INTENT_UPDATED, {"goal": text})

        event_type_str = result.get("event_type", "IntentUpdated")
        payload = result.get("payload", {"goal": text})

        # 跟进消息处理：如果已有意图，不覆盖 goal
        if submission.intent_version > 0 and event_type_str == "IntentUpdated" and "goal" in payload:
            payload["constraints"] = payload.get("constraints", []) + [payload["goal"]]
            payload.pop("goal", None)

        try:
            event_type = EventType(event_type_str)
        except ValueError:
            event_type = EventType.INTENT_UPDATED

        return _build_event(submission, event_type, payload)

    except Exception as e:
        logger.warning("Normalize DeepSeek call failed: %s", e)
        return _build_event(submission, EventType.INTENT_UPDATED, {"goal": text})


def _build_event(submission: EventSubmission, event_type: EventType, payload: dict) -> NormalizeResult:
    """根据解析后的参数构建 CognitiveEvent。

    辅助函数，被结构化路径和原始文本路径共用。
    """
    actor = Actor.TALKER if submission.component == "talker" else Actor.THINKER
    event = CognitiveEvent(
        event_id="",
        kernel_session_id=submission.session_id,
        run_id=submission.run_id,
        event_type=event_type,
        actor=actor,
        source_component=submission.component,
        payload=payload,
        visibility=Visibility.SHARED,
        intent_version=submission.intent_version,
    )
    return NormalizeResult(True, event=event)


# ===========================================================================
# 第 3 阶段：Validate — 策略规则
# ===========================================================================

@dataclass
class ValidateResult:
    """Validate 阶段的输出。"""
    allowed: bool
    reason: Optional[str] = None


# Talker 禁止直接写入以下事件类型——必须通过 Proposal 间接提交
TALKER_FORBIDDEN = {
    EventType.BELIEF_UPDATED,
    EventType.EVIDENCE_ACCEPTED,
    EventType.PLAN_ACCEPTED,
    EventType.TASK_COMPLETED,
}

# Thinker 只允许提交以下事件类型
THINKER_ALLOWED = {
    EventType.PLAN_PROPOSED,
    EventType.BELIEF_PROPOSED,
    EventType.TOOL_STARTED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TOOL_RETRIED,
    EventType.EVIDENCE_CANDIDATE_FOUND,
    EventType.STEP_STARTED,
    EventType.CONFLICT_DETECTED,
    EventType.VERIFICATION_WARNING_RAISED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
    EventType.INTENT_UPDATED,
    EventType.SESSION_CANCELLED,
    # 扩展 Thinker 协议（§6.2）
    EventType.REPLAN_REQUEST,
    EventType.RISK_ASSESSMENT,
    EventType.REASONING_SUMMARY,
    EventType.RAW_RESULT_AVAILABLE,
    EventType.ACTION_BLOCKED,
    EventType.VERIFICATION_RESULT,
    EventType.COMPLETION_CHECK,
}


def validate(
    event: CognitiveEvent,
    existing_intent_version: int = 0,
    *,
    session_status: str = "running",
    active_run_id: str = "",
) -> ValidateResult:
    """根据策略规则验证事件。

    检查项：
    - Talker 不能直接写 belief/evidence/plan
    - Thinker 只能提交白名单内的事件类型
    - 意图版本不能过期
    - 信念必须有 claim 且 confidence 在 0-1 范围内
    """
    et = event.event_type

    # ── Talker 强制规则 ──
    # Talker 不能直接声明 belief、提交 evidence、制定 plan
    # 必须通过 Proposal 间接实现
    if event.actor == Actor.TALKER:
        if et in TALKER_FORBIDDEN:
            return ValidateResult(False, f"Talker cannot submit {et.value}. Use proposal types instead.")

    # ── Thinker 强制规则 ──
    # Thinker 只能提交白名单内的事件类型
    if event.actor == Actor.THINKER:
        if et not in THINKER_ALLOWED:
            return ValidateResult(False, f"Thinker cannot submit {et.value}.")
        if session_status in {"paused", "cancelled", "completed", "failed"}:
            return ValidateResult(
                False,
                f"Session is {session_status}; thinker writes are not accepted.",
            )
        if active_run_id and et not in {EventType.INTENT_UPDATED, EventType.SESSION_CANCELLED}:
            if not event.run_id:
                return ValidateResult(False, "Thinker must include run_id.")
            if event.run_id != active_run_id:
                return ValidateResult(
                    False,
                    f"Stale thinker run: submitted {event.run_id}, active is {active_run_id}",
                )

    # ── 意图版本过时检查 ──
    # 当 Talker 发送新意图时，其 intent_version 不能低于当前版本
    if (
        event.intent_version > 0
        and existing_intent_version > 0
        and event.intent_version < existing_intent_version
    ):
        return ValidateResult(
            False,
            f"Intent version mismatch: submitted v{event.intent_version}, current v{existing_intent_version}",
        )

    # ── 信念输入完整性检查 ──
    # claim 为空或 confidence 越界 → 400 拒绝
    # 此检查在流水线早期执行，防止无效信念进入 Arbitrate 阶段
    if et in (EventType.BELIEF_PROPOSED, EventType.BELIEF_UPDATED):
        claim = event.payload.get("claim", "")
        confidence = event.payload.get("confidence", 0)
        if not claim or len(claim.strip()) < 2:
            return ValidateResult(False, "Belief claim must be at least 2 characters")
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            return ValidateResult(False, f"Belief confidence must be 0-1, got {confidence}")

    return ValidateResult(True)


# ===========================================================================
# 第 4 阶段：Classify — 事件类别路由
# ===========================================================================

@dataclass
class ClassifyResult:
    """Classify 阶段的输出——将事件路由到正确的状态类别。"""
    category: str  # "intent"、"plan"、"evidence"、"belief"、"execution"、"commitment"、"progress"


def classify(event: CognitiveEvent) -> ClassifyResult:
    """确定此事件属于哪个认知状态类别。

    告诉 Reducer 需要更新哪些状态表。
    分类依据是 request_type（EventType），不是 payload 内容。
    已验证：payload 再混乱也不会跨表污染。
    """
    et = event.event_type

    classification_map = {
        EventType.INTENT_UPDATED: "intent",
        EventType.PLAN_PROPOSED: "plan",
        EventType.PLAN_ACCEPTED: "plan",
        EventType.EVIDENCE_CANDIDATE_FOUND: "evidence",
        EventType.EVIDENCE_ACCEPTED: "evidence",
        EventType.BELIEF_PROPOSED: "belief",
        EventType.BELIEF_UPDATED: "belief",
        EventType.TOOL_STARTED: "execution",
        EventType.TOOL_COMPLETED: "execution",
        EventType.TOOL_FAILED: "execution",
        EventType.TOOL_RETRIED: "execution",
        EventType.STEP_STARTED: "plan",
        EventType.TASK_COMPLETED: "plan",
        EventType.TASK_FAILED: "plan",
        EventType.CONFLICT_DETECTED: "belief",
        EventType.VERIFICATION_WARNING_RAISED: "belief",
        EventType.COMMITMENT_CREATED: "commitment",
        EventType.COMMITMENT_UPDATED: "commitment",
        EventType.SESSION_CREATED: "intent",
        EventType.SESSION_CANCELLED: "intent",
        # 扩展 Thinker 协议
        EventType.REPLAN_REQUEST: "plan",
        EventType.RISK_ASSESSMENT: "belief",        # 风险作为特殊信念
        EventType.REASONING_SUMMARY: "execution",   # 推理摘要存入执行账本
        EventType.RAW_RESULT_AVAILABLE: "execution",# 原始结果就绪信号
        EventType.ACTION_BLOCKED: "execution",      # 阻塞同失败处理
        EventType.VERIFICATION_RESULT: "belief",    # 验证结果更新信念
        EventType.COMPLETION_CHECK: "progress",     # 完成度检查
    }

    return ClassifyResult(category=classification_map.get(et, "execution"))


# ===========================================================================
# 第 5 阶段：Arbitrate — 运行评判器
# ===========================================================================

@dataclass
class ArbitrateResult:
    """Arbitrate 阶段的输出——评判器裁决。"""
    modifications: Dict[str, Any] = field(default_factory=dict)
    side_effects: List[CognitiveEvent] = field(default_factory=list)
    judge_results: List[Dict[str, Any]] = field(default_factory=list)
    rejected: bool = False


async def arbitrate(
    event: CognitiveEvent,
    existing_evidence: List[EvidenceItem],
    existing_beliefs: List[BeliefItem],
    kms_url: str = "",
) -> ArbitrateResult:
    """对事件运行评判器。

    两个模式：
    内联模式（默认）：加载来自 src.kms.* 的评判器
    远程模式：POST 到独立的 KMS 服务

    评判器运行顺序：
    Evidence 事件：ReliabilityJudge → DedupJudge → ConflictJudge
                 → SemanticConflictJudge → ContentReliabilityJudge
    Belief 事件：追加 BeliefReviewJudge
    """
    if kms_url:
        # ── 远程 KMS ──
        # 所有评判器在独立进程中运行，Kernel 只发 HTTP
        from src.kms.transport.remote import RemoteKMSClient
        client = RemoteKMSClient(kms_url)
        results = await client.evaluate(event, existing_evidence, existing_beliefs)
        mods = client.get_modifications(results)
        return ArbitrateResult(
            modifications=mods,
            side_effects=client.get_side_effects(results),
            judge_results=results,
            rejected=client.has_rejections(results),
        )

    # ── 内联 KMS ──
    # 评判器在 Kernel 进程内运行，无网络开销
    from src.kms.decisioning.judges import (
        ConflictJudge,
        DedupJudge,
        KMSPipeline,
        ReliabilityJudge,
    )
    from src.kms.decisioning.model import ContentReliabilityJudge, SemanticConflictJudge, DEEPSEEK_API_KEY
    from src.kms.decisioning.belief import BeliefReviewJudge

    pipeline = KMSPipeline()
    # Belief 事件时追加 BeliefReviewJudge ——审查 claim 与证据的一致性
    if event.event_type == EventType.BELIEF_UPDATED:
        pipeline.judges.append(BeliefReviewJudge())
    results = await pipeline.evaluate(event, existing_evidence, existing_beliefs)
    mods = pipeline.get_modifications(results)
    side_effects = pipeline.get_side_effects(results)

    judge_output = [
        {
            "judge_name": r.judge_name,
            "verdict": r.verdict,
            "reason": r.reason,
        }
        for r in results
    ]

    return ArbitrateResult(
        modifications=mods,
        side_effects=side_effects,
        judge_results=judge_output,
        rejected=any(r.verdict == "reject" for r in results),
    )


def is_candidate_event(event_type: EventType) -> bool:
    """只有 candidate/proposal 事件会被 KMS 提升为正式事件。"""
    return event_type in CANDIDATE_TO_FINAL_EVENT


def _accepted_event_type(event_type: EventType) -> EventType:
    return CANDIDATE_TO_FINAL_EVENT[event_type]


def _should_arbitrate(event_type: EventType) -> bool:
    return event_type in (EventType.EVIDENCE_ACCEPTED, EventType.BELIEF_UPDATED)


def _build_final_event(source_event: CognitiveEvent) -> CognitiveEvent:
    """把 Thinker 的 candidate/proposal 提升为 KMS 生成的正式事件。"""
    final_type = _accepted_event_type(source_event.event_type)
    payload = dict(source_event.payload)
    if final_type == EventType.PLAN_ACCEPTED and "intent_version" not in payload:
        payload["intent_version"] = source_event.intent_version

    return CognitiveEvent(
        event_id="",
        kernel_session_id=source_event.kernel_session_id,
        runtime_session_id=source_event.runtime_session_id,
        event_type=final_type,
        actor=Actor.KERNEL_MANAGER,
        source_component="kms",
        payload=payload,
        runtime_refs=source_event.runtime_refs,
        visibility=source_event.visibility,
        intent_version=source_event.intent_version,
    )


async def _assign_event_metadata(
    store,
    session_id: str,
    event: CognitiveEvent,
    *,
    force_next_intent_version: bool = False,
) -> CognitiveEvent:
    """为即将入日志的事件分配唯一 ID、状态版本和 intent 版本。"""
    latest = await store.get_latest_state_version(session_id)
    session = await store.get_session(session_id)
    task_brief = await store.get_task_brief(session_id)
    current_intent_version = (
        task_brief.task_brief_version
        if task_brief and task_brief.task_brief_version
        else session.intent_version if session else 0
    )

    if not event.event_id:
        event.event_id = f"evt_{uuid.uuid4().hex[:12]}"
    event.state_version = latest + 1
    if not event.runtime_session_id and session:
        event.runtime_session_id = session.runtime_session_id
    if not event.run_id and session and event.actor == Actor.THINKER:
        event.run_id = session.active_run_id or ""

    if force_next_intent_version:
        event.intent_version = current_intent_version + 1
    elif event.intent_version <= 0:
        event.intent_version = current_intent_version

    return event


def _merge_execution_payload(event: CognitiveEvent) -> Dict[str, Any]:
    """把 event.runtime_refs 映射回执行态 payload，供 execution reducer 使用。"""
    payload = dict(event.payload)
    runtime_refs = payload.get("runtime_refs")
    if not isinstance(runtime_refs, dict):
        runtime_refs = {}

    if event.runtime_refs:
        for key, value in event.runtime_refs.model_dump().items():
            if value and key not in runtime_refs:
                runtime_refs[key] = value

        if not payload.get("output_ref") and event.runtime_refs.tool_result_ref:
            payload["output_ref"] = event.runtime_refs.tool_result_ref

    if runtime_refs:
        payload["runtime_refs"] = runtime_refs
    return payload


# ===========================================================================
# 第 6 阶段：Reduce — 调用 State Reducer 更新派生状态
# ===========================================================================

async def reduce(
    store,  # SqliteStore
    session_id: str,
    event: CognitiveEvent,
    _processed: Optional[set] = None,
) -> None:
    """调用 State Reducer 从事件更新派生状态。

    幂等：通过 event_id 去重，重放时跳过已处理的事件。
    _processed 集合在重建/重放上下文中跨调用共享。

    §6 架构文档："KMS 负责调用 State Reducer 聚合最新状态"

    处理顺序：
    1. Intent ——意图目标、约束、版本
    2. Plan ——计划步骤、状态转换
    3. Evidence ——证据条目、可靠性评分
    4. Beliefs ——信念声明、置信度（含完整性检查）
    5. Execution ——工具调用、失败、重试
    6. Commitments ——Talker 承诺
    """
    # ── 幂等守卫 ──
    # 相同 event_id 不会重复处理
    if _processed is not None:
        if event.event_id in _processed:
            return
        _processed.add(event.event_id)

    et = event.event_type
    payload = event.payload

    # ── 1. 意图 ──
    # INTENT_UPDATED：更新 goal、constraints、版本号
    # SESSION_CANCELLED：标记已取消
    current_intent = intent_from_task_brief(await store.get_task_brief(session_id))
    old_goal = current_intent.goal if current_intent else ""
    old_version = current_intent.intent_version if current_intent else 0
    new_intent = reduce_intent(current_intent, et, payload)
    if new_intent:
        await store.save_intent(session_id, new_intent)
        if et == EventType.SESSION_CANCELLED:
            await store.update_session_status(
                session_id,
                "cancelled",
                active_run_id="",
            )
            await store.set_cancellation_token(session_id, True)
        if current_intent and new_intent.intent_version != old_version:
            session = await store.get_session(session_id)
            await store.update_session_status(
                session_id, session.status.value if session else "running",
                intent_version=new_intent.intent_version,
            )
            # §5.1：目标变更时设置 cancellation_token，通知 Thinker 停止旧任务
            if new_intent.goal and old_goal and new_intent.goal != old_goal:
                await store.set_cancellation_token(session_id, True)

    # ── 2. 计划 ──
    # PLAN_ACCEPTED：创建/替换计划
    # STEP_STARTED/TASK_COMPLETED：更新步骤状态
    current_plan = plan_from_task_flow(await store.get_task_flow(session_id))
    new_plan = reduce_plan(current_plan, et, payload)
    if et in (
        EventType.PLAN_ACCEPTED,
        EventType.REPLAN_REQUEST,
        EventType.STEP_STARTED,
        EventType.TASK_COMPLETED,
        EventType.TASK_FAILED,
    ):
        if new_plan:
            await store.save_plan(session_id, new_plan)

    # ── 3. 证据 ──
    # EVIDENCE_ACCEPTED：追加或更新证据条目
    evidence_items = await store.get_evidence(session_id)
    reduce_evidence(evidence_items, et, payload)
    if et == EventType.EVIDENCE_ACCEPTED:
        ev_id = payload.get("evidence_id", "")
        saved = False
        for ev in evidence_items:
            if ev.evidence_id == ev_id:
                await store.save_evidence(session_id, ev)
                saved = True
                break
        if not saved and evidence_items:
            await store.save_evidence(session_id, evidence_items[-1])

    # ── 4. 信念（含完整性检查）──
    # BELIEF_UPDATED：创建或更新信念
    # CONFLICT_DETECTED：标记为 CONFLICTING
    # VERIFICATION_WARNING：降低置信度
    beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
    reduce_beliefs(beliefs, et, payload)
    if et in (EventType.BELIEF_UPDATED, EventType.CONFLICT_DETECTED,
              EventType.VERIFICATION_WARNING_RAISED, EventType.RISK_ASSESSMENT,
              EventType.VERIFICATION_RESULT):
        target_id = payload.get("belief_id") or payload.get("assessment_id", "")
        for b in beliefs:
            if b.belief_id == target_id or (et == EventType.RISK_ASSESSMENT and b.claim.startswith("[风险]")):
                if not b.claim or b.confidence < 0.0 or b.confidence > 1.0:
                    logger.warning("KMS: Belief %s integrity check failed", b.belief_id)
                    b.status = BeliefStatus.UNVERIFIED
                    b.confidence = 0.0
                await store.save_belief(session_id, b)
                break
        if et == EventType.RISK_ASSESSMENT and beliefs:
            await store.save_belief(session_id, beliefs[-1])

    # ── 5. 执行动作 ──
    # TOOL_STARTED/COMPLETED/FAILED/RETRIED：跟踪工具调用
    executions = await store.get_executions(session_id)
    execution_payload = _merge_execution_payload(event)
    reduce_execution(executions, et, execution_payload)
    if et in (EventType.TOOL_STARTED, EventType.TOOL_COMPLETED, EventType.TOOL_FAILED,
              EventType.REASONING_SUMMARY, EventType.RAW_RESULT_AVAILABLE, EventType.ACTION_BLOCKED):
        action_id = (
            execution_payload.get("action_id")
            or execution_payload.get("reasoning_id")
            or execution_payload.get("result_id", "")
        )
        for a in executions:
            if a.action_id == action_id:
                await store.save_execution(session_id, a)
                break
        else:
            if executions:
                await store.save_execution(session_id, executions[-1])

    # ── 6. 承诺 ──
    # COMMITMENT_CREATED/UPDATED：Talker 承诺管理
    commitments = commitments_from_todos(await store.get_todo_obligations(session_id))
    reduce_commitments(commitments, et, payload)
    if et in (EventType.COMMITMENT_CREATED, EventType.COMMITMENT_UPDATED):
        cid = payload.get("commitment_id", "")
        for c in commitments:
            if c.commitment_id == cid:
                await store.save_commitment(session_id, c)
                break


# ===========================================================================
# 第 7 阶段：Summarize — Talker 进度合成（懒加载）
# ===========================================================================

async def refresh_progress(store, session_id: str) -> ProgressState:
    """同步刷新 progress_states，供 Gate/Sync/Thinker 直接读取。"""
    plan = plan_from_task_flow(await store.get_task_flow(session_id))
    beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
    intent = intent_from_task_brief(await store.get_task_brief(session_id))
    constraints = intent.constraints if intent else []
    progress = synthesize_progress(session_id, plan, beliefs, constraints)
    await store.save_progress(session_id, progress)
    return progress


async def summarize(store, session_id: str) -> ProgressState:
    """合成面向用户的进度视图（§5.12）。

    progress_states 每次写事件后都会同步刷新；这里仅补充自然语言摘要。
    """
    progress = await refresh_progress(store, session_id)
    safe_fact_count = len(progress.safe_facts)

    # ── 尝试 DeepSeek 自然语言摘要 ──
    # 只有当有信念且 API key 可用时才调用
    if safe_fact_count and DEEPSEEK_API_KEY:
        try:
            from src.kms.decisioning.model import ModelCall
            model = ModelCall()
            safe_text = "\n".join(f"- {f}" for f in progress.safe_facts[:5])
            prompt = (
                f"Status: {progress.status}\n"
                f"Stage: {progress.stage or 'ongoing'}\n"
                f"Safe facts:\n{safe_text}\n\n"
                f"Unresolved items count: {len(progress.unsafe_claims)}\n\n"
                "Write ONE sentence in Chinese summarizing the current progress. "
                "Only mention safe facts and high-level progress. "
                "Do not quote or infer unresolved claims. "
                "Only write the sentence, nothing else."
            )
            raw = await model.ask(system="", user=prompt, max_tokens=150)
            if raw and isinstance(raw, str) and len(raw.strip()) > 5:
                progress.summary = raw.strip()[:300]
        except Exception as e:
            logger.debug("Summarize DeepSeek call failed: %s", e)

    await store.save_progress(session_id, progress)
    return progress


# ===========================================================================
# 第 8 阶段：Gate — 可见性闸门（懒加载）
# ===========================================================================

@dataclass
class GateResult:
    """Visibility Gate 的输出。"""
    allowed: bool
    reason: Optional[str] = None
    safe_alternative: Optional[str] = None


# 规则层：声称完成的触发词
COMPLETION_KEYWORDS = [
    "完成", "已发送", "已发布", "成功了", "搞定了",
    "done", "completed", "finished",
]


def _rule_contradicts_verified_belief(beliefs, proposed_message: str) -> Optional[str]:
    message = proposed_message.lower()
    no_competitor_claim = any(
        marker in message
        for marker in ("没有对手", "没有竞争对手", "唯一", "no competitor", "only choice")
    )
    if not no_competitor_claim:
        return None

    for belief in beliefs:
        if getattr(getattr(belief, "status", None), "value", "") != "verified":
            continue
        claim = (belief.claim or "").lower()
        mentions_competition = any(
            marker in claim
            for marker in ("amd", "竞争", "追赶", "对手", "competitor", "catching up")
        )
        if mentions_competition:
            return belief.claim
    return None


async def gate(store, session_id: str, proposed_message: str = "") -> GateResult:
    """可见性闸门：确定 Talker 能否安全地说某句话（§5.11）。

    双层检查：
    1. 规则层：关键词匹配完成声明 + unsafe_claims 字面匹配
    2. 语义层（DeepSeek）：检查消息是否与已验证信念矛盾

    规则层先运行（免费、快速），语义层后补（DeepSeek，仅在规则层放行后）。
    """
    progress = await store.get_progress(session_id)
    if not progress:
        progress = await refresh_progress(store, session_id)

    if not proposed_message:
        return GateResult(True)

    # ── 第 1 层：规则检查 ──
    # 声称完成但任务未完成 → 拦截
    if any(kw in proposed_message.lower() for kw in COMPLETION_KEYWORDS):
        if progress.status != "completed":
            return GateResult(
                False,
                reason="任务尚未完成，不能宣称已完成",
                safe_alternative=progress.summary,
            )

    # 包含未验证声明 → 拦截
    for claim in progress.unsafe_claims:
        if claim in proposed_message:
            return GateResult(
                False,
                reason=f"包含未验证内容: {claim}",
                safe_alternative="，".join(progress.safe_facts) if progress.safe_facts else progress.summary,
            )

    # ── 第 2 层：语义检查（DeepSeek）──
    # 检查消息语义上是否与已验证信念矛盾
    beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
    contradicted = _rule_contradicts_verified_belief(beliefs, proposed_message)
    if contradicted:
        return GateResult(
            False,
            reason=f"与信念矛盾: {contradicted[:80]}",
            safe_alternative=progress.summary,
        )

    if beliefs and DEEPSEEK_API_KEY:
        try:
            from src.kms.decisioning.model import ModelCall
            model = ModelCall()
            belief_text = "\n".join(
                f"- [{b.status.value}] {b.claim}"
                for b in beliefs[:5]
            )
            result = await model.ask_json(
                system="You are a fact-checker guarding an AI agent's output. Check if a proposed message contradicts any of the agent's verified beliefs. Respond ONLY with JSON: {\"contradicts\": true/false, \"which_belief\": \"brief quote of contradicted belief\", \"reason\": \"one sentence\"}",
                user=f"Agent's current beliefs:\n{belief_text}\n\nProposed message: \"{proposed_message}\"\n\nDoes this message contradict any belief?",
                max_tokens=100,
            )
            if result and result.get("contradicts"):
                return GateResult(
                    False,
                    reason=f"与信念矛盾: {result.get('which_belief', '')[:80]} — {result.get('reason', '')}",
                    safe_alternative=progress.summary,
                )
        except Exception as e:
            logger.debug("Gate semantic check failed: %s", e)

    return GateResult(True)


# ===========================================================================
# 第 9 阶段：Sync — 外部同步视图（懒加载）
# ===========================================================================

async def sync(store, session_id: str) -> Optional[SyncView]:
    """生成外部同步视图（§5.14）。

    供 Multica 等外部协作系统使用。
    当前为桩——无已连接的外部系统。
    """
    progress = await summarize(store, session_id)
    session = await store.get_session(session_id)
    intent = intent_from_task_brief(await store.get_task_brief(session_id))
    commitments = commitments_from_todos(await store.get_todo_obligations(session_id))
    executions = await store.get_executions(session_id)
    if not progress or not session:
        return None

    pending_confirmations = [
        commitment.statement
        for commitment in commitments
        if commitment.requires_confirmation and commitment.status.value == "pending"
    ]
    failed_tools = [
        action.tool or action.action_id
        for action in executions
        if action.status == "failed"
    ]

    blocking_reason = None
    if intent and intent.cancelled:
        blocking_reason = "session_cancelled"
    elif pending_confirmations:
        blocking_reason = "awaiting_user_confirmation"
    elif progress.needs_user_input:
        blocking_reason = "awaiting_user_input"
    elif progress.status == "blocked":
        blocking_reason = "task_blocked"
    elif failed_tools:
        blocking_reason = f"tool_failed:{failed_tools[0]}"
    elif session.last_interrupted_run_id:
        blocking_reason = "interrupted_by_new_request"

    return SyncView(
        external_task_id=session.external_task_id,
        status=progress.status,
        stage=progress.stage,
        summary=progress.summary,
        needs_user_input=progress.needs_user_input,
        blocking_reason=blocking_reason,
        pending_confirmations=pending_confirmations,
        final_facts=progress.safe_facts[:5] if progress.status == "completed" else [],
    )


# ===========================================================================
# 完整流水线编排器
# ===========================================================================

@dataclass
class PipelineResult:
    """完整 KMS 流水线的运行结果。"""
    accepted: bool
    stage: str = ""          # 哪个阶段拒绝/接受了
    reason: Optional[str] = None
    event: Optional[CognitiveEvent] = None
    judge_results: List[Dict[str, Any]] = field(default_factory=list)
    is_read_only: bool = False
    latest_state_version: int = 0


async def run_pipeline(
    store,       # SqliteStore
    submission: EventSubmission,
    kms_url: str = "",
) -> PipelineResult:
    """运行完整的 9 阶段 KMS 流水线。

    这是引擎调用的单一入口。流水线处理：
    ① Intake → ② Normalize → ③ Validate → ④ Classify →
    ⑤ Arbitrate → ⑤.5 EventLog → ⑥ Reduce → return。

    第 7-9 阶段（Summarize/Gate/Sync）为懒加载——仅在
    Talker 显式查询时运行。
    """
    session_id = submission.session_id

    # ── ① Intake：接收 ──
    intake_result = intake(submission)
    if not intake_result.accepted:
        return PipelineResult(False, stage="intake", reason=intake_result.reason)

    # ── ② Normalize：标准化 ──
    # Talker 自然语言 → DeepSeek → CognitiveEvent
    # Thinker JSON → 直接映射 → CognitiveEvent
    norm_result = await normalize(submission)
    if not norm_result.accepted:
        return PipelineResult(False, stage="normalize", reason=norm_result.reason)

    # 只读请求在此停止
    if norm_result.is_read_only:
        return PipelineResult(True, stage="normalize", is_read_only=True)

    event = norm_result.event

    # ── ③ Validate：验证 ──
    # 权限检查、输入完整性、版本检查
    current_intent = intent_from_task_brief(await store.get_task_brief(session_id))
    existing_version = current_intent.intent_version if current_intent else 0
    session = await store.get_session(session_id)
    val_result = validate(
        event,
        existing_version,
        session_status=session.status.value if session else "running",
        active_run_id=session.active_run_id if session else "",
    )
    if not val_result.allowed:
        return PipelineResult(False, stage="validate", reason=val_result.reason)

    # ── ④ Classify：分类 ──
    # 按 request_type 路由到正确的状态类别
    class_result = classify(event)

    # ── ⑤.5 Event Log：先记录提交事件，再决定是否生成正式事件 ──
    force_next_intent_version = event.event_type == EventType.INTENT_UPDATED
    await _assign_event_metadata(
        store,
        session_id,
        event,
        force_next_intent_version=force_next_intent_version,
    )
    await store.append_event(event)
    await register_runtime_references(store, session_id, event)

    primary_event = event
    latest_state_version = event.state_version

    # ── ⑤ Arbitrate：proposal/candidate 先提升，再做 KMS 仲裁 ──
    arb_result: ArbitrateResult = ArbitrateResult()
    if is_candidate_event(event.event_type):
        primary_event = _build_final_event(event)
    if _should_arbitrate(primary_event.event_type):
        existing_evidence = await store.get_evidence(session_id)
        existing_beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
        arb_result = await arbitrate(primary_event, existing_evidence, existing_beliefs, kms_url)

        for key, val in arb_result.modifications.items():
            primary_event.payload[key] = val

    if arb_result.rejected:
        await refresh_progress(store, session_id)
        return PipelineResult(
            True,
            stage="arbitrate",
            event=event,
            judge_results=arb_result.judge_results,
            latest_state_version=latest_state_version,
        )

    # ── ⑥ Reduce：只有正式事件才驱动派生状态 ──
    if is_candidate_event(event.event_type):
        await _assign_event_metadata(store, session_id, primary_event)
        await store.append_event(primary_event)
        await register_runtime_references(store, session_id, primary_event)
        await reduce(store, session_id, primary_event)
        latest_state_version = primary_event.state_version
    else:
        await reduce(store, session_id, primary_event)

    # 副作用事件（ConflictDetected 等）也通过相同路径处理，但必须有独立版本号
    for se in arb_result.side_effects:
        se.kernel_session_id = session_id
        if not se.source_component:
            se.source_component = "kms"
        if se.actor == Actor.KERNEL_MANAGER and se.intent_version <= 0:
            se.intent_version = primary_event.intent_version
        await _assign_event_metadata(store, session_id, se)
        await store.append_event(se)
        await register_runtime_references(store, session_id, se)
        await reduce(store, session_id, se)
        latest_state_version = se.state_version

    await refresh_progress(store, session_id)

    return PipelineResult(
        True,
        stage="reduce",
        event=primary_event,
        judge_results=arb_result.judge_results,
        latest_state_version=latest_state_version,
    )
