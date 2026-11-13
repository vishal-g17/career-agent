#!/usr/bin/env python3
"""
Priority #0 Immediate Task Executor for Discord #prompt (ALL Tasks — Not Just LinkedIn!).
Whenever Vishal commands something in #prompt, this executes it IMMEDIATELY on Priority #0
(bypassing background queues) and returns the REAL VERIFIED COMPLETION RESULT synchronously!

Subcommands:
  1) linkedin-msg : Immediate custom or pitch LinkedIn message (preempts Mac Ridealong, waits synchronously for receipt)
  2) email        : Immediate SMTP dispatch right now (custom text or tailored cold pitch + CV, bypasses 2,200-lead queue)
  3) apply        : Immediate job/form application right now (ATS/Form inline on Cloud VM, or LinkedIn Easy Apply on Mac)
  4) mac-sh       : Immediate shell execution on Local Mac via 1s HTTPS Priority Bridge
"""

import argparse
import json
import os
import smtplib
import sqlite3
import sys
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")


def ensure_priority_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS priority_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            channel_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            result_msg TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """
    )
    conn.commit()


def dispatch_and_wait_mac(task_type: str, payload: Dict[str, Any], channel_id: str = "", timeout_s: float = 42.0) -> str:
    """Enqueues a Priority #0 Mac task and waits synchronously until Mac completes it (~3-15s)."""
    if sys.platform == "darwin":
        return execute_mac_task_locally(task_type, payload)

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    ensure_priority_table(conn)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO priority_tasks (task_type, payload_json, channel_id, status) VALUES (?, ?, ?, ?)",
        (task_type, json.dumps(payload), channel_id, "pending"),
    )
    tid = cur.lastrowid
    conn.commit()
    conn.close()

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.35)
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        row = conn.execute("SELECT status, result_msg FROM priority_tasks WHERE id = ?", (tid,)).fetchone()
        conn.close()
        if row and row[0] in ("done", "failed"):
            return row[1] or f"Task #{tid} finished ({row[0]})."

    return (
        f"⏳ Priority task #{tid} is dispatched and running on Local Mac (`{task_type}`) — "
        "completion receipt will post here as soon as Ridealong finishes."
    )


