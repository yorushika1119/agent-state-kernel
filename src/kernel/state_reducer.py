"""State Reducer — 将事件聚合为派生状态。

确定性、可重放。不包含 LLM 调用。KMS 级别的分类
（如"这个网页内容是否是可靠证据？"）在 Reducer 运行之前
已经完成；Reducer 仅组合已被分类的事件。

规则源自功能设计文档 §5.3–§5.12。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.schema.events import EventType
from src.schema.state import (
    BeliefItem,
    BeliefStatus,
    Commitment,
    CommitmentStatus,
    EvidenceItem,
    EvidenceType,
    ExecutionAction,
    IntentState,
    PlanState,
    PlanStatus,
    ProgressState,
    Reliability,
    StepStatus,
)
from src.utils.time import utc_now

logger = logging.getLogger(__name__)


# ── 工具函数 ──

def _to_str(v: Any) -> str:
    """安全转换为字符串。"""
    if v is None:
        return ""
    return str(v)


def _to_list(v: Any) -> list:
    """安全转换为列表。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _find_plan_step(current: Optional[PlanState], step_id: str):
    if current is None or not step_id:
        return None
    for step in current.steps:
        if step.step_id == step_id:
            return step
    return None


def _get_current_plan_step(current: Optional[PlanState]):
    if current is None:
        return None
    step = _find_plan_step(current, current.current_step)
    if step is not None:
        return step
    for item in current.steps:
        if item.status == StepStatus.RUNNING:
            return item
    return None


def _resolve_terminal_plan_step(current: Optional[PlanState], payload: Dict[str, Any]):
    step_id = _to_str(payload.get("step_id"))
    step = _find_plan_step(current, step_id)
    if step is not None:
        return step
    if not step_id or step_id.startswith("run_"):
        return _get_current_plan_step(current)
    return None


# ===========================================================================
# Intent Reducer — 意图状态
# ===========================================================================

def reduce_intent(
    current: Optional[IntentState],
    event_type: EventType,
    payload: Dict[str, Any],
) -> Optional[IntentState]:
    """从事件归约意图状态。

    核心规则：
    - INTENT_UPDATED：更新目标/约束/版本
    - SESSION_CANCELLED：标记为已取消
    - 意图合并：跟进消息不会覆盖非平凡 goal

    合并策略：
    1. trivial goal（"谢谢"、"greeting"、<3 字符）→ 保留旧 goal
    2. refinement（新 goal 比旧 goal 短）→ 保留旧 goal（这是细化，不是替换）
    3. 约束：追加合并，不替换
    """
    if event_type == EventType.SESSION_CREATED:
        return IntentState(intent_version=0, goal="", constraints=[])
    if event_type == EventType.INTENT_UPDATED:
        intent = current or IntentState(intent_version=0)
        intent.intent_version += 1
        new_goal = _to_str(payload.get("goal", ""))
        new_constraints = _to_list(payload.get("constraints", []))

        # ── 意图合并器：不要用平凡 goal 覆盖非平凡 goal ──
        # 例如："和去年比怎么样" 应该作为约束而不是新目标
        TRIVIAL_GOALS = {"greeting", "ok", "thanks", "谢谢", "好的", "是", "对", "不是", "不对"}
        is_trivial = not new_goal or new_goal.lower() in TRIVIAL_GOALS or len(new_goal) < 3

        # 短跟进消息（≤5字且不共享关键词）作为约束追加，不替换goal
        # 原启发式(len(新)<len(旧)→refinement)被"chip research"→"write letter"误触，已替换
        is_short_followup = (
            intent.goal
            and new_goal
            and len(new_goal) <= 5
            and not any(w in new_goal for w in intent.goal.split() if len(w) > 1)
        )

        if is_trivial and intent.goal:
            pass  # 保留已有
        elif is_short_followup and intent.goal:
            intent.constraints.append(new_goal)
        elif new_goal:
            intent.goal = new_goal

        # 约束：合并（追加新的，不替换）
        if new_constraints:
            existing = set(intent.constraints or [])
            for c in new_constraints:
                existing.add(c)
            intent.constraints = list(existing)

        intent.output_format = _to_str(payload.get("output_format", intent.output_format))
        intent.priority = _to_str(payload.get("priority", intent.priority))
        intent.cancelled = bool(payload.get("cancelled", False))
        intent.last_user_update_at = utc_now()
        return intent
    if event_type == EventType.SESSION_CANCELLED:
        if current:
            current.cancelled = True
        return current
    # ── §6.2 扩展：COMPLETION_CHECK 不改变意图，仅触发 Summarize ──
    if event_type == EventType.COMPLETION_CHECK:
        return current
    return current


