#!/usr/bin/env python3
"""
Local Mac LinkedIn Ad-Hoc Task Consumer (Cloud VM -> Mac Ridealong Bridge).
Polls Cloud VM SQLite `linkedin_adhoc_tasks` every 6s; whenever a user requests an on-demand
LinkedIn message in Discord #prompt, executes it on Local Mac Ridealong Chrome and posts
the verified result back into Discord #prompt.
"""

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.send_linkedin_person_msg import send_person_outreach
from src.engine.discord_client import DiscordClient
from src import config

SSH_KEY = os.path.expanduser("~/Downloads/agent-vm-key.key")
VM_HOST = "opc@130.210.108.55"
VM_PY = "/home/opc/career-agent/venv/bin/python3"


def claim_next_cloud_task():
    remote_py = (
        "import json, sqlite3; "
        "conn = sqlite3.connect('/home/opc/.linkedin_agent.db', timeout=15.0); "
        "conn.execute('CREATE TABLE IF NOT EXISTS linkedin_adhoc_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT NOT NULL, name_hint TEXT DEFAULT \"\", "
        "company_hint TEXT DEFAULT \"\", role_hint TEXT DEFAULT \"\", channel_id TEXT DEFAULT \"\", "
        "status TEXT DEFAULT \"pending\", result_msg TEXT DEFAULT \"\", "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP)'); "
        "cur = conn.cursor(); "
        "row = cur.execute(\"SELECT id, query, name_hint, company_hint, role_hint, channel_id "
        "FROM linkedin_adhoc_tasks WHERE status='pending' ORDER BY id ASC LIMIT 1\").fetchone(); "
        "if row: cur.execute(\"UPDATE linkedin_adhoc_tasks SET status='running' WHERE id=?\", (row[0],)); conn.commit(); "
        "conn.close(); "
        "print(json.dumps(row) if row else '')"
    )
    # Note: one-line Python cannot mix inline `if row:` after `;` — use clean multi-line script via stdin!
    remote_script = """
import json, sqlite3
conn = sqlite3.connect('/home/opc/.linkedin_agent.db', timeout=15.0)
conn.execute('''CREATE TABLE IF NOT EXISTS linkedin_adhoc_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    name_hint TEXT DEFAULT '',
    company_hint TEXT DEFAULT '',
    role_hint TEXT DEFAULT '',
    channel_id TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    result_msg TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
)''')
cur = conn.cursor()
row = cur.execute(
    "SELECT id, query, name_hint, company_hint, role_hint, channel_id FROM linkedin_adhoc_tasks WHERE status = ? ORDER BY id ASC LIMIT 1",
    ('pending',)
).fetchone()
if row:
    cur.execute("UPDATE linkedin_adhoc_tasks SET status = ? WHERE id = ?", ('running', row[0]))
    conn.commit()
conn.close()
print(json.dumps(row) if row else '')
"""
    proc = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", VM_HOST, f"{VM_PY} -"],
        input=remote_script,
        text=True,
        capture_output=True,
        timeout=12,
    )
    out = (proc.stdout or "").strip()
    if not out:
        return None
    return json.loads(out)


def complete_cloud_task(task_id: int, status: str, result_msg: str) -> None:
    payload_b64 = base64.b64encode(json.dumps({"id": task_id, "status": status, "msg": result_msg}).encode()).decode()
    remote_script = f"""
import base64, json, sqlite3
d = json.loads(base64.b64decode("{payload_b64}").decode())
conn = sqlite3.connect('/home/opc/.linkedin_agent.db', timeout=15.0)
conn.execute(
    "UPDATE linkedin_adhoc_tasks SET status = ?, result_msg = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
    (d['status'], d['msg'], d['id'])
)
conn.commit()
conn.close()
"""
    subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", VM_HOST, f"{VM_PY} -"],
        input=remote_script,
        text=True,
        capture_output=True,
        timeout=12,
    )


def main():
    print("[MacLinkedInBridge] Active — polling Cloud VM `linkedin_adhoc_tasks` every 6s for #prompt LinkedIn requests...")
    discord = DiscordClient()
    default_ch = getattr(config, "DISCORD_PROMPT_CHANNEL_ID", "1570342549435187290")

    while True:
        try:
            row = claim_next_cloud_task()
            if row:
                tid, query, name_h, comp_h, role_h, ch_id = row
                target_ch = ch_id or default_ch
                print(f"[MacLinkedInBridge] Executing task #{tid}: query='{query}' name='{name_h}' company='{comp_h}' role='{role_h}'")
                try:
                    res = send_person_outreach(
                        query=query,
                        name_hint=name_h,
                        company_hint=comp_h,
                        role_hint=role_h,
                    )
                    msg = res.get("message") or json.dumps(res)
                    status = "done" if res.get("ok") else "failed"
                except Exception as ex:
                    msg = f"❌ LinkedIn On-Demand Execution Error for `{query}`: `{ex}`"
                    status = "failed"

                complete_cloud_task(tid, status, msg)
                try:
                    discord.send_message(target_ch, msg)
                except Exception as de:
                    print(f"[MacLinkedInBridge] Discord notify error: {de}")
                print(f"[MacLinkedInBridge] Completed task #{tid} ({status}): {msg}")
        except Exception as e:
            print(f"[MacLinkedInBridge] Poll note: {e}")

        time.sleep(6)


if __name__ == "__main__":
    main()
