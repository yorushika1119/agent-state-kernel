"""Incremental debug — check evidence after each submission."""
import asyncio, httpx

KERNEL = "http://127.0.0.1:8420"

async def check(c, sid, label):
    v = await c.get(f"{KERNEL}/kms/sessions/{sid}/views/thinker")
    vd = v.json()
    print(f"  [{label}] ev={len(vd.get('evidence',[]))} be={len(vd.get('beliefs',[]))} ex={len(vd.get('executions',[]))}")

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{KERNEL}/kernel/sessions", json={
            "agent_id": "hermes-thinker", "external_task_id": "research-001"
        })
        sid = r.json()["kernel_session_id"]
        print(f"Session: {sid}")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "IntentUpdated",
            "payload": {"goal": "研究A公司", "constraints": ["不能发送"], "output_format": "email_draft"}
        })
        await check(c, sid, "intent")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "PlanProposed",
            "payload": {"plan_id": "plan_001", "plan": {"steps": [
                {"step_id": "s1", "name": "search", "owner": "executor"},
                {"step_id": "s2", "name": "verify", "owner": "verifier"},
                {"step_id": "s3", "name": "draft", "owner": "executor"},
            ]}}
        })
        await check(c, sid, "plan")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "ToolStarted",
            "payload": {"action_id": "act_001", "step_id": "s1", "tool": "web_search", "input_summary": "search"}
        })
        await check(c, sid, "tool-start")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "ToolCompleted",
            "payload": {"action_id": "act_001", "step_id": "s1", "input_summary": "found", "output_ref": "r1"}
        })
        await check(c, sid, "tool-done")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "EvidenceCandidateFound",
            "payload": {"evidence_id": "ev_001", "evidence_type": "web_page",
                        "source": "https://t.co", "title": "A raises B",
                        "extracted_facts": ["fact1", "fact2"], "reliability": "medium"}
        })
        await check(c, sid, "ev1")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "EvidenceCandidateFound",
            "payload": {"evidence_id": "ev_002", "evidence_type": "web_page",
                        "source": "https://36kr.com", "title": "A round",
                        "extracted_facts": ["fact3", "fact4"], "reliability": "medium"}
        })
        await check(c, sid, "ev2")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "TaskCompleted",
            "payload": {"step_id": "s1"}
        })
        await check(c, sid, "s1-done")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "StepStarted",
            "payload": {"step_id": "s2"}
        })
        await check(c, sid, "s2-start")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "BeliefProposed",
            "payload": {"belief_id": "b_001", "claim": "A got funded",
                        "status": "likely", "confidence": 0.85,
                        "supporting_evidence": ["ev_001", "ev_002"]}
        })
        await check(c, sid, "b1")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "BeliefProposed",
            "payload": {"belief_id": "b_002", "claim": "amount is $30M",
                        "status": "conflicting", "confidence": 0.45,
                        "supporting_evidence": ["ev_001"],
                        "conflicting_evidence": ["ev_002"]}
        })
        await check(c, sid, "b2")

        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "TaskCompleted",
            "payload": {"step_id": "s2"}
        })
        await check(c, sid, "s2-done")

        print("\nFINAL:")
        v = await c.get(f"{KERNEL}/kms/sessions/{sid}/views/thinker")
        vd = v.json()
        print(f"  Evidence: {[e['evidence_id'] for e in vd.get('evidence',[])]}")
        print(f"  Beliefs: {[b['belief_id'] for b in vd.get('beliefs',[])]}")
        print(f"  Executions: {len(vd.get('executions',[]))}")

asyncio.run(main())