def execute_mac_task_locally(task_type: str, payload: Dict[str, Any]) -> str:
    if task_type == "linkedin_msg":
        from scripts.send_linkedin_person_msg import send_person_outreach
        res = send_person_outreach(
            query=payload.get("query", ""),
            name_hint=payload.get("name", ""),
            company_hint=payload.get("company", ""),
            role_hint=payload.get("role", ""),
            custom_message=payload.get("custom_message", ""),
            attach_cv=payload.get("attach_cv"),
        )
        return res.get("message") or json.dumps(res)
    elif task_type == "linkedin_apply":
        import subprocess
        url = payload.get("url", "")
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "linkedin_single_task.py"), "apply", url],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return (proc.stdout or proc.stderr or "Completed.").strip()[:1800]
    elif task_type == "mac_sh":
        import subprocess
        proc = subprocess.run(
            payload.get("cmd", ""),
            shell=True,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[:1800] or "Exit 0"
    return f"Unknown task_type: {task_type}"


def run_priority_email(args) -> str:
    from src.services.cold_mailer import compose_cold_email, send_cold_email_smtp, get_sender_credentials
    from src.services.exclusion_filter import FORBIDDEN_COMPANIES

    recip = args.to.strip()
    if not args.body and any(fc in f"{recip} {args.company}".lower() for fc in FORBIDDEN_COMPANIES):
        return f"🛑 Blocked: `{recip}` matches restricted company exclusion rule."

    if args.body:
        # Custom immediate email!
        sender, app_pwd = get_sender_credentials(args.sender or None)
        subj = args.subject or "Quick note from Vishal Gupta"
        msg = MIMEMultipart()
        msg["From"] = f"Vishal Gupta <{sender}>"
        msg["To"] = recip
        msg["Subject"] = subj
        msg.attach(MIMEText(args.body, "plain"))
        if not args.no_cv:
            pdf_p = PROJECT_ROOT / "data" / "resume.pdf"
            if pdf_p.exists():
                part = MIMEApplication(pdf_p.read_bytes(), Name="Vishal_Gupta_Resume.pdf")
                part["Content-Disposition"] = 'attachment; filename="Vishal_Gupta_Resume.pdf"'
                msg.attach(part)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as srv:
            srv.ehlo()
            srv.starttls()
            srv.ehlo()
            srv.login(sender, app_pwd)
            srv.sendmail(sender, [recip], msg.as_string())
        return (
            f"✅ **Priority Email Sent Immediately!**\n"
            f"• **To:** `{recip}` (from `{sender}`)\n"
            f"• **Subject:** `{subj}`\n"
            f"• **CV Attached:** `{'No' if args.no_cv else 'Yes'}`"
        )
    else:
        draft = compose_cold_email(
            recipient_email=recip,
            role_title=args.role or "APM / Product & Business Analyst",
            company_name=args.company or "",
            preferred_sender=args.sender or None,
        )
        subj = args.subject or draft["subject"]
        ok = send_cold_email_smtp(recip, subj, draft["body"], sender_email=draft["sender"])
        if not ok:
            return f"❌ Immediate email to `{recip}` could not be sent (failed MX/SMTP check)."

        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        conn.execute(
            """
            INSERT INTO outreach_leads (name, email, company, role_target, status, first_sent_at, last_sent_at, sender_email)
            VALUES (?, ?, ?, ?, 'sent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(email) DO UPDATE SET
                status='sent',
                last_sent_at=CURRENT_TIMESTAMP,
                sender_email=excluded.sender_email
            """,
            (args.name or "Hiring Team", recip.lower(), args.company or "", args.role or "Product / Analytics", draft["sender"]),
        )
        conn.commit()
        conn.close()
        return (
            f"✅ **Priority Cold Email + CV Sent Immediately!**\n"
            f"• **To:** `{recip}` (`{args.company or 'Direct'}`)\n"
            f"• **Sender:** `{draft['sender']}`\n"
            f"• **Subject:** `{subj}`"
        )


def run_priority_apply(args) -> str:
    url = args.url.strip()
    if "linkedin.com/jobs" in url.lower():
        return dispatch_and_wait_mac("linkedin_apply", {"url": url}, channel_id=args.channel, timeout_s=95.0)

    from src.services.ats_filler import fill_ats_application
    res = fill_ats_application(url, role_title=args.role or "Product Analyst", auto_submit=True)
    if res.get("submitted") and res.get("confirmed"):
        return f"✅ **Priority Application Submitted & Confirmed Immediately!**\n• **URL:** {url}"
    return f"⚠️ **Priority Application Attempted (`{url}`):** `{json.dumps(res)}`"


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    li = sub.add_parser("linkedin-msg")
    li.add_argument("--query", required=True)
    li.add_argument("--name", default="")
    li.add_argument("--company", default="")
    li.add_argument("--role", default="")
    li.add_argument("--custom-message", default="")
    li.add_argument("--attach-cv", action="store_true")
    li.add_argument("--channel", default="1570342549435187290")

    em = sub.add_parser("email")
    em.add_argument("--to", required=True)
    em.add_argument("--name", default="")
    em.add_argument("--company", default="")
    em.add_argument("--role", default="")
    em.add_argument("--subject", default="")
    em.add_argument("--body", default="")
    em.add_argument("--sender", default="")
    em.add_argument("--no-cv", action="store_true")

    ap = sub.add_parser("apply")
    ap.add_argument("--url", required=True)
    ap.add_argument("--role", default="Product Analyst")
    ap.add_argument("--channel", default="1570342549435187290")

    sh = sub.add_parser("mac-sh")
    sh.add_argument("--cmd", required=True)
    sh.add_argument("--channel", default="1570342549435187290")

    args = parser.parse_args()
    if args.cmd == "linkedin-msg":
        out = dispatch_and_wait_mac(
            "linkedin_msg",
            {
                "query": args.query,
                "name": args.name,
                "company": args.company,
                "role": args.role,
                "custom_message": args.custom_message,
                "attach_cv": args.attach_cv,
            },
            channel_id=args.channel,
            timeout_s=42.0,
        )
        print(out)
    elif args.cmd == "email":
        print(run_priority_email(args))
    elif args.cmd == "apply":
        print(run_priority_apply(args))
    elif args.cmd == "mac-sh":
        print(dispatch_and_wait_mac("mac_sh", {"cmd": args.cmd}, channel_id=args.channel, timeout_s=45.0))


if __name__ == "__main__":
    main()
