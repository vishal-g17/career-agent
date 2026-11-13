import time
import os
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from .cold_mailer import compose_cold_email, send_cold_email_smtp, get_sender_credentials
from .email_verifier import verify_email
from .exclusion_filter import is_excluded, is_bounced_or_discarded
from ..db import database
from ..agents.discord_notifier import DiscordNotifier
from .. import config

def get_active_senders(sender_mode: str = "rotate") -> List[str]:
    """
    Returns list of active sender email addresses based on configured credentials.
    """
    senders = []
    if getattr(config, "COLLEGE_EMAIL_APP_PASSWORD", ""):
        senders.append(getattr(config, "COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in"))
    if getattr(config, "GMAIL_APP_PASSWORD", ""):
        senders.append(getattr(config, "GMAIL_ADDRESS", "vishal90301@gmail.com"))

    if not senders:
        senders = ["vishal90301@gmail.com"]

    mode = sender_mode.lower().strip()
    if mode in ("nsut", "college") and getattr(config, "COLLEGE_EMAIL_ADDRESS", "") in senders:
        return [getattr(config, "COLLEGE_EMAIL_ADDRESS")]
    elif mode == "gmail" and getattr(config, "GMAIL_ADDRESS", "") in senders:
        return [getattr(config, "GMAIL_ADDRESS")]

    return senders

DAILY_CAP_NSUT = int(os.environ.get("DAILY_CAP_NSUT", "1500"))
DAILY_CAP_GMAIL = int(os.environ.get("DAILY_CAP_GMAIL", "500"))


def get_sender_daily_cap(sender_addr: str) -> int:
    return DAILY_CAP_NSUT if "nsut" in (sender_addr or "").lower() else DAILY_CAP_GMAIL


def get_today_sender_counts(conn) -> Dict[str, int]:
    rows = conn.execute(
        """
        SELECT sender_email, COUNT(*) as cnt
        FROM outreach_leads
        WHERE status = 'sent'
          AND date(COALESCE(last_sent_at, first_sent_at), '+5 hours', '30 minutes') = date('now', '+5 hours', '30 minutes')
        GROUP BY sender_email
        """
    ).fetchall()
    return {(r["sender_email"] or "").strip().lower(): int(r["cnt"]) for r in rows}


