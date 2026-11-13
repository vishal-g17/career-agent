import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.sync_leads_to_sheet import DB_PATH, export_split_leads

SSH_KEY = "/Users/admin/Downloads/agent-vm-key.key"
VM_HOST = "opc@130.210.108.55"

VM_FETCH_SCRIPT = """
import json, sqlite3
conn = sqlite3.connect('/home/opc/.linkedin_agent.db', timeout=30.0)
conn.row_factory = sqlite3.Row
conn.execute('CREATE TABLE IF NOT EXISTS bounced_blocklist (email TEXT PRIMARY KEY, reason TEXT, detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
blocked = [dict(r) for r in conn.execute('SELECT email, reason FROM bounced_blocklist').fetchall()]
updated = [dict(r) for r in conn.execute("SELECT email, status, sender_email, first_sent_at, last_sent_at, reply_snippet, reply_received_at FROM outreach_leads WHERE status != 'pending'").fetchall()]
conn.close()
print(json.dumps({'blocked': blocked, 'updated': updated}))
"""


def sync_once() -> dict:
    raw = subprocess.check_output(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", VM_HOST, "/home/opc/career-agent/venv/bin/python3 -"],
        input=VM_FETCH_SCRIPT,
        text=True,
        timeout=25,
    )
    data = json.loads(raw.strip())
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bounced_blocklist (
            email TEXT PRIMARY KEY,
            reason TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    deleted_local = 0
    for b in data.get("blocked", []):
        em = (b.get("email") or "").strip().lower()
        if not em:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO bounced_blocklist (email, reason, detected_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (em, b.get("reason") or "Cloud VM Bounce Purge"),
        )
        cur = conn.execute("DELETE FROM outreach_leads WHERE LOWER(TRIM(email)) = ?", (em,))
        deleted_local += cur.rowcount

    status_synced = 0
    for u in data.get("updated", []):
        em = (u.get("email") or "").strip().lower()
        if not em:
            continue
        cur = conn.execute(
            """
            UPDATE outreach_leads
            SET status = ?,
                sender_email = COALESCE(?, sender_email),
                first_sent_at = COALESCE(?, first_sent_at),
                last_sent_at = COALESCE(?, last_sent_at),
                reply_snippet = COALESCE(?, reply_snippet),
                reply_received_at = COALESCE(?, reply_received_at)
            WHERE LOWER(TRIM(email)) = ? AND status != ?
            """,
            (
                u.get("status"),
                u.get("sender_email"),
                u.get("first_sent_at"),
                u.get("last_sent_at"),
                u.get("reply_snippet"),
                u.get("reply_received_at"),
                em,
                u.get("status"),
            ),
        )
        status_synced += cur.rowcount

    conn.commit()
    conn.close()

    if deleted_local > 0 or status_synced > 0:
        export_split_leads()

    return {"deleted_local": deleted_local, "status_synced": status_synced, "vm_blocked_total": len(data.get("blocked", []))}


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        print("[LocalSync] Starting continuous 45s Cloud VM -> Local DB/Sheet bounce & status sync...")
        res = sync_once()
        export_split_leads()
        print(f"[LocalSync:Init] {res}")
        while True:
            try:
                time.sleep(45)
                r = sync_once()
                if r["deleted_local"] > 0 or r["status_synced"] > 0:
                    print(f"[LocalSync] Purged {r['deleted_local']} bounced lead(s), synced {r['status_synced']} status update(s) -> sheets refreshed.")
            except Exception as e:
                print(f"[LocalSync:Warn] {e}")
                time.sleep(30)
    else:
        res = sync_once()
        export_split_leads()
        print(f"[LocalSync] {res}")
