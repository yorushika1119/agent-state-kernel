import httpx, asyncio, json

KERNEL = 'http://127.0.0.1:8420'

async def run_demo():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f'{KERNEL}/kernel/sessions', json={'agent_id':'t','external_task_id':'kms-test-2'})
        sid = r.json()['kernel_session_id']
        print(f"Session: {sid}")

        await c.post(f'{KERNEL}/kms/request', json={
            'session_id':sid,'component':'thinker','request_type':'IntentUpdated',
            'payload':{'goal':'test KMS model judges'}
        })
        await c.post(f'{KERNEL}/kms/request', json={
            'session_id':sid,'component':'thinker','request_type':'PlanProposed',
            'payload':{'plan_id':'p1','plan':{'steps':[{'step_id':'s1','name':'t'}]}}
        })

        # E1: Ars Technica (high quality, moderate claims)
        r = await c.post(f'{KERNEL}/kms/request', json={
            'session_id':sid,'component':'thinker','request_type':'EvidenceCandidateFound',
            'payload':{'evidence_id':'e1','evidence_type':'web_page',
                       'source':'https://arstechnica.com/gadgets/2026/vision-pro-sales-disappoint',
                       'title':'Ars Technica: Vision Pro Sales Disappoint, Production Scaled Back',
                       'extracted_facts':['Apple大幅削减Vision Pro产量','销售远低于预期，2024年仅售出约20万台'],
                       'reliability':'unknown'}
        })
        print(f"E1 (Ars): status={r.status_code}")

        # E2: Sensational blog (low quality, conflicting claims)
        r = await c.post(f'{KERNEL}/kms/request', json={
            'session_id':sid,'component':'thinker','request_type':'EvidenceCandidateFound',
            'payload':{'evidence_id':'e2','evidence_type':'web_page',
                       'source':'https://applegadget.blog/vision-pro-massive-success',
                       'title':'Vision Pro is a MASSIVE HIT! You Wont Believe These Numbers!',
                       'extracted_facts':['Vision Pro是苹果历史上最成功的产品发布','销量远超预期，供不应求'],
                       'reliability':'unknown'}
        })
        print(f"E2 (blog): status={r.status_code}")

        # Wait for async model calls to complete
        await asyncio.sleep(5)

        r = await c.get(f'{KERNEL}/kms/sessions/{sid}/views/thinker')
        t = r.json()
        print(f"\n=== RESULTS ===")
        print(f"Evidence ({len(t['evidence'])}):")
        for ev in t['evidence']:
            print(f"  {ev['evidence_id']}: [{ev['reliability']}] {ev['title'][:60]}")

        r = await c.get(f'{KERNEL}/kernel/sessions/{sid}/events')
        events = r.json()
        cf = [e for e in events if e['event_type'] == 'ConflictDetected']
        print(f"\nConflictDetected: {len(cf)}")
        for c in cf:
            p = c['payload']
            if isinstance(p, str): p = json.loads(p)
            print(f"  by={c.get('source_component','')}: {p.get('conflict_description','')[:120]}")

if __name__ == "__main__":
    asyncio.run(run_demo())