def send_outreach_batch(company_filter: Optional[str] = None, batch_size: int = 30, sender_mode: str = "rotate") -> Dict[str, Any]:
    """
    Sends a controlled, highly deliverable 1-on-1 batch of cold emails to pending leads.
    Rotates between NSUT (max 1,500/day) and personal Gmail (max 500/day),
    with randomized sub-20s delays and automatic anti-bounce verification.
    """
    conn = database.get_connection()
    senders = get_active_senders(sender_mode)
    today_counts = get_today_sender_counts(conn)

    available_init = [
        s for s in senders
        if today_counts.get(s.lower(), 0) < get_sender_daily_cap(s)
    ]
    if not available_init:
        print(f"[OutreachCampaign] Daily sender caps reached for today IST ({today_counts}). Waiting until midnight IST.")
        return {"attempted": 0, "sent": 0, "bounced": 0, "daily_cap_reached": True, "results": []}

    query = "SELECT * FROM outreach_leads WHERE status = 'pending'"
    params = []

    if company_filter and company_filter.strip():
        query += " AND LOWER(company) = ?"
        params.append(company_filter.strip().lower())
        query += " ORDER BY id ASC LIMIT ?"
    else:
        # Prioritize verified scraped recruiter pools first
        query += """ ORDER BY CASE 
            WHEN UPPER(company) LIKE '%ZOMATO%' THEN 1 
            WHEN UPPER(company) LIKE '%MEESHO%' THEN 2 
            WHEN UPPER(company) LIKE '%BLINKIT%' OR UPPER(company) LIKE '%GROFERS%' THEN 3 
            WHEN UPPER(company) LIKE '%URBAN COMPANY%' THEN 4 
            WHEN UPPER(company) LIKE '%TATA 1MG%' OR UPPER(company) LIKE '%1MG%' THEN 5 
            WHEN UPPER(company) LIKE '%PAYTM%' THEN 6 
            WHEN UPPER(company) LIKE '%CARS24%' THEN 7 
            ELSE 8 END, id ASC LIMIT ?"""
    params.append(batch_size)

    leads = conn.execute(query, params).fetchall()
    conn.close()

    if not leads:
        print("[OutreachCampaign] No pending leads found matching criteria.")
        return {"attempted": 0, "sent": 0, "bounced": 0, "results": []}

    # Pre-filter exclusions & partition leads across simultaneous active senders (disjoint lanes -> 0 duplicates)
    clean_leads = []
    for lead in leads:
        lead_id = lead["id"]
        name = lead["name"] or "Hiring Team"
        email_addr = lead["email"]
        company = lead["company"] or "Your Team"

        if is_bounced_or_discarded(email_addr):
            print(f"[OutreachCampaign] [DISCARDED_BOUNCE] Permanently skipping {name} ({company}, {email_addr})")
            with database.get_connection() as c:
                c.execute("UPDATE outreach_leads SET status = 'bounced' WHERE id = ?", (lead_id,))
            continue

        exclusion = is_excluded(name=name, headline=f"{company} {email_addr}")
        if exclusion:
            print(f"[OutreachCampaign] [EXCLUDED {exclusion}] Skipping {name} ({company}, {email_addr})")
            with database.get_connection() as c:
                c.execute("UPDATE outreach_leads SET status = 'excluded' WHERE id = ?", (lead_id,))
            continue

        clean_leads.append(dict(lead))

    if not clean_leads:
        return {"attempted": 0, "sent": 0, "bounced": 0, "results": []}

    import threading
    from concurrent.futures import ThreadPoolExecutor

    # Partition leads into parallel buckets per eligible sender while respecting remaining daily caps
    sender_buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in available_init}
    sender_rem: Dict[str, int] = {
        s: max(0, get_sender_daily_cap(s) - today_counts.get(s.lower(), 0))
        for s in available_init
    }

    rr_idx = 0
    for ld in clean_leads:
        open_senders = [s for s in available_init if len(sender_buckets[s]) < sender_rem[s]]
        if not open_senders:
            break
        chosen_s = open_senders[rr_idx % len(open_senders)]
        sender_buckets[chosen_s].append(ld)
        rr_idx += 1

    active_lanes = {s: b for s, b in sender_buckets.items() if b}
    if not active_lanes:
        return {"attempted": 0, "sent": 0, "bounced": 0, "daily_cap_reached": True, "results": []}

    print(
        f"[OutreachCampaign] ⚡ Firing SIMULTANEOUS parallel lanes across {list(active_lanes.keys())} "
        f"({', '.join(f'{s}: {len(b)} leads' for s, b in active_lanes.items())})..."
    )

    db_lock = threading.Lock()
    results: List[Dict[str, Any]] = []
    counters = {"sent": 0, "bounced": 0, "attempted": 0}

    def _run_sender_lane(sender_addr: str, lane_leads: List[Dict[str, Any]]) -> None:
        cap_cur = get_sender_daily_cap(sender_addr)
        for idx, ld in enumerate(lane_leads):
            l_id = ld["id"]
            l_name = ld.get("name") or "Hiring Team"
            l_email = ld["email"]
            l_comp = ld.get("company") or "Your Team"

            mail_data = compose_cold_email(
                recipient_email=l_email,
                role_title="Business / Product Analyst / Management",
                company_name=l_comp,
                sender_email=sender_addr,
            )
            ok = send_cold_email_smtp(
                recipient_email=l_email,
                subject=mail_data["subject"],
                body=mail_data["body"],
                sender_email=sender_addr,
            )

            with db_lock:
                counters["attempted"] += 1
                with database.get_connection() as c:
                    if ok:
                        c.execute(
                            """
                            UPDATE outreach_leads 
                            SET status = 'sent', sender_email = ?, first_sent_at = CURRENT_TIMESTAMP, last_sent_at = CURRENT_TIMESTAMP,
                                last_subject = ?
                            WHERE id = ?
                            """,
                            (sender_addr, mail_data["subject"], l_id),
                        )
                        counters["sent"] += 1
                        today_counts[sender_addr.lower()] = today_counts.get(sender_addr.lower(), 0) + 1
                        status_str = f"sent via {sender_addr} ({today_counts[sender_addr.lower()]}/{cap_cur} today)"
                    else:
                        c.execute("UPDATE outreach_leads SET status = 'bounced' WHERE id = ?", (l_id,))
                        counters["bounced"] += 1
                        status_str = "bounced/blocked"

                results.append({
                    "name": l_name,
                    "email": l_email,
                    "company": l_comp,
                    "sender": sender_addr,
                    "status": status_str,
                })

            if idx < len(lane_leads) - 1:
                jitter = round(random.uniform(4.5, 19.5), 1)
                print(f"[OutreachCampaign:{sender_addr}] Random <20s timer: waiting {jitter}s ({idx+1}/{len(lane_leads)})...")
                time.sleep(jitter)

    with ThreadPoolExecutor(max_workers=len(active_lanes)) as pool:
        futures = [pool.submit(_run_sender_lane, s, b) for s, b in active_lanes.items()]
        for fut in futures:
            fut.result()

    all_capped_after = all(
        today_counts.get(s.lower(), 0) >= get_sender_daily_cap(s) for s in senders
    )
    print(f"[OutreachCampaign] Simultaneous batch complete. Sent: {counters['sent']}, Bounced/Blocked: {counters['bounced']}")
    return {
        "attempted": counters["attempted"],
        "sent": counters["sent"],
        "bounced": counters["bounced"],
        "daily_cap_reached": all_capped_after,
        "results": results,
    }

