#!/usr/bin/env python3
"""
1-Task-at-a-Time Verified LinkedIn Automation Runner.
Strictly enforces:
  - Sequential 1-task execution with human-like delays
  - Zero false 'applied' statuses (requires explicit post-submit confirmation screen)
  - Verification screenshot saved on every run
  - Supports local Ridealong CDP OR exported session cookies on Cloud VM
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.easy_apply import apply_to_easy_apply_job, _resolve_cookie_file, RIDEALONG_CDP_ENDPOINT
from src.services.ats_filler import fill_ats_application


def get_db_path() -> str:
    for candidate in [
        os.environ.get("DATABASE_PATH"),
        str(Path.home() / ".linkedin_agent.db"),
        str(PROJECT_ROOT / ".linkedin_agent.db"),
        str(PROJECT_ROOT / "agent.db"),
    ]:
        if candidate and os.path.exists(candidate):
            return candidate
    return str(Path.home() / ".linkedin_agent.db")


def export_cookies_from_ridealong() -> str:
    out_path = PROJECT_ROOT / "data" / "linkedin_cookies.json"
    os.makedirs(out_path.parent, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=5000)
        ctx = browser.contexts[0]
        cookies = ctx.cookies(["https://www.linkedin.com", "https://in.linkedin.com"])
        li_at = any(c.get("name") == "li_at" for c in cookies)
        if not li_at:
            raise RuntimeError("Ridealong browser does not contain an active 'li_at' LinkedIn session cookie.")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        os.chmod(out_path, 0o600)
    return f"Exported {len(cookies)} LinkedIn cookies (including li_at) to {out_path}"


def check_session_and_queue() -> str:
    data_dir = PROJECT_ROOT / "data"
    os.makedirs(data_dir, exist_ok=True)
    shot_path = str(data_dir / "linkedin_session_check.png")
    mode = "none"
    logged_in = False
    current_url = ""

    with sync_playwright() as p:
        browser = None
        page = None
        is_headless = False
        try:
            try:
                browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=3000)
                ctx = browser.contexts[0]
                mode = "Ridealong CDP"
            except Exception:
                cookie_file = _resolve_cookie_file()
                if not os.path.exists(cookie_file):
                    return "❌ LinkedIn Session: Not authenticated (no Ridealong and no data/linkedin_cookies.json found)."
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
                )
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 820},
                )
                with open(cookie_file, "r", encoding="utf-8") as cf:
                    ctx.add_cookies(json.load(cf))
                is_headless = True
                mode = "Headless Cookie Context"

            page = ctx.new_page()
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            current_url = page.url
            logged_in = (
                "feed" in current_url
                and not any(k in current_url.lower() for k in ["login", "authwall", "checkpoint"])
            )
            page.screenshot(path=shot_path)
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            if browser and is_headless:
                try:
                    browser.close()
                except Exception:
                    pass

    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*) FROM jobs WHERE status = 'new' AND url LIKE '%linkedin.com%' AND match_score >= 80"
    )
    high_match_count = cur.fetchone()[0]
    cur.execute(
        """
        SELECT title, company, match_score, url
        FROM jobs
        WHERE status = 'new' AND url LIKE '%linkedin.com%'
        ORDER BY match_score DESC, created_at DESC
        LIMIT 3
        """
    )
    sample_jobs = cur.fetchall()
    conn.close()

    lines = [
        f"🔐 **LinkedIn Session Health ({mode}):** {'🟢 AUTHENTICATED' if logged_in else '🔴 LOGGED OUT / CHECKPOINT'}",
        f"• Landing URL: `{current_url}`",
        f"• Proof Screenshot: `{shot_path}`",
        f"• High-Match LinkedIn Queue (Score ≥ 80): **{high_match_count} pending**",
    ]
    if sample_jobs:
        lines.append("\n**Top 3 Queued Candidates:**")
        for idx, (t, c, s, u) in enumerate(sample_jobs, 1):
            lines.append(f"{idx}. **{t}** @ **{c}** ({s}/100) — <{u}>")
    return "\n".join(lines)


def run_single_job_task(target_url: Optional[str] = None) -> str:
    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()

    job_id = None
    title = "Associate Product Manager"
    company = "Company"

    if target_url:
        cur.execute("SELECT id, title, company FROM jobs WHERE url = ? LIMIT 1", (target_url,))
        row = cur.fetchone()
        if row:
            job_id, title, company = row
        candidates = [(job_id, title, company, target_url)]
    else:
        # Fetch up to 4 top-ranked 'new' LinkedIn jobs so if the first 1-3 are external redirects,
        # we still find and execute 1 genuine Easy Apply or confirmed ATS task cleanly.
        cur.execute(
            """
            SELECT id, title, company, url
            FROM jobs
            WHERE status = 'new' AND url LIKE '%linkedin.com%'
            ORDER BY match_score DESC, created_at DESC
            LIMIT 4
            """
        )
        candidates = cur.fetchall()
        if not candidates:
            conn.close()
            return "ℹ️ No pending `'new'` LinkedIn jobs left in the queue."

    report_lines = []
    for jid, jtitle, jcomp, jurl in candidates:
        role_label = f"{jtitle} @ {jcomp}"
        res = apply_to_easy_apply_job(jurl, role_title=role_label, auto_submit=True)

        if res.get("already_applied"):
            if jid:
                cur.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (jid,))
                conn.commit()
            report_lines.append(f"ℹ️ **{role_label}** was already marked 'Applied' on LinkedIn (<{jurl}>).")
            if target_url:
                break
            continue

        if res.get("is_easy_apply"):
            if res.get("submitted") and res.get("confirmed"):
                if jid:
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status = 'applied',
                            scorecard = json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'linkedin_easy_apply', '$.applied_at', CURRENT_TIMESTAMP)
                        WHERE id = ?
                        """,
                        (jid,),
                    )
                    conn.commit()
                conn.close()
                return (
                    f"✅ **1-Task LinkedIn Easy Apply — SUBMITTED & CONFIRMED**\n"
                    f"• Role: **{jtitle}** @ **{jcomp}**\n"
                    f"• Questions Answered: **{len(res.get('questions_handled', []))}**\n"
                    f"• Proof Screenshot: `{res.get('screenshot_path')}`\n"
                    f"• URL: <{jurl}>"
                )
            else:
                if jid:
                    cur.execute("UPDATE jobs SET status = 'reviewed' WHERE id = ?", (jid,))
                    conn.commit()
                conn.close()
                return (
                    f"⚠️ **1-Task LinkedIn Easy Apply — NOT CONFIRMED (Honest Hold)**\n"
                    f"• Role: **{jtitle}** @ **{jcomp}**\n"
                    f"• Reason: `{res.get('error') or 'Custom validation field required manual review'}`\n"
                    f"• Status in DB: `'reviewed'` (NOT falsely marked applied)\n"
                    f"• Preview Screenshot: `{res.get('screenshot_path')}`\n"
                    f"• URL: <{jurl}>"
                )
        else:
            # Not Easy Apply -> Try external ATS once if explicit URL, otherwise queue as external_redirect
            if target_url:
                ats_res = fill_ats_application(jurl, role_title=role_label, auto_submit=True)
                if ats_res.get("submitted") and ats_res.get("confirmed"):
                    if jid:
                        cur.execute(
                            """
                            UPDATE jobs
                            SET status = 'applied',
                                scorecard = json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'ats_autofill', '$.ats_type', ?, '$.applied_at', CURRENT_TIMESTAMP)
                            WHERE id = ?
                            """,
                            (ats_res.get("ats_type", "external_ats"), jid),
                        )
                        conn.commit()
                    conn.close()
                    return (
                        f"✅ **External ATS Application — SUBMITTED & CONFIRMED**\n"
                        f"• Role: **{jtitle}** @ **{jcomp}**\n"
                        f"• ATS Platform: `{ats_res.get('ats_type')}`\n"
                        f"• Proof Screenshot: `{ats_res.get('screenshot_path')}`\n"
                        f"• Destination: <{ats_res.get('ats_url', jurl)}>"
                    )
                else:
                    if jid:
                        cur.execute("UPDATE jobs SET status = 'external_pending_review' WHERE id = ?", (jid,))
                        conn.commit()
                    conn.close()
                    return (
                        f"🔗 **External Company Portal — NOT AUTO-CONFIRMED (Honest Hold)**\n"
                        f"• Role: **{jtitle}** @ **{jcomp}**\n"
                        f"• Reason: `{ats_res.get('error') or 'Requires manual portal login/verification'}`\n"
                        f"• Status in DB: `'external_pending_review'` (NOT falsely marked applied)\n"
                        f"• Destination: <{ats_res.get('ats_url', jurl)}>"
                    )
            else:
                if jid:
                    cur.execute("UPDATE jobs SET status = 'external_redirect' WHERE id = ?", (jid,))
                    conn.commit()
                report_lines.append(f"↪️ Skipped external redirect **{role_label}** (moved to `external_redirect` queue).")

    conn.close()
    return "\n".join(report_lines) + "\nℹ️ Run `!linkedin_run next` again to pull the next LinkedIn candidate."


def main():
    args = sys.argv[1:]
    action = args[0].lower() if args else "check"
    if action == "export-cookies":
        print(export_cookies_from_ridealong())
    elif action == "check":
        print(check_session_and_queue())
    elif action == "next":
        print(run_single_job_task(target_url=None))
    elif action == "apply":
        if len(args) < 2:
            print("Usage: python3 scripts/linkedin_single_task.py apply <job_url_or_next>")
            sys.exit(1)
        url_arg = args[1].strip("<>")
        if url_arg.lower() == "next":
            print(run_single_job_task(target_url=None))
        else:
            print(run_single_job_task(target_url=url_arg))
    else:
        print(f"Unknown action '{action}'. Supported: check | next | apply <url> | export-cookies")


if __name__ == "__main__":
    main()