# ===========================================================================
# Plan Reducer — 计划状态
# ===========================================================================

def reduce_plan(
    current: Optional[PlanState],
    event_type: EventType,
    payload: Dict[str, Any],
) -> Optional[PlanState]:
    """从事件归约计划状态。

    状态转换：
    - PLAN_ACCEPTED：创建/替换计划，第一步标记为 RUNNING
    - STEP_STARTED：标记步骤为 RUNNING
    - TASK_COMPLETED：标记步骤为 COMPLETED，移到下一个 PENDING 步骤
    - TASK_FAILED：标记步骤为 FAILED
    - 所有步骤完成后：计划状态变为 COMPLETED
    """
    if event_type == EventType.PLAN_ACCEPTED:
        plan_data = payload.get("plan", {})
        steps_data = plan_data.get("steps", [])
        from src.schema.state import PlanStep
        steps = [
            PlanStep(
                step_id=s.get("step_id", f"s{i}"),
                name=s.get("name", f"Step {i}"),
                status=StepStatus(s.get("status", "pending")),
                owner=s.get("owner", "thinker"),
                depends_on=s.get("depends_on", []),
            )
            for i, s in enumerate(steps_data)
        ]
        if steps and not any(step.status == StepStatus.RUNNING for step in steps):
            steps[0].status = StepStatus.RUNNING
        return PlanState(
            plan_id=payload.get("plan_id", ""),
            status=PlanStatus.ACTIVE,
            steps=steps,
            current_step=steps[0].step_id if steps else "",
            intent_version=payload.get("intent_version", 0),
        )
    if event_type == EventType.STEP_STARTED and current:
        step = _find_plan_step(current, _to_str(payload.get("step_id")))
        if step is not None:
            step.status = StepStatus.RUNNING
            current.status = PlanStatus.ACTIVE
            current.current_step = step.step_id
        return current
    if event_type == EventType.TASK_COMPLETED and current:
        step = _resolve_terminal_plan_step(current, payload)
        if step is None:
            return current
        step.status = StepStatus.COMPLETED
        next_step = next((item for item in current.steps if item.status == StepStatus.PENDING), None)
        if next_step is None:
            current.current_step = ""
            current.status = PlanStatus.COMPLETED
            return current
        next_step.status = StepStatus.RUNNING
        current.current_step = next_step.step_id
        current.status = PlanStatus.ACTIVE
        return current
    if event_type == EventType.TASK_FAILED and current:
        step = _resolve_terminal_plan_step(current, payload)
        if step is None:
            return current
        step.status = StepStatus.FAILED
        current.current_step = step.step_id
        current.status = PlanStatus.BLOCKED
        return current
    # ── §6.2 扩展：REPLAN_REQUEST 覆盖当前计划 ──
    if event_type == EventType.REPLAN_REQUEST:
        plan_data = payload.get("plan", {})
        steps_data = plan_data.get("steps", [])
        from src.schema.state import PlanStep
        steps = [
            PlanStep(
                step_id=s.get("step_id", f"s{i}"),
                name=s.get("name", f"Step {i}"),
                status=StepStatus(s.get("status", "pending")),
                owner=s.get("owner", "thinker"),
                depends_on=s.get("depends_on", []),
            )
            for i, s in enumerate(steps_data)
        ]
        if steps and not any(step.status == StepStatus.RUNNING for step in steps):
            steps[0].status = StepStatus.RUNNING
        return PlanState(
            plan_id=payload.get("plan_id", ""),
            status=PlanStatus.ACTIVE,
            steps=steps,
            current_step=steps[0].step_id if steps else "",
            intent_version=payload.get("intent_version", 0),
        )
    return current


# ===========================================================================
# Evidence Reducer — 证据
# ===========================================================================

