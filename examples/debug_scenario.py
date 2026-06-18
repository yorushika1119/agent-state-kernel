"""Minimal debug scenario."""
import asyncio, httpx

KERNEL = "http://127.0.0.1:8420"

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        # Create session
        r = await c.post(f"{KERNEL}/kernel/sessions", json={})
        sid = r.json()["kernel_session_id"]
        print(f"Session: {sid}")

        # Submit evidence directly
        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "EvidenceCandidateFound",
            "payload": {"evidence_id": "ev_a", "evidence_type": "web_page",
                        "source": "a", "title": "A", "extracted_facts": ["a1"],
                        "reliability": "high"}
        })
        print(f"Evidence A: {r.json()}")
        v = await c.get(f"{KERNEL}/kms/sessions/{sid}/views/thinker")
        ev = v.json().get("evidence", [])
        print(f"  Thinker view evidence: {len(ev)} items")

        # Now submit intent + plan
        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "IntentUpdated",
            "payload": {"goal": "test"}
        })
        print(f"Intent: {r.json()}")

        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "PlanProposed",
            "payload": {"plan_id": "p1", "plan": {"steps": [{"step_id": "s1", "name": "t1"}]}}
        })
        print(f"Plan: {r.json()}")

        # Check evidence again
        v = await c.get(f"{KERNEL}/kms/sessions/{sid}/views/thinker")
        ev = v.json().get("evidence", [])
        print(f"  After intent+plan, evidence: {len(ev)} items")

        # Submit another evidence
        r = await c.post(f"{KERNEL}/kms/request", json={
            "session_id": sid, "component": "thinker",
            "request_type": "EvidenceCandidateFound",
            "payload": {"evidence_id": "ev_b", "evidence_type": "web_page",
                        "source": "b", "title": "B", "extracted_facts": ["b1"],
                        "reliability": "medium"}
        })
        print(f"Evidence B: {r.json()}")
        v = await c.get(f"{KERNEL}/kms/sessions/{sid}/views/thinker")
        ev = v.json().get("evidence", [])
        print(f"  After evidence B, evidence: {len(ev)} items")

asyncio.run(main())
