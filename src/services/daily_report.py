"""
Daily Full-Day Career Autopilot Summary Generator (IST Timezone: UTC+05:30).

Used for:
  1) Automatic 11:00 PM IST End-of-Day summary posted to Discord (#prompt & #jobs) every night by 24/7 Cloud VM.
  2) Instant on-demand `today` / `daily update` / `full day update` command in Discord #prompt.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def _resolve_db_path() -> str:
    for cand in (
        os.environ.get("DATABASE_PATH"),
        str(Path.home() / ".linkedin_agent.db"),
        str(PROJECT_ROOT / ".linkedin_agent.db"),
        str(PROJECT_ROOT / "agent.db"),
    ):
        if cand and os.path.exists(cand):
            return cand
    return str(Path.home() / ".linkedin_agent.db")


def classify_job_platform(jid: str, url: str, scorecard_raw: str) -> str:
    u = (url or "").lower()
    s = (scorecard_raw or "").lower()
    if "instahyre.com" in u or str(jid).startswith("instahyre_"):
        return "Instahyre"
    if "linkedin.com" in u or "linkedin_easy_apply" in s:
        return "LinkedIn"
    if "naukri.com" in u or "naukri" in s:
        return "Naukri"
    if "indeed.com" in u:
        return "Indeed"
    if "greenhouse.io" in u or "gh_jid" in u:
        return "Greenhouse ATS"
    if "lever.co" in u:
        return "Lever ATS"
    if "ashbyhq.com" in u:
        return "Ashby ATS"
    if "forms.gle" in u or "docs.google.com/forms" in u:
        return "Google Forms"
    return "Direct Company ATS"


def collect_daily_metrics(target_date_ist: Optional[str] = None) -> Dict[str, Any]:
    now_ist = datetime.now(IST)
    day_str = target_date_ist or now_ist.strftime("%Y-%m-%d")
    db_path = _resolve_db_path()

    conn = sqlite3.connect(db_path, timeout=20.0)
    cur = conn.cursor()

    # 1. Jobs Applied (Today vs All-Time by Platform)
    job_rows = cur.execute(
        """
        SELECT id, title, company, url, COALESCE(scorecard, ''),
               date(created_at, '+5 hours', '30 minutes') AS ist_date
        FROM jobs
        WHERE status = 'applied'
        ORDER BY created_at DESC
        """
    ).fetchall()

    today_by_plat: Counter = Counter()
    total_by_plat: Counter = Counter()
    today_companies = []

    for jid, title, comp, url, sc, ist_d in job_rows:
        plat = classify_job_platform(jid, url, sc)
        total_by_plat[plat] += 1
        if ist_d == day_str:
            today_by_plat[plat] += 1
            if comp and comp not in today_companies and len(today_companies) < 8:
                today_companies.append(f"{comp} ({plat})")

    # 2. Cold Emails (Today vs All-Time)
    fresh_by_sender = dict(
        cur.execute(
            """
            SELECT COALESCE(sender_email, 'default'), count(*)
            FROM outreach_leads
            WHERE date(first_sent_at, '+5 hours', '30 minutes') = ?
            GROUP BY COALESCE(sender_email, 'default')
            """,
            (day_str,),
        ).fetchall()
    )
    fresh_today_total = sum(fresh_by_sender.values())
    nsut_today = sum(v for k, v in fresh_by_sender.items() if "nsut" in k.lower())
    gmail_today = fresh_today_total - nsut_today

    fu1_today = cur.execute(
        "SELECT count(*) FROM outreach_leads WHERE date(followup_1_at, '+5 hours', '30 minutes') = ?",
        (day_str,),
    ).fetchone()[0]
    fu2_today = cur.execute(
        "SELECT count(*) FROM outreach_leads WHERE date(followup_2_at, '+5 hours', '30 minutes') = ?",
        (day_str,),
    ).fetchone()[0]

    total_active_emails = cur.execute(
        "SELECT count(*) FROM outreach_leads WHERE status IN ('sent', 'replied', 'unresponsive')"
    ).fetchone()[0]
    pending_emails = cur.execute("SELECT count(*) FROM outreach_leads WHERE status = 'pending'").fetchone()[0]
    total_replies = cur.execute("SELECT count(*) FROM outreach_leads WHERE status = 'replied'").fetchone()[0]

    replies_today = 0
    try:
        replies_today = cur.execute(
            "SELECT count(*) FROM outreach_leads WHERE status = 'replied' AND date(reply_received_at, '+5 hours', '30 minutes') = ?",
            (day_str,),
        ).fetchone()[0]
    except Exception:
        replies_today = 0

    bounces_today = 0
    bounces_total = 0
    try:
        bounces_today = cur.execute(
            "SELECT count(*) FROM bounced_blocklist WHERE date(detected_at, '+5 hours', '30 minutes') = ?",
            (day_str,),
        ).fetchone()[0]
        bounces_total = cur.execute("SELECT count(*) FROM bounced_blocklist").fetchone()[0]
    except Exception:
        pass

    # 3. LinkedIn 1st-Degree DMs & Invites
    li_today = 0
    li_total = 0
    try:
        li_today = cur.execute(
            "SELECT count(*) FROM linkedin_connections_outreach WHERE status='sent' AND date(sent_at, '+5 hours', '30 minutes') = ?",
            (day_str,),
        ).fetchone()[0]
        li_total = cur.execute("SELECT count(*) FROM linkedin_connections_outreach WHERE status='sent'").fetchone()[0]
    except Exception:
        pass

    invites_today = 0
    try:
        invites_today = cur.execute(
            "SELECT count(*) FROM poc_connection_requests WHERE date(requested_at, '+5 hours', '30 minutes') = ?",
            (day_str,),
        ).fetchone()[0]
    except Exception:
        pass

    conn.close()

    ats_today = (
        today_by_plat["Greenhouse ATS"]
        + today_by_plat["Lever ATS"]
        + today_by_plat["Ashby ATS"]
        + today_by_plat["Google Forms"]
        + today_by_plat["Direct Company ATS"]
    )
    ats_total = (
        total_by_plat["Greenhouse ATS"]
        + total_by_plat["Lever ATS"]
        + total_by_plat["Ashby ATS"]
        + total_by_plat["Google Forms"]
        + total_by_plat["Direct Company ATS"]
    )

    return {
        "date_ist": day_str,
        "generated_at_ist": now_ist.strftime("%d %b %Y, %I:%M %p IST"),
        "jobs_today_total": sum(today_by_plat.values()),
        "jobs_all_time_total": sum(total_by_plat.values()),
        "today_by_plat": {
            "Instahyre": today_by_plat["Instahyre"],
            "Direct ATS (Greenhouse/Lever/Ashby)": ats_today,
            "LinkedIn Jobs": today_by_plat["LinkedIn"],
            "Naukri": today_by_plat["Naukri"],
            "Indeed": today_by_plat["Indeed"],
        },
        "total_by_plat": {
            "Instahyre": total_by_plat["Instahyre"],
            "Direct ATS (Greenhouse/Lever/Ashby)": ats_total,
            "LinkedIn Jobs": total_by_plat["LinkedIn"],
            "Naukri": total_by_plat["Naukri"],
            "Indeed": total_by_plat["Indeed"],
        },
        "today_companies": today_companies,
        "emails": {
            "sent_today_total": fresh_today_total + fu1_today + fu2_today,
            "fresh_today": fresh_today_total,
            "nsut_today": nsut_today,
            "gmail_today": gmail_today,
            "fu1_today": fu1_today,
            "fu2_today": fu2_today,
            "active_delivered_all_time": total_active_emails,
            "pending": pending_emails,
            "replies_today": replies_today,
            "replies_all_time": total_replies,
            "bounces_today": bounces_today,
            "bounces_all_time": bounces_total,
        },
        "linkedin": {
            "dms_today": li_today,
            "dms_all_time": li_total,
            "invites_today": invites_today,
        },
    }


def format_daily_report_markdown(base_cloud_url: str = "", header_prefix: str = "📅 **Full-Day Career Autopilot Update**") -> str:
    m = collect_daily_metrics()
    tp = m["today_by_plat"]
    ap = m["total_by_plat"]
    em = m["emails"]
    li = m["linkedin"]

    lines = [
        f"{header_prefix} (`{m['date_ist']}` — `{m['generated_at_ist']}`)\n",
        f"### 🎯 1. Jobs Applied Today: **{m['jobs_today_total']}** (`{m['jobs_all_time_total']}` All-Time Confirmed)",
        f"• **Instahyre (24/7 Cloud REST):** `{tp['Instahyre']}` today (`{ap['Instahyre']}` total)",
        f"• **Direct Company ATS (Greenhouse/Lever/Ashby):** `{tp['Direct ATS (Greenhouse/Lever/Ashby)']}` today (`{ap['Direct ATS (Greenhouse/Lever/Ashby)']}` total)",
        f"• **LinkedIn Easy Apply:** `{tp['LinkedIn Jobs']}` today (`{ap['LinkedIn Jobs']}` total)",
        f"• **Naukri:** `{tp['Naukri']}` today (`{ap['Naukri']}` total — Cloud IP gated by Akamai)",
        f"• **Indeed:** `{tp['Indeed']}` today (`{ap['Indeed']}` total — Cloudflare CAPTCHA gated)",
    ]
    if m["today_companies"]:
        lines.append(f"• **Recent Companies Applied Today:** {', '.join(m['today_companies'][:6])}")

    lines.extend(
        [
            f"\n### ✉️ 2. Cold Emails Today: **{em['sent_today_total']} Sent** (`{em['active_delivered_all_time']}` Active Delivered Total)",
            f"• **Fresh Pitches + CV Sent Today:** `{em['fresh_today']}` (`{em['nsut_today']}` via NSUT • `{em['gmail_today']}` via Gmail)",
            f"• **Follow-Ups Sent Today:** `{em['fu1_today'] + em['fu2_today']}` (`FU1: {em['fu1_today']}`, `FU2: {em['fu2_today']}`)",
            f"• **Inbound Recruiter Replies:** `{em['replies_all_time']}` total tracked (`+{em['replies_today']}` timestamped today)",
            f"• **Bounces Auto-Purged (6s Wiper):** `{em['bounces_today']}` wiped today (`{em['bounces_all_time']}` blocked all-time) | **Pending Queue:** `{em['pending']}`",
            f"\n### 🤝 3. LinkedIn Outreach Today: **{li['dms_today']} DMs + CV** (`{li['dms_all_time']}` All-Time)",
            f"• **1st-Degree Referrals / HR DMs Sent Today:** `{li['dms_today']}` (`{li['dms_all_time']}` total)",
            f"• **New Connection Invites Sent Today:** `{li['invites_today']}`",
        ]
    )

    if base_cloud_url:
        b = base_cloud_url.rstrip("/")
        lines.append(
            f"\n### 🔗 Live 24/7 Cloud Trackers\n"
            f"• 📋 **Live Sheet:** {b}/sheet\n"
            f"• 📥 **Clean Excel:** {b}/download/MASTER_LEADS_SHEET.xlsx\n"
            f"• 📊 **Dashboard:** {b}/p/dashboard"
        )

    return "\n".join(lines)
