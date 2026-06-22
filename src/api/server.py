"""Kernel REST API 服务器。

提供：
- 会话管理（创建/查询/列表/删除/取消）
- 事件提交（从 Thinker/Talker → KMS 流水线）
- 视图查询（talker/thinker/sync/debug 四层）
- ASK_CAN_SAY 可见性检查（Gate）
- 断点恢复（从事件日志重建派生状态）
- 交互式仪表盘（/dashboard）
- 自动演示（/demo/run）
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.kms.manager import KmsManager
from src.kernel.engine import KernelEngine
from src.stores.sqlite_store import SqliteStore
from src.schema.events import EventSubmission

logger = logging.getLogger(__name__)

# ── 请求/响应模型 ──

class CreateSessionRequest(BaseModel):
    """创建会话请求。"""
    agent_id: str = ""
    runtime_id: str = ""
    runtime_session_id: str = ""
    runtime_type: str = "cli-agent"
    external_source: str = ""
    external_workspace_id: str = ""
    external_issue_id: str = ""
    external_task_id: str = ""


class ObserveUserSessionRequest(BaseModel):
    user_session_id: str = ""
    runtime_session_id: str = ""
    runtime_id: str = ""
    runtime_type: str = "cli-agent"
    agent_id: str = ""
    session_kind: str = "user_chat"
    created_by: str = "runtime"


class SubmitEventRequest(BaseModel):
    """提交事件请求——Thinker 或 Talker 的唯一入口。"""
    session_id: str
    component: str = "thinker"        # "talker" 或 "thinker"
    request_type: str                  # EventType 值，或 Talker 用 "raw"
    payload: dict = Field(default_factory=dict)
    intent_version: int = 0
    run_id: str = ""
    runtime_refs: Optional[dict] = None


class DispatchUserMessageRequest(BaseModel):
    """用户新消息调度请求——由 Kernel 判断新任务/继续/打断。"""
    text: str
    runtime_session_id: str = ""
    runtime_id: str = ""
    runtime_type: str = "cli-agent"
    agent_id: str = ""
    external_source: str = ""
    external_workspace_id: str = ""
    external_issue_id: str = ""
    external_task_id: str = ""
    target_session_id: str = ""
    user_session_id: str = ""
    mode: str = "auto"  # auto|new_task


class CompleteRunRequest(BaseModel):
    session_id: str
    run_id: str
    session_status: str = "running"


class ClaimThinkerDispatchRequest(BaseModel):
    dispatch_id: str = ""
    thinker_id: str = ""
    kernel_session_id: str = ""
    task_id: str = ""


class CompleteThinkerDispatchRequest(BaseModel):
    session_status: str = "completed"


class FailThinkerDispatchRequest(BaseModel):
    error: str = ""
    session_status: str = "failed"


async def _create_dispatch_notification(
    engine: KernelEngine,
    dispatch,
    *,
    notification_type: str,
    urgency: str,
    reason: str,
) -> None:
    await engine.store.create_observer_notification(
        target="observer",
        kernel_session_id=dispatch.kernel_session_id,
        task_id=dispatch.task_id,
        notification_type=notification_type,
        urgency=urgency,
        reason=reason,
        progress_ref=dispatch.run_id,
        suggested_observer_context={
            "dispatch_id": dispatch.dispatch_id,
            "run_id": dispatch.run_id,
            "task_id": dispatch.task_id,
        },
        delivery_policy={
            "dedupe_key": f"{dispatch.task_id or dispatch.kernel_session_id}:{notification_type}",
            "requires_user_visible_message": notification_type in {"task_failed", "task_done"},
        },
    )


class AskCanSayRequest(BaseModel):
    """Gate 发言检查请求。"""
    session_id: str
    proposed_message: str


# ── 应用工厂 ──

_store: Optional[SqliteStore] = None
_engine: Optional[KernelEngine] = None
_kms_manager: Optional[KmsManager] = None


def get_engine() -> KernelEngine:
    """获取引擎实例。未初始化时抛出异常。"""
    if _engine is None:
        raise RuntimeError("Kernel not initialized")
    return _engine


def get_kms_manager() -> KmsManager:
    if _kms_manager is None:
        raise RuntimeError("KMS manager not initialized")
    return _kms_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时连接 SQLite，关闭时断开。"""
    global _store, _engine, _kms_manager
    db_path = Path("data/kernel.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _store = SqliteStore(str(db_path))
    await _store.connect()
    _engine = KernelEngine(_store)
    _kms_manager = KmsManager(_store, _engine)
    logger.info("Kernel store connected at %s", db_path)
    yield
    if _store:
        await _store.close()
        logger.info("Kernel store closed")


app = FastAPI(
    title="Agent State Kernel",
    version="0.2.0",
    lifespan=lifespan,
)


# ===========================================================================
# 会话端点
# ===========================================================================

@app.post("/kernel/sessions")
async def create_session(req: CreateSessionRequest):
    """创建新的 Kernel 会话。

    自动写入 SessionCreated 事件，返回 kernel_session_id。
    """
    engine = get_engine()
    session = await engine.create_session(
        agent_id=req.agent_id,
        runtime_id=req.runtime_id,
        runtime_session_id=req.runtime_session_id,
        runtime_type=req.runtime_type,
        external_source=req.external_source,
        external_workspace_id=req.external_workspace_id,
        external_issue_id=req.external_issue_id,
        external_task_id=req.external_task_id,
    )
    return session.model_dump()


@app.post("/kernel/user-sessions/observe")
async def observe_user_session(req: ObserveUserSessionRequest):
    """观察或创建 User Session，不一定创建 Kernel task。"""
    engine = get_engine()
    session = await engine.store.observe_user_session(
        user_session_id=req.user_session_id,
        runtime_session_id=req.runtime_session_id,
        runtime_id=req.runtime_id,
        runtime_type=req.runtime_type,
        agent_id=req.agent_id,
        session_kind=req.session_kind,
        created_by=req.created_by,
    )
    return session.model_dump()


@app.get("/kernel/user-sessions/{user_session_id}")
async def get_user_session(user_session_id: str):
    engine = get_engine()
    session = await engine.store.get_user_session(user_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="User session not found")
    tasks = await engine.store.list_global_tasks(user_session_id=user_session_id)
    payload = session.model_dump()
    payload["tasks"] = [task.model_dump() for task in tasks]
    return payload


@app.get("/kernel/sessions")
async def list_sessions(status: str = "", limit: int = 20):
    """列出会话，可按状态过滤。"""
    engine = get_engine()
    store = engine.store
    sessions = await store.list_sessions(status=status, limit=limit)
    return sessions


@app.get("/kernel/sessions/{session_id}")
async def get_session(session_id: str):
    """按 ID 获取单个会话。"""
    engine = get_engine()
    session = await engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump()


@app.delete("/kernel/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其所有派生状态。

    同时删除所有关联表（事件、证据、信念等 10 张表）。
    """
    engine = get_engine()
    store = engine.store
    success = await store.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True, "session_id": session_id}


@app.post("/kernel/sessions/{session_id}/archive")
async def archive_session(session_id: str):
    """归档会话（§10.1）——标记为 completed。"""
    engine = get_engine()
    store = engine.store
    success = await store.archive_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"archived": True, "session_id": session_id}


@app.post("/kernel/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    """取消会话——写入 SessionCancelled 事件。"""
    engine = get_engine()
    submission = EventSubmission(
        session_id=session_id,
        component="thinker",
        request_type="SessionCancelled",
    )
    ok, reason, event = await engine.submit_event(submission)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    return {"status": "cancelled", "event_id": event.event_id if event else ""}


@app.post("/kernel/sessions/{session_id}/rebuild")
async def rebuild_session(session_id: str):
    """从事件日志重建全部派生状态。

    断点恢复机制：清空派生表 → 从 append-only 事件流重放 Reducer。
    完全幂等——重复调用不产生副作用。
    """
    engine = get_engine()
    store = engine.store

    session = await store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    events_raw = await store.get_events(session_id, limit=10000)
    await store.clear_derived_state(session_id)

    from src.kms.pipeline import reduce, register_runtime_references, summarize
    from src.schema.events import Actor, CognitiveEvent, EventType, RuntimeRef, Visibility

    processed = set()
    reduced = set()
    for ev_dict in events_raw:
        payload = ev_dict.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        runtime_refs = ev_dict.get("runtime_refs", {})
        if isinstance(runtime_refs, str):
            runtime_refs = json.loads(runtime_refs or "{}")

        event = CognitiveEvent(
            event_id=ev_dict["event_id"],
            kernel_session_id=session_id,
            runtime_session_id=ev_dict.get("runtime_session_id", "") or "",
            run_id=ev_dict.get("run_id", "") or "",
            event_type=EventType(ev_dict["event_type"]),
            actor=Actor(ev_dict["actor"]),
            source_component=ev_dict.get("source_component", ""),
            payload=payload,
            runtime_refs=RuntimeRef(**runtime_refs),
            visibility=Visibility(ev_dict.get("visibility", "shared")),
            intent_version=ev_dict.get("intent_version", 0) or 0,
            state_version=ev_dict.get("state_version", 0) or 0,
        )
        if event.event_id in processed:
            continue  # 幂等：跳过已处理事件
        processed.add(event.event_id)
        await register_runtime_references(store, session_id, event)
        await reduce(store, session_id, event, _processed=reduced)

    # 重放后重新合成进度
    await summarize(store, session_id)

    return {
        "rebuilt": True,
        "events_replayed": len(reduced),
        "total_events": len(events_raw),
    }


# ===========================================================================
# 事件提交 — 核心入口
# ===========================================================================

@app.post("/kms/request")
async def submit_event(req: SubmitEventRequest):
    """提交事件到 KMS 流水线。

    这是 Thinker 和 Talker 的唯一写入入口。
    Thinker 发送结构化 JSON（如 EvidenceCandidateFound）。
    Talker 发送 request_type="raw" + text 字段（自然语言）。

    流水线：Intake → Normalize → Validate → Classify
           → Arbitrate → EventLog → Reduce → return。
    """
    engine = get_engine()
    submission = EventSubmission(
        session_id=req.session_id,
        component=req.component,
        request_type=req.request_type,
        payload=req.payload,
        intent_version=req.intent_version,
        run_id=req.run_id,
        runtime_refs=req.runtime_refs,
    )
    ok, reason, event = await engine.submit_event(submission)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    return {
        "accepted": True,
        "event_id": event.event_id if event else "",
        "event_type": event.event_type.value if event else "",
        "state_version": event.state_version if event else 0,
    }


@app.post("/kms/dispatch-user-message")
async def dispatch_user_message(req: DispatchUserMessageRequest):
    """用户消息统一调度入口。"""
    manager = get_kms_manager()
    decision = await manager.dispatch_user_message(
        text=req.text,
        runtime_session_id=req.runtime_session_id,
        runtime_id=req.runtime_id,
        runtime_type=req.runtime_type,
        agent_id=req.agent_id,
        external_source=req.external_source,
        external_workspace_id=req.external_workspace_id,
        external_issue_id=req.external_issue_id,
        external_task_id=req.external_task_id,
        target_session_id=req.target_session_id,
        user_session_id=req.user_session_id,
        mode=req.mode,
    )
    return {
        "action": decision.action,
        "kernel_session_id": decision.kernel_session_id,
        "intent_version": decision.intent_version,
        "run_id": decision.run_id,
        "session_status": decision.session_status,
        "reason": decision.reason,
        "task_action": decision.task_action,
        "task_id": decision.task_id,
        "requires_thinker": decision.requires_thinker,
        "kernel_response": decision.kernel_response,
        "resume_context": decision.resume_context,
        "user_session_id": decision.user_session_id,
        "route_decision": decision.route_decision,
        "thinker_dispatch_id": decision.thinker_dispatch_id,
    }


@app.post("/kms/complete-run")
async def complete_run(req: CompleteRunRequest):
    engine = get_engine()
    ok = await engine.complete_run(
        req.session_id,
        req.run_id,
        session_status=req.session_status,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Run is not active")
    return {"ok": True}


@app.get("/kms/thinker/dispatches")
async def list_thinker_dispatches(
    kernel_session_id: str = "",
    task_id: str = "",
    status: str = "",
    limit: int = 50,
):
    engine = get_engine()
    dispatches = await engine.store.list_thinker_dispatches(
        kernel_session_id=kernel_session_id,
        task_id=task_id,
        status=status,
        limit=limit,
    )
    return [dispatch.model_dump() for dispatch in dispatches]


@app.post("/kms/thinker/dispatches/claim")
async def claim_thinker_dispatch(req: ClaimThinkerDispatchRequest):
    engine = get_engine()
    dispatch = await engine.store.claim_thinker_dispatch(
        dispatch_id=req.dispatch_id,
        thinker_id=req.thinker_id,
        kernel_session_id=req.kernel_session_id,
        task_id=req.task_id,
    )
    return {"dispatch": dispatch.model_dump() if dispatch else None}


@app.post("/kms/thinker/dispatches/{dispatch_id}/heartbeat")
async def heartbeat_thinker_dispatch(dispatch_id: str):
    engine = get_engine()
    dispatch = await engine.store.heartbeat_thinker_dispatch(dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="Thinker dispatch not found")
    return dispatch.model_dump()


@app.post("/kms/thinker/dispatches/{dispatch_id}/complete")
async def complete_thinker_dispatch(dispatch_id: str, req: CompleteThinkerDispatchRequest):
    engine = get_engine()
    dispatch = await engine.store.get_thinker_dispatch(dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="Thinker dispatch not found")
    completed_run = False
    if dispatch.run_id:
        completed_run = await engine.complete_run(
            dispatch.kernel_session_id,
            dispatch.run_id,
            session_status=req.session_status,
        )
    updated = await engine.store.complete_thinker_dispatch(dispatch_id)
    if completed_run:
        await _create_dispatch_notification(
            engine,
            dispatch,
            notification_type="task_done" if req.session_status == "completed" else "progress_update",
            urgency="normal",
            reason="thinker_dispatch_completed",
        )
    return updated.model_dump()


@app.post("/kms/thinker/dispatches/{dispatch_id}/fail")
async def fail_thinker_dispatch(dispatch_id: str, req: FailThinkerDispatchRequest):
    engine = get_engine()
    dispatch = await engine.store.get_thinker_dispatch(dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="Thinker dispatch not found")
    completed_run = False
    if dispatch.run_id:
        completed_run = await engine.complete_run(
            dispatch.kernel_session_id,
            dispatch.run_id,
            session_status=req.session_status,
        )
    updated = await engine.store.fail_thinker_dispatch(dispatch_id, error=req.error)
    if completed_run:
        await _create_dispatch_notification(
            engine,
            dispatch,
            notification_type="task_failed",
            urgency="important",
            reason=req.error or "thinker_dispatch_failed",
        )
    return updated.model_dump()


async def _list_notifications(
    *,
    target: str,
    kernel_session_id: str = "",
    task_id: str = "",
    status: str = "pending",
    limit: int = 50,
):
    engine = get_engine()
    notifications = await engine.store.list_observer_notifications(
        target=target,
        kernel_session_id=kernel_session_id,
        task_id=task_id,
        status=status,
        limit=limit,
    )
    return [notification.model_dump() for notification in notifications]


async def _ack_notification(notification_id: str):
    engine = get_engine()
    notification = await engine.store.ack_observer_notification(notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification.model_dump()


async def _resolve_notification(notification_id: str):
    engine = get_engine()
    notification = await engine.store.resolve_observer_notification(notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification.model_dump()


@app.get("/kms/observer/notifications")
async def list_observer_notifications(
    kernel_session_id: str = "",
    task_id: str = "",
    status: str = "pending",
    limit: int = 50,
):
    return await _list_notifications(
        target="observer",
        kernel_session_id=kernel_session_id,
        task_id=task_id,
        status=status,
        limit=limit,
    )


@app.post("/kms/observer/notifications/{notification_id}/ack")
async def ack_observer_notification(notification_id: str):
    return await _ack_notification(notification_id)


@app.post("/kms/observer/notifications/{notification_id}/resolve")
async def resolve_observer_notification(notification_id: str):
    return await _resolve_notification(notification_id)


@app.get("/kms/talker/notifications")
async def list_talker_notifications(
    kernel_session_id: str = "",
    task_id: str = "",
    status: str = "pending",
    limit: int = 50,
):
    return await _list_notifications(
        target="talker",
        kernel_session_id=kernel_session_id,
        task_id=task_id,
        status=status,
        limit=limit,
    )


@app.post("/kms/talker/notifications/{notification_id}/ack")
async def ack_talker_notification(notification_id: str):
    return await _ack_notification(notification_id)


@app.post("/kms/talker/notifications/{notification_id}/resolve")
async def resolve_talker_notification(notification_id: str):
    return await _resolve_notification(notification_id)


@app.get("/kernel/sessions/{session_id}/events")
async def get_events(session_id: str, after: str = "", limit: int = 100):
    """获取会话的事件日志——append-only 审计跟踪。"""
    engine = get_engine()
    store = engine.store
    events = await store.get_events(session_id, after_event_id=after, limit=limit)
    return events


# ===========================================================================
# 视图端点 — 四层视图
# ===========================================================================

@app.get("/kms/sessions/{session_id}/views/talker")
async def get_talker_view(session_id: str):
    """Talker 视图——Talker 可安全读取和表达的内容。

    懒生成：查询时运行 Summarize 阶段生成 DeepSeek 摘要。
    """
    engine = get_engine()
    view = await engine.get_talker_view(session_id)
    if not view:
        raise HTTPException(status_code=404, detail="No progress state")
    return view.model_dump()


@app.get("/kms/sessions/{session_id}/views/observer")
async def get_observer_view(session_id: str):
    engine = get_engine()
    view = await engine.get_observer_view(session_id)
    if not view:
        raise HTTPException(status_code=404, detail="No observer view")
    return view


@app.get("/kms/sessions/{session_id}/views/manager")
async def get_manager_view(session_id: str):
    engine = get_engine()
    view = await engine.get_manager_view(session_id)
    if not view:
        raise HTTPException(status_code=404, detail="No manager view")
    return view


@app.get("/kms/sessions/{session_id}/views/thinker")
async def get_thinker_view(session_id: str):
    """Thinker 视图——全量派生状态：意图、计划、证据、信念、执行、承诺。"""
    engine = get_engine()
    return await engine.get_thinker_view(session_id)


@app.get("/kms/sessions/{session_id}/views/sync")
async def get_sync_view(session_id: str):
    """Sync 视图——供 Multica 等外部系统使用的最小摘要。"""
    engine = get_engine()
    view = await engine.get_sync_view(session_id)
    if not view:
        raise HTTPException(status_code=404, detail="No sync view")
    return view.model_dump()


@app.get("/kms/sessions/{session_id}/views/debug")
async def get_debug_view(session_id: str):
    """Debug 视图——用于调试/审计的完整内部状态（事件+派生状态）。"""
    engine = get_engine()
    return await engine.get_debug_view(session_id)


# ===========================================================================
# 可见性闸门
# ===========================================================================

@app.post("/kms/ask-can-say")
async def ask_can_say(req: AskCanSayRequest):
    """检查 Talker 能否对用户说某句话。

    双层：规则关键词 + DeepSeek 语义矛盾检测。
    """
    engine = get_engine()
    result = await engine.ask_can_say(req.session_id, req.proposed_message)
    return result


# ===========================================================================
# 自动演示 — 完整流水线自动运行
# ===========================================================================

class DemoRequest(BaseModel):
    """自动演示请求。"""
    question: str


@app.post("/demo/run")
async def demo_run(req: DemoRequest):
    """运行完整自动流水线：Talker 输入 → Thinker → 结果。

    创建会话、搜索网络、提交证据、形成信念，
    完整运行 9 阶段 KMS 流水线。返回 Talker 视图。
    """
    engine = get_engine()
    from src.kernel.demo_runner import run_demo
    try:
        result = await run_demo(engine, req.question)
        return result
    except Exception as e:
        logger.exception("Demo run failed")
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# 仪表盘
# ===========================================================================

@app.get("/dashboard")
async def dashboard():
    """交互式 Kernel 仪表盘——单页 HTML 应用。"""
    dashboard_path = Path(__file__).parent.parent / "dashboard.html"
    if not dashboard_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(str(dashboard_path), media_type="text/html")


# ===========================================================================
# 健康检查
# ===========================================================================

@app.get("/")
async def root():
    """服务信息和可用端点。"""
    return {
        "service": "Agent State Kernel",
        "version": "0.2.0",
        "docs": "/docs",
        "dashboard": "/dashboard",
    }


@app.get("/health")
async def health():
    """健康检查——负载均衡器探测用。"""
    return {"status": "ok"}