def reduce_evidence(
    items: List[EvidenceItem],
    event_type: EventType,
    payload: Dict[str, Any],
) -> List[EvidenceItem]:
    """从事件归约证据。

    EVIDENCE_ACCEPTED：追加新证据或更新已有证据（按 evidence_id 去重）。
    注意：可靠性评分由 Arbitrate 阶段完成；Reducer 只负责存储。
    """
    if event_type == EventType.EVIDENCE_ACCEPTED:
        evidence = EvidenceItem(
            evidence_id=payload.get("evidence_id", ""),
            evidence_type=EvidenceType(payload.get("evidence_type", "web_page")),
            source=_to_str(payload.get("source", "")),
            title=_to_str(payload.get("title", "")),
            observed_at=utc_now(),
            source_date=payload.get("source_date"),
            reliability=Reliability(payload.get("reliability", "unknown")),
            extracted_facts=_to_list(payload.get("extracted_facts", [])),
            raw_ref=payload.get("raw_ref"),
            accepted_by="kernel_manager",
        )
        existing_ids = {e.evidence_id for e in items}
        if evidence.evidence_id not in existing_ids:
            items.append(evidence)
        else:
            for i, e in enumerate(items):
                if e.evidence_id == evidence.evidence_id:
                    items[i] = evidence
                    break
    return items


# ===========================================================================
# Belief Reducer — 信念
# ===========================================================================

def reduce_beliefs(
    beliefs: List[BeliefItem],
    event_type: EventType,
    payload: Dict[str, Any],
) -> List[BeliefItem]:
    """从事件归约信念。

    - BELIEF_UPDATED：创建或更新信念（按 belief_id 去重）
    - CONFLICT_DETECTED：标记信念为 CONFLICTING
    - VERIFICATION_WARNING：降低置信度 0.2

    信念的置信度和状态由 Arbitrate 阶段的 BeliefReviewJudge 审查后写入。
    Reducer 只负责存储——它不判断信念是否合理。
    """
    if event_type == EventType.BELIEF_UPDATED:
        belief = BeliefItem(
            belief_id=payload.get("belief_id", ""),
            claim=_to_str(payload.get("claim", "")),
            status=BeliefStatus(payload.get("status", "unverified")),
            confidence=float(payload.get("confidence", 0.0)),
            supporting_evidence=_to_list(payload.get("supporting_evidence", [])),
            conflicting_evidence=_to_list(payload.get("conflicting_evidence", [])),
            visibility=_to_str(payload.get("visibility", "shared")),
            last_verified_at=utc_now(),  # §5.8：Belief 创建/更新时自动记录验证时间
        )
        existing_ids = {b.belief_id for b in beliefs}
        if belief.belief_id not in existing_ids:
            beliefs.append(belief)
        else:
            for i, b in enumerate(beliefs):
                if b.belief_id == belief.belief_id:
                    beliefs[i] = belief
                    break
    if event_type == EventType.CONFLICT_DETECTED and beliefs:
        belief_id = _to_str(payload.get("belief_id"))
        for b in beliefs:
            if b.belief_id == belief_id:
                b.status = BeliefStatus.CONFLICTING
                break
    if event_type == EventType.VERIFICATION_WARNING_RAISED and beliefs:
        belief_id = _to_str(payload.get("belief_id"))
        for b in beliefs:
            if b.belief_id == belief_id:
                b.confidence = max(0.0, b.confidence - 0.2)
                break
    # ── §6.2 扩展：RISK_ASSESSMENT 作为未验证信念 ──
    if event_type == EventType.RISK_ASSESSMENT:
        belief = BeliefItem(
            belief_id=payload.get("assessment_id", f"risk_{utc_now().timestamp()}"),
            claim=f"[风险] {_to_str(payload.get('risk', ''))}",
            status=BeliefStatus.UNVERIFIED,
            confidence=float(payload.get("severity", 0.5)),
            visibility=_to_str(payload.get("visibility", "shared")),
        )
        beliefs.append(belief)
    # ── §6.2 扩展：VERIFICATION_RESULT 更新信念置信度 ──
    if event_type == EventType.VERIFICATION_RESULT:
        target_id = _to_str(payload.get("belief_id"))
        new_status = _to_str(payload.get("status", ""))
        new_conf = float(payload.get("confidence", 0))
        for b in beliefs:
            if b.belief_id == target_id:
                if new_status:
                    b.status = BeliefStatus(new_status)
                if new_conf > 0:
                    b.confidence = new_conf
                b.last_verified_at = utc_now()
                break
    return beliefs


