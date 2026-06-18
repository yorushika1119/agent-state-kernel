"""Validation scenario — multi-step research task through Kernel.

Simulates: "研究 A 公司融资情况并生成邮件草稿"
Steps: search → extract → verify → generate

This is the same scenario from the architecture document.
"""
import asyncio
import httpx

KERNEL_URL = "http://127.0.0.1:8420"


async def scenario():
    async with httpx.AsyncClient(timeout=30) as client:
        # ---- Phase 1: Start session ----
        print("=" * 60)
        print("PHASE 1: Create session")
        r = await client.post(
            f"{KERNEL_URL}/kernel/sessions",
            json={"agent_id": "hermes-thinker", "external_task_id": "research-001"},
        )
        session = r.json()
        sid = session["kernel_session_id"]
        print(f"  Session: {sid}")
        print(f"  Status: {session['status']}")
        print()

        # ---- Phase 2: Set intent ----
        print("PHASE 2: Set intent (user goal)")
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "IntentUpdated",
                "payload": {
                    "goal": "研究 A 公司融资情况并生成邮件草稿",
                    "constraints": ["不能直接发送", "金额不确定时要标注"],
                    "output_format": "email_draft",
                },
            },
        )
        print(f"  Intent set: {r.json()}")
        print()

        # ---- Phase 3: Propose plan ----
        print("PHASE 3: Propose plan")
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "PlanProposed",
                "payload": {
                    "plan_id": "plan_001",
                    "plan": {
                        "steps": [
                            {"step_id": "s1", "name": "搜索 A 公司公开融资信息", "owner": "executor"},
                            {"step_id": "s2", "name": "交叉验证融资金额和投资方", "owner": "verifier"},
                            {"step_id": "s3", "name": "生成邮件草稿", "owner": "executor"},
                        ]
                    },
                },
            },
        )
        print(f"  Plan accepted: {r.json()}")
        print()

        # ---- Phase 4: Step 1 — search ----
        print("PHASE 4: Execute Step 1 — Web search")
        # Tool started
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "ToolStarted",
                "payload": {
                    "action_id": "act_001",
                    "step_id": "s1",
                    "tool": "web_search",
                    "input_summary": "search: A company Series B funding 2026",
                },
            },
        )
        print(f"  Tool started: {r.json()}")

        # Tool completed → submit evidence
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "ToolCompleted",
                "payload": {
                    "action_id": "act_001",
                    "step_id": "s1",
                    "input_summary": "找到 3 条相关结果",
                    "output_ref": "tool_result_001",
                },
            },
        )
        print(f"  Tool completed: {r.json()}")

        # Evidence from search results
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "EvidenceCandidateFound",
                "payload": {
                    "evidence_id": "ev_001",
                    "evidence_type": "web_page",
                    "source": "https://techcrunch.com/example",
                    "title": "A Company raises Series B",
                    "extracted_facts": [
                        "A 公司完成 B 轮融资",
                        "投资方包括 X Capital",
                        "融资金额约 3000 万美元",
                    ],
                    "reliability": "medium",
                },
            },
        )
        print(f"  Evidence ev_001: {r.json()}")

        # Another source with conflicting info
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "EvidenceCandidateFound",
                "payload": {
                    "evidence_id": "ev_002",
                    "evidence_type": "web_page",
                    "source": "https://36kr.com/example",
                    "title": "A 公司完成新一轮融资",
                    "extracted_facts": [
                        "A 公司完成新一轮融资",
                        "投资方包括 Y Ventures",
                        "融资金额约 5000 万美元",
                    ],
                    "reliability": "medium",
                },
            },
        )
        print(f"  Evidence ev_002: {r.json()}")

        # Mark step 1 complete
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "TaskCompleted",
                "payload": {"step_id": "s1"},
            },
        )
        print(f"  Step s1 completed: {r.json()}")
        print()

        # ---- Phase 5: Step 2 — Verify ----
        print("PHASE 5: Execute Step 2 — Cross-verify")
        # Start verification step
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "StepStarted",
                "payload": {"step_id": "s2"},
            },
        )
        print(f"  Step s2 started: {r.json()}")

        # Propose belief based on evidence
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "BeliefProposed",
                "payload": {
                    "belief_id": "b_001",
                    "claim": "A 公司最近完成了一轮融资",
                    "status": "likely",
                    "confidence": 0.85,
                    "supporting_evidence": ["ev_001", "ev_002"],
                },
            },
        )
        print(f"  Belief b_001 (likely): {r.json()}")

        # Conflict detected on amount
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "BeliefProposed",
                "payload": {
                    "belief_id": "b_002",
                    "claim": "融资金额为 3000 万美元",
                    "status": "conflicting",
                    "confidence": 0.45,
                    "supporting_evidence": ["ev_001"],
                    "conflicting_evidence": ["ev_002"],
                },
            },
        )
        print(f"  Belief b_002 (conflicting): {r.json()}")

        # Verify step 2 done — now s3 starts
        r = await client.post(
            f"{KERNEL_URL}/kms/request",
            json={
                "session_id": sid,
                "component": "thinker",
                "request_type": "TaskCompleted",
                "payload": {"step_id": "s2"},
            },
        )
        print(f"  Step s2 completed: {r.json()}")
        print()

        # ---- Phase 6: Check views ----
        print("=" * 60)
        print("PHASE 6: Check derived views")

        # Thinker view
        r = await client.get(f"{KERNEL_URL}/kms/sessions/{sid}/views/thinker")
        thinker = r.json()
        print("\n--- THINKER VIEW ---")
        print(f"  Intent: {thinker['intent']['goal']}")
        print(f"  Plan steps: {[(s['step_id'], s['name'], s['status']) for s in thinker['plan']['steps']]}")
        print(f"  Evidence: {len(thinker['evidence'])} items")
        for ev in thinker["evidence"]:
            print(f"    {ev['evidence_id']}: {ev['title']} (可靠性: {ev['reliability']})")
        print(f"  Beliefs: {len(thinker['beliefs'])} items")
        for b in thinker["beliefs"]:
            print(f"    {b['belief_id']}: \"{b['claim']}\" — {b['status']} (置信度: {b['confidence']})")
        print(f"  Executions: {len(thinker['executions'])} actions")

        # Progress (Talker) view
        r = await client.get(f"{KERNEL_URL}/kms/sessions/{sid}/views/talker")
        progress = r.json()
        print("\n--- TALKER VIEW (User-facing Progress) ---")
        print(f"  Status: {progress['status']}")
        print(f"  Stage: {progress['stage']}")
        print(f"  Summary: {progress['summary']}")
        print(f"  Safe facts: {progress['safe_facts']}")
        print(f"  Unsafe claims: {progress['unsafe_claims']}")
        print(f"  Allowed actions: {progress['allowed_actions']}")
        print(f"  Forbidden actions: {progress['forbidden_actions']}")

        # Sync view
        r = await client.get(f"{KERNEL_URL}/kms/sessions/{sid}/views/sync")
        sync = r.json()
        print("\n--- SYNC VIEW (External) ---")
        print(f"  Status: {sync['status']}")
        print(f"  Stage: {sync['stage']}")
        print(f"  Summary: {sync['summary']}")

        # Event log
        r = await client.get(f"{KERNEL_URL}/kernel/sessions/{sid}/events")
        events = r.json()
        print(f"\n--- EVENT LOG: {len(events)} events ---")
        for ev in events:
            print(f"  [{ev['state_version']}] {ev['event_type']} (by {ev['actor']})")

        # ---- Phase 7: ASK_CAN_SAY tests ----
        print("\n" + "=" * 60)
        print("PHASE 7: Visibility Gate tests")

        # Test 1: Safe statement
        r = await client.post(
            f"{KERNEL_URL}/kms/ask-can-say",
            json={
                "session_id": sid,
                "proposed_message": "已找到 A 公司相关融资信息，正在验证中",
            },
        )
        result = r.json()
        print(f"\n  '已找到 A 公司相关融资信息，正在验证中'")
        print(f"    Allowed: {result['allowed']}")

        # Test 2: Premature completion claim
        r = await client.post(
            f"{KERNEL_URL}/kms/ask-can-say",
            json={
                "session_id": sid,
                "proposed_message": "研究已完成，邮件草稿已生成",
            },
        )
        result = r.json()
        print(f"\n  '研究已完成，邮件草稿已生成'")
        print(f"    Allowed: {result['allowed']}")
        print(f"    Reason: {result.get('reason', 'N/A')}")
        print(f"    Safe alternative: {result.get('safe_alternative', 'N/A')}")

        # Test 3: Unsafe claim
        r = await client.post(
            f"{KERNEL_URL}/kms/ask-can-say",
            json={
                "session_id": sid,
                "proposed_message": "融资金额为 3000 万美元",
            },
        )
        result = r.json()
        print(f"\n  '融资金额为 3000 万美元'")
        print(f"    Allowed: {result['allowed']}")
        print(f"    Reason: {result.get('reason', 'N/A')}")

        print("\n" + "=" * 60)
        print("VALIDATION COMPLETE")


if __name__ == "__main__":
    asyncio.run(scenario())