def process_pending_followups(max_followups: int = 15) -> Dict[str, Any]:
    """
    Scans for leads that have received outreach, haven't replied, and are due for follow-ups.
    Follow-up 1: 3 days after initial send.
    Follow-up 2: 4 days after Follow-up 1.
    Strictly uses the same sender email address that initiated the original thread.
    """
    conn = database.get_connection()
    fu1_sent = 0
    fu2_sent = 0

    # 1. Process Follow-up 1 (Due after 3 days)
    fu1_leads = conn.execute(
        """
        SELECT * FROM outreach_leads 
        WHERE status = 'sent' AND followup_count = 0 
          AND first_sent_at <= datetime('now', '-3 days')
        LIMIT ?
        """,
        (max_followups,)
    ).fetchall()

    for lead in fu1_leads:
        lead_id = lead["id"]
        name = lead["name"] or "Hiring Team"
        email_addr = lead["email"]
        company = lead["company"] or "Your Team"
        sender_email = lead["sender_email"] or getattr(config, "COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in")
        last_subj = lead["last_subject"] or f"Application for APM | Vishal Gupta | Immediate Joinee"

        subject = f"Re: {last_subj}"
        body = f"""Hi {name},

Following up on my previous note. I'm actively exploring APM and Product / Tech Analyst opportunities where I can apply my operational data engineering and growth telemetry background.

I’d love to connect if you have 5 minutes or if there’s a relevant team member you could introduce me to.

Resume: https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing
Portfolio: https://vishalgupta.vercel.app/

Best regards,
Vishal Gupta
📞 +91 8368387245
✉️ {sender_email}
🔗 https://www.linkedin.com/in/vshal-g007"""

        success = send_cold_email_smtp(email_addr, subject, body, sender_email=sender_email)
        if success:
            with database.get_connection() as c:
                c.execute(
                    """
                    UPDATE outreach_leads 
                    SET followup_count = 1, followup_1_at = CURRENT_TIMESTAMP, last_sent_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lead_id,)
                )
            fu1_sent += 1
            time.sleep(random.randint(45, 60))

    # 2. Process Follow-up 2 (Due after 4 days from FU1)
    fu2_leads = conn.execute(
        """
        SELECT * FROM outreach_leads 
        WHERE status = 'sent' AND followup_count = 1 
          AND followup_1_at <= datetime('now', '-4 days')
        LIMIT ?
        """,
        (max_followups,)
    ).fetchall()

    for lead in fu2_leads:
        lead_id = lead["id"]
        name = lead["name"] or "Hiring Team"
        email_addr = lead["email"]
        company = lead["company"] or "Your Team"
        sender_email = lead["sender_email"] or getattr(config, "COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in")
        last_subj = lead["last_subject"] or f"Application for APM | Vishal Gupta | Immediate Joinee"

        subject = f"Re: {last_subj}"
        body = f"""Hi {name},

I know things get busy, so I'll keep this brief. If your team is currently hiring or anticipating hiring for product / analyst roles in the coming quarter, I'd appreciate keeping my profile on file.

Wishing you and the {company} team continued growth!

Best regards,
Vishal Gupta
📞 +91 8368387245
✉️ {sender_email}
🔗 https://www.linkedin.com/in/vshal-g007"""

        success = send_cold_email_smtp(email_addr, subject, body, sender_email=sender_email)
        with database.get_connection() as c:
            c.execute(
                """
                UPDATE outreach_leads 
                SET followup_count = 2, followup_2_at = CURRENT_TIMESTAMP, status = 'unresponsive'
                WHERE id = ?
                """,
                (lead_id,)
            )
        fu2_sent += 1
        time.sleep(random.randint(45, 60))

    return {"followup_1_sent": fu1_sent, "followup_2_sent": fu2_sent}

def get_campaign_stats() -> Dict[str, Any]:
    """
    Returns breakdown of campaign leads by status and sending account.
    """
    conn = database.get_connection()
    c = conn.cursor()
    
    total = c.execute("SELECT count(*) FROM outreach_leads").fetchone()[0]
    pending = c.execute("SELECT count(*) FROM outreach_leads WHERE status = 'pending'").fetchone()[0]
    sent_total = c.execute("SELECT count(*) FROM outreach_leads WHERE status = 'sent'").fetchone()[0]
    replied = c.execute("SELECT count(*) FROM outreach_leads WHERE status = 'replied'").fetchone()[0]
    bounced = c.execute("SELECT count(*) FROM outreach_leads WHERE status = 'bounced'").fetchone()[0]
    fu1 = c.execute("SELECT count(*) FROM outreach_leads WHERE followup_count = 1").fetchone()[0]
    fu2 = c.execute("SELECT count(*) FROM outreach_leads WHERE followup_count = 2").fetchone()[0]
    
    # Sender breakdown
    c.execute("SELECT sender_email, count(*) FROM outreach_leads WHERE status = 'sent' GROUP BY 1")
    sender_counts = dict(c.fetchall())

    return {
        "total_leads": total,
        "pending": pending,
        "sent_total": sent_total,
        "replied": replied,
        "bounced": bounced,
        "followup_1_active": fu1,
        "followup_2_active": fu2,
        "sender_breakdown": sender_counts
    }
