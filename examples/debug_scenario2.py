"""Debug scenario 2 — test with ToolStarted/Completed before evidence."""
import asyncio, httpx

KERNEL = "http://127.0.0.1:8420"

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{KERNEL}/kernel/sessions", json={})
        sid = r.json()["kernel_session_id"]
        print(f"Session: {sid}")

        # Intent
        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "IntentUpdated",
            "payload": {"goal": "test", "constraints": ["no-send"]}
        })

        # Plan
        await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "PlanProposed",
            "payload": {"plan_id": "p1", "plan": {"steps": [
                {"step_id": "s1", "name": "search"},
                {"step_id": "s2", "name": "verify"},
            ]}}
        })

        # ToolStarted + ToolCompleted
        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "ToolStarted",
            "payload": {"action_id": "act1", "step_id": "s1",
                        "tool": "web_search", "input_summary": "search X"}
        })
        print(f"ToolStarted: {r.status_code}")
        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "ToolCompleted",
            "payload": {"action_id": "act1", "step_id": "s1",
                        "input_summary": "found results", "output_ref": "ref1"}
        })
        print(f"ToolCompleted: {r.status_code}")

        # Evidence AFTER tool events
        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "EvidenceCandidateFound",
            "payload": {"evidence_id": "ev_x", "evidence_type": "web_page",
                        "source": "x", "title": "X", "extracted_facts": ["x1"],
                        "reliability": "high"}
        })
        print(f"Evidence X: {r.status_code}")
        v = await c.get(f"{KERNEL}/kms/sessions/{sid}/views/thinker")
        vd = v.json()
        print(f"Evidence: {len(vd.get('evidence', []))} items")
        print(f"Beliefs: {len(vd.get('beliefs', []))} items")
        print(f"Executions: {len(vd.get('executions', []))} items")

asyncio.run(main())
