"""
Cloud-to-Mac On-Demand LinkedIn Task Queue Bridge.
Allows the 24/7 Cloud VM Discord #prompt listener to enqueue live LinkedIn browser tasks
(e.g., 'Send linkedin msg to Shivani Gupta working at CARS24 as Associate') into SQLite on the VM,
which the Local Mac Ridealong bridge executes within seconds and reports back to Discord #prompt.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")


def ensure_adhoc_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS linkedin_adhoc_tasks (
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
        )
        """
    )
    conn.commit()


def enqueue_task(query: str, name: str = "", company: str = "", role: str = "", channel_id: str = "") -> int:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    ensure_adhoc_table(conn)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO linkedin_adhoc_tasks (query, name_hint, company_hint, role_hint, channel_id, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (query.strip(), name.strip(), company.strip(), role.strip(), channel_id.strip()),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(task_id)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    enq = sub.add_parser("enqueue")
    enq.add_argument("--query", required=True)
    enq.add_argument("--name", default="")
    enq.add_argument("--company", default="")
    enq.add_argument("--role", default="")
    enq.add_argument("--channel", default="1570342549435187290")

    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()
    if args.cmd == "enqueue":
        tid = enqueue_task(args.query, args.name, args.company, args.role, args.channel)
        print(
            json.dumps(
                {
                    "ok": True,
                    "task_id": tid,
                    "status": "queued_for_mac_ridealong",
                    "message": (
                        f"Queued LinkedIn task #{tid} (`{args.query}`). "
                        "Local Mac Ridealong bridge will execute it within ~6–25s and post the verified receipt directly in Discord."
                    ),
                }
            )
        )
    elif args.cmd == "list":
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        ensure_adhoc_table(conn)
        rows = conn.execute(
            "SELECT id, query, status, result_msg, created_at, completed_at FROM linkedin_adhoc_tasks ORDER BY id DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        conn.close()
        for r in rows:
            print(r)


if __name__ == "__main__":
    main()
