import sys, json, httpx

if __name__ == "__main__":
    sys.path.insert(0, r'C:\program1\agent-state-kernel\src')
    sys.path.insert(0, r'C:\Users\EDY\AppData\Local\hermes\plugins')
    from kernel_adapter import _get_adapter, _on_post_tool_call

    # Simulate a web_search tool call
    _on_post_tool_call(
        tool_name="web_search",
        args={"query": "test kernel adapter integration"},
        result=json.dumps({
            "success": True,
            "data": {
                "web": [
                    {"url": "https://example.com/test", "title": "Test Result", "description": "A test description"}
                ]
            }
        }),
        session_id="test-session-001",
        tool_call_id="test-call-001",
        duration_ms=150,
        status="success",
    )

    adapter = _get_adapter()
    sid = adapter.ensure_session()
    print(f"Kernel session: {sid}")

    r = httpx.get(f"http://127.0.0.1:8420/kms/sessions/{sid}/views/thinker", timeout=10)
    data = r.json()
    print(f"Evidence: {len(data.get('evidence', []))} items")
    for ev in data.get('evidence', []):
        print(f"  [{ev['reliability']}] {ev['title']}")
        for f in ev.get('extracted_facts', []):
            print(f"    o {f}")
    print(f"Executions: {len(data.get('executions', []))} actions")
    for x in data.get('executions', []):
        print(f"  {x['tool']}: [{x['status']}] {x['input_summary'][:80]}")