# ===========================================================================
# Execution Reducer — 执行动作
# ===========================================================================

def reduce_execution(
    actions: List[ExecutionAction],
    event_type: EventType,
    payload: Dict[str, Any],
) -> List[ExecutionAction]:
    """从事件归约执行动作。

    跟踪工具调用的生命周期：
    - TOOL_STARTED：创建执行记录（status=running）
    - TOOL_COMPLETED：标记成功
    - TOOL_FAILED：标记失败
    - TOOL_RETRIED：重试计数 +1
    """
    if event_type in (EventType.TOOL_STARTED,):
        action = ExecutionAction(
            action_id=payload.get("action_id", ""),
            step_id=_to_str(payload.get("step_id", "")),
            tool=_to_str(payload.get("tool", "")),
            status="running",
            input_summary=_to_str(payload.get("input_summary", "")),
            runtime_refs=payload.get("runtime_refs", {}),
        )
        actions.append(action)
    if event_type in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED):
        action_id = _to_str(payload.get("action_id"))
        for a in actions:
            if a.action_id == action_id:
                a.status = "success" if event_type == EventType.TOOL_COMPLETED else "failed"
                a.ended_at = utc_now()
                a.output_ref = payload.get("output_ref")
                break
    if event_type == EventType.TOOL_RETRIED:
        action_id = _to_str(payload.get("action_id"))
        for a in actions:
            if a.action_id == action_id:
                a.retry_count += 1
                break
    # ── §6.2 扩展：REASONING_SUMMARY 追加推理记录 ──
    if event_type == EventType.REASONING_SUMMARY:
        action = ExecutionAction(
            action_id=payload.get("reasoning_id", f"reason_{utc_now().timestamp()}"),
            tool="reasoning",
            status="success",
            input_summary=_to_str(payload.get("summary", ""))[:200],
        )
        actions.append(action)
    # ── §6.2 扩展：RAW_RESULT_AVAILABLE 追加就绪信号 ──
    if event_type == EventType.RAW_RESULT_AVAILABLE:
        action = ExecutionAction(
            action_id=payload.get("result_id", f"raw_{utc_now().timestamp()}"),
            tool="raw_result",
            status="success",
            input_summary=f"raw: {_to_str(payload.get('ref', ''))}",
        )
        actions.append(action)
    # ── §6.2 扩展：ACTION_BLOCKED 同失败处理 ──
    if event_type == EventType.ACTION_BLOCKED:
        action = ExecutionAction(
            action_id=payload.get("action_id", f"blocked_{utc_now().timestamp()}"),
            step_id=_to_str(payload.get("step_id", "")),
            tool=_to_str(payload.get("tool", "")),
            status="failed",
            input_summary=f"[阻塞] {_to_str(payload.get('reason', ''))}",
        )
        actions.append(action)
    return actions


# ===========================================================================
# Commitment Reducer — 承诺
# ===========================================================================

def reduce_commitments(
    commitments: List[Commitment],
    event_type: EventType,
    payload: Dict[str, Any],
) -> List[Commitment]:
    """从事件归约承诺。

    Talker 对用户做出的承诺管理。
    - COMMITMENT_CREATED：创建待定承诺
    - COMMITMENT_UPDATED：更新承诺状态（已完成/已放弃）
    """
    if event_type == EventType.COMMITMENT_CREATED:
        c = Commitment(
            commitment_id=payload.get("commitment_id", ""),
            statement=_to_str(payload.get("statement", "")),
            created_by=_to_str(payload.get("created_by", "talker")),
            status=CommitmentStatus.PENDING,
            requires_confirmation=bool(payload.get("requires_confirmation", False)),
            related_intent_version=payload.get("related_intent_version", 0),
        )
        commitments.append(c)
    if event_type == EventType.COMMITMENT_UPDATED:
        cid = _to_str(payload.get("commitment_id"))
        new_status = _to_str(payload.get("status", ""))
        for c in commitments:
            if c.commitment_id == cid:
                if new_status:
                    c.status = CommitmentStatus(new_status)
                if payload.get("resolved_at"):
                    c.resolved_at = utc_now()
                break
    return commitments


