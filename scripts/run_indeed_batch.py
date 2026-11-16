#!/usr/bin/env python3
"""
Local Mac Indeed Discovery + SmartApply Batch Runner (Syncs Confirmed Applies to Cloud VM DB).
"""

import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.job_hunter import score_job_heuristic
from src.services.exclusion_filter import FORBIDDEN_COMPANIES
from src.services.indeed_filler import apply_to_indeed_job, scrape_fresh_indeed_jobs

DB_PATH = os.environ.get("DATABASE_PATH") or os.path.expanduser("~/.linkedin_agent.db")


def _discover_fresh_indeed_into_db(conn: sqlite3.Connection) -> int:
    scraped = scrape_fresh_indeed_jobs()
    saved = 0
    cur = conn.cursor()
    for j in scraped:
        comp_l = (j.get("company") or "").lower()
        title_l = (j.get("title") or "").lower()
        if any(fc in f"{comp_l} {title_l}" for fc in FORBIDDEN_COMPANIES if len(fc) > 4):
            continue
        score, reasons = score_job_heuristic(j["title"], j["company"])
        if score < 70:
            continue
        exists = cur.execute(
            "SELECT 1 FROM jobs WHERE id = ? OR url = ? OR job_id = ?",
            (j["dedup_id"], j["url"], j["job_id"]),
        ).fetchone()
        if exists:
            continue
        sc_json = json.dumps({"score": score, "reasons": reasons, "platform": "Indeed"})
        cur.execute(
            """
            INSERT INTO jobs (id, job_id, title, company, location, posted_at, url, match_score, scorecard, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                j["dedup_id"],
                j["job_id"],
                j["title"],
                j["company"],
                j["location"],
                j["posted_time"],
                j["url"],
                score,
                sc_json,
            ),
        )
        saved += 1
    conn.commit()
    print(f"[IndeedBatch] Scanned {len(scraped)} fresh Indeed Easily-Apply listings -> saved {saved} high-fit roles.")
    return saved


def _sync_indeed_updates_to_cloud(conn: sqlite3.Connection) -> None:
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if sys.platform != "darwin" or not os.path.exists(ssh_key):
        return
    try:
        rows = conn.execute(
            """
            SELECT id, job_id, title, company, COALESCE(location, 'India'), posted_at, url,
                   COALESCE(match_score, 85), COALESCE(scorecard, '{}'), status, created_at
            FROM jobs
            WHERE status = 'applied'
              AND (url LIKE '%indeed.com%' OR COALESCE(scorecard, '') LIKE '%indeed%')
            ORDER BY created_at DESC
            LIMIT 60
            """
        ).fetchall()
        if not rows:
            return
        b64 = base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
        remote_py = (
            f"import base64,json,sqlite3; rows=json.loads(base64.b64decode('{b64}').decode()); "
            "c=sqlite3.connect('/home/opc/.linkedin_agent.db',timeout=20.0); "
            "c.executemany('''INSERT INTO jobs (id,job_id,title,company,location,posted_at,url,match_score,scorecard,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, url=excluded.url, scorecard=excluded.scorecard''', rows); "
            "c.commit(); c.close()"
        )
        subprocess.run(
            [
                "ssh",
                "-i",
                ssh_key,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                "opc@130.210.108.55",
                "/home/opc/career-agent/venv/bin/python -",
            ],
            input=remote_py,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except Exception as se:
        print(f"[IndeedBatch] Cloud DB sync notice: {se}")


def run_indeed_batch(limit: int = 12) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    pending_fresh = cur.execute(
        "SELECT count(*) FROM jobs WHERE job_id LIKE 'indeed_%' AND status = 'new'"
    ).fetchone()[0]
    if sys.platform == "darwin" and pending_fresh < 15:
        try:
            _discover_fresh_indeed_into_db(conn)
        except Exception as de:
            print(f"[IndeedBatch] Discovery notice: {de}")

    jobs = cur.execute(
        """
        SELECT id, title, company, url
        FROM jobs
        WHERE url LIKE '%indeed.com%' AND status = 'new'
        ORDER BY (CASE WHEN job_id LIKE 'indeed_%' THEN 0 ELSE 1 END), created_at DESC, match_score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    print(f"[IndeedBatch] Fetched {len(jobs)} pending Indeed jobs to process...")

    applied_count = 0
    for idx, (jid, title, company, url) in enumerate(jobs, 1):
        print(f"\n[{idx}/{len(jobs)}] Indeed Applying: {title} @ {company} ({url})")
        res = apply_to_indeed_job(url, f"{title} @ {company}")
        st = res.get("status")

        if st in ("applied", "external_applied"):
            applied_count += 1
            via_tag = "indeed_external_ats_verified" if st == "external_applied" else "indeed_smartapply"
            cur.execute(
                """
                UPDATE jobs
                SET status = 'applied',
                    scorecard = json_set(COALESCE(scorecard, '{}'), '$.applied_via', ?, '$.applied_at', CURRENT_TIMESTAMP)
                WHERE id = ?
                """,
                (via_tag, jid),
            )
            conn.commit()
            print(f"[IndeedBatch] -> CONFIRMED APPLIED: {title} @ {company}!")
            try:
                from src.agents.discord_notifier import DiscordNotifier
                DiscordNotifier().send_application_alert(
                    {
                        "company": company,
                        "role": title,
                        "platform": "Indeed (SmartApply Confirmed)",
                        "url": res.get("external_url", url),
                        "status": "SUBMITTED & CONFIRMED",
                        "details": f"Answered {len(res.get('questions_answered', []))} screening items",
                        "resume": "Vishal_Gupta_Resume.pdf",
                    }
                )
            except Exception:
                pass
        elif st == "already_applied":
            cur.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (jid,))
            conn.commit()
        elif st in ("captcha_gate", "needs_login", "ridealong_unavailable"):
            print(f"[IndeedBatch] Stopping batch cleanly due to '{st}' (leaving remaining jobs untouched).")
            from src.services.system_alerter import send_system_alert
            fix_map = {
                "captcha_gate": "Open Ridealong (`http://127.0.0.1:8083`) -> click the Cloudflare human verification checkbox on `in.indeed.com`.",
                "needs_login": "Open Ridealong (`http://127.0.0.1:8083`) -> log back into `https://in.indeed.com`.",
                "ridealong_unavailable": "Start Ridealong on Mac (`ridealong start`) and verify `http://127.0.0.1:8083` is reachable.",
            }
            send_system_alert(
                platform="Indeed",
                issue_type="logged_out" if st == "needs_login" else st,
                summary=f"Indeed auto-apply paused cleanly due to `{st}` on `{title} @ {company}`.",
                fix_steps=fix_map.get(st, "Check Indeed inside Ridealong (`http://127.0.0.1:8083`)."),
                details=url,
            )
            break
        elif st == "external_redirect":
            cur.execute("UPDATE jobs SET status = 'external_redirect' WHERE id = ?", (jid,))
            conn.commit()
        elif st == "expired":
            cur.execute("UPDATE jobs SET status = 'expired' WHERE id = ?", (jid,))
            conn.commit()
        else:
            cur.execute("UPDATE jobs SET status = 'failed' WHERE id = ?", (jid,))
            conn.commit()
            print(f"[IndeedBatch] -> Skipped/Failed ({st}): {res.get('error')}")

        time.sleep(4)

    if applied_count > 0:
        try:
            from src.services.system_alerter import clear_system_alert
            clear_system_alert("Indeed")
        except Exception:
            pass
    _sync_indeed_updates_to_cloud(conn)
    conn.close()
    print(f"[IndeedBatch] Done! Confirmed Indeed applications: {applied_count}")
    return applied_count


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    run_indeed_batch(lim)
