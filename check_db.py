import sqlite3, json, sys
db = sqlite3.connect('/c/program1/agent-state-kernel/data/kernel.db')
for table in ['evidence_items', 'belief_items', 'execution_actions']:
    rows = db.execute(f'SELECT * FROM {table}').fetchall()
    print(f'{table}: {len(rows)} rows')
    for r in rows:
        state = json.loads(r[2])
        print(f'  id={r[0]} session={r[1]}: {json.dumps(state, ensure_ascii=False)[:120]}')
db.close()