# ===========================================================================
# Progress Synthesizer — 合成 Talker 可读进度
# ===========================================================================

def synthesize_progress(
    session_id: str,
    plan: Optional[PlanState],
    beliefs: List[BeliefItem],
    constraints: List[str],
) -> ProgressState:
    """合成面向用户的进度视图。

    这是决定 Talker 能看到什么的关键函数。
    规则：
    - VERIFIED + confidence ≥ 0.8 → safe_facts
    - LIKELY + confidence ≥ 0.5 → safe_facts（带 [待确认] 标记）
    - UNVERIFIED 或 LIKELY + confidence < 0.5 → unsafe_claims
    - CONFLICTING → unsafe_claims

    当没有计划但已有信念时，自动生成最小计划
    （这样 Talker 不会看到"无活动计划"）。
    """
    if plan is None:
        # 自动计划：如有信念或证据，合成一个桩
        if beliefs:
            safe_count = len([b for b in beliefs if b.status == BeliefStatus.VERIFIED])
            return ProgressState(
                session_id=session_id,
                status="running",
                stage="分析中",
                summary=f"已形成 {len(beliefs)} 条判断，其中 {safe_count} 条已确认" if safe_count else f"正在分析 {len(beliefs)} 条初步判断",
                safe_facts=[
                    b.claim for b in beliefs
                    if b.status == BeliefStatus.VERIFIED and b.confidence >= 0.8
                ],
                unsafe_claims=[
                    b.claim for b in beliefs
                    if b.status in (BeliefStatus.UNVERIFIED, BeliefStatus.CONFLICTING)
                    or (b.status == BeliefStatus.LIKELY and b.confidence < 0.5)
                ],
                needs_user_input=len(beliefs) > 0 and safe_count == 0,
            )
        return ProgressState(
            session_id=session_id,
            status="idle",
            summary="等待任务开始。",
        )

    # 确定当前步骤的阶段
    current_step = next(
        (s for s in plan.steps if s.step_id == plan.current_step), None
    )

    # 从信念中收集安全事实和未验证声明
    safe_facts: List[str] = []
    unsafe_claims: List[str] = []
    for b in beliefs:
        if b.status == BeliefStatus.VERIFIED and b.confidence >= 0.8:
            safe_facts.append(b.claim)
        elif b.status == BeliefStatus.LIKELY and b.confidence >= 0.5:
            safe_facts.append(f"[待确认] {b.claim}")
        elif b.status in (BeliefStatus.UNVERIFIED, BeliefStatus.LIKELY) and b.confidence < 0.5:
            unsafe_claims.append(b.claim)
        elif b.status == BeliefStatus.CONFLICTING:
            unsafe_claims.append(b.claim)

    # 确定允许和禁止的动作
    allowed_actions = ["report_progress", "ask_clarifying_question"]
    forbidden_actions: List[str] = []

    # 没有已验证的信念 → 不能声称任务完成
    if not any(b.status == BeliefStatus.VERIFIED for b in beliefs):
        forbidden_actions.append("claim_task_completed")

    # 检查约束中的禁止动作
    if any("不能直接发送" in c or "不能发送" in c for c in constraints):
        forbidden_actions.append("send_external_email")

    # 状态映射
    if plan.status == PlanStatus.COMPLETED:
        status = "completed"
        allowed_actions.append("claim_task_completed")
    elif plan.status == PlanStatus.BLOCKED:
        status = "blocked"
    elif plan.status == PlanStatus.CANCELLED:
        status = "cancelled"
    else:
        status = "running"

    # 合成摘要文本
    summary_parts: List[str] = []
    if current_step:
        summary_parts.append(f"当前阶段: {current_step.name}")
    verified_count = len([b for b in beliefs if b.status == BeliefStatus.VERIFIED])
    if verified_count:
        summary_parts.append(f"已确认 {verified_count} 条结论")
    if unsafe_claims:
        summary_parts.append(f"{len(unsafe_claims)} 条判断待验证")

    return ProgressState(
        session_id=session_id,
        status=status,
        stage=current_step.name if current_step else "",
        summary="，".join(summary_parts) if summary_parts else "执行中",
        safe_facts=safe_facts,
        unsafe_claims=unsafe_claims,
        needs_user_input=len(unsafe_claims) > 0 and not safe_facts,
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
    )
