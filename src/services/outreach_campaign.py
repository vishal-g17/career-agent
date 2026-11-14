import time
import os
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from .cold_mailer import compose_cold_email, send_cold_email_smtp, get_sender_credentials
from .email_verifier import verify_email
from .exclusion_filter import is_excluded, is_bounced_or_discarded, record_bounced_email
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
            print(f"[OutreachCampaign] [DISCARDED_BOUNCE] Deleting bounced lead {name} ({company}, {email_addr})")
            record_bounced_email(email_addr, "Pre-known bounce")
            continue

        exclusion = is_excluded(name=name, headline=f"{company} {email_addr}")
        if exclusion:
            print(f"[OutreachCampaign] [EXCLUDED {exclusion}] Deleting excluded lead {name} ({company}, {email_addr})")
            record_bounced_email(email_addr, f"Excluded: {exclusion}")
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
                        c.execute(
                            """
                            CREATE TABLE IF NOT EXISTS bounced_blocklist (
                                email TEXT PRIMARY KEY,
                                reason TEXT,
                                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )
                            """
                        )
                        c.execute(
                            "INSERT OR REPLACE INTO bounced_blocklist (email, reason, detected_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                            (l_email.strip().lower(), "Pre-send MX / SMTP bounce"),
                        )
                        c.execute("DELETE FROM outreach_leads WHERE id = ?", (l_id,))
                        counters["bounced"] += 1
                        status_str = "bounced_and_deleted"

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

def process_pending_followups(max_followups: int = 20) -> Dict[str, Any]:
    """
    Scans for leads that have received outreach, haven't replied, and are due for follow-ups:
      - Follow-up #1: 3 days (72h) after initial send (`first_sent_at <= datetime('now', '-3 days')`)
      - Follow-up #2: 4 days (96h) after Follow-up #1 (`followup_1_at <= datetime('now', '-4 days')`)
    Strictly uses the exact same sender email address that initiated the original thread,
    respects daily caps (1,500 NSUT / 500 Gmail), and skips any bounced/excluded/replied leads.
    """
    conn = database.get_connection()
    today_counts = get_today_sender_counts(conn)

    fu1_rows = conn.execute(
        """
        SELECT * FROM outreach_leads 
        WHERE status = 'sent' AND COALESCE(followup_count, 0) = 0 
          AND first_sent_at IS NOT NULL
          AND first_sent_at <= datetime('now', '-3 days')
        ORDER BY first_sent_at ASC
        LIMIT ?
        """,
        (max_followups,),
    ).fetchall()

    fu2_rows = conn.execute(
        """
        SELECT * FROM outreach_leads 
        WHERE status = 'sent' AND followup_count = 1 
          AND followup_1_at IS NOT NULL
          AND followup_1_at <= datetime('now', '-4 days')
        ORDER BY followup_1_at ASC
        LIMIT ?
        """,
        (max_followups,),
    ).fetchall()
    conn.close()

    tasks = [(1, dict(r)) for r in fu1_rows] + [(2, dict(r)) for r in fu2_rows]
    if not tasks:
        return {"followup_1_sent": 0, "followup_2_sent": 0, "due_remaining": 0}

    fu1_sent = 0
    fu2_sent = 0
    default_sender = getattr(config, "COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in")

    for stage, lead in tasks:
        lead_id = lead["id"]
        name = (lead.get("name") or "Hiring Team").strip()
        email_addr = (lead.get("email") or "").strip()
        company = (lead.get("company") or "Your Team").strip()
        raw_sender = (lead.get("sender_email") or default_sender).split(",")[0].strip()
        sender_email = raw_sender if "@" in raw_sender else default_sender

        if is_bounced_or_discarded(email_addr):
            record_bounced_email(email_addr, "Pre-followup bounce blocklist")
            continue
        excl = is_excluded(name=name, headline=f"{company} {email_addr}")
        if excl:
            record_bounced_email(email_addr, f"Excluded: {excl}")
            continue

        # Enforce daily sender cap (shared with fresh sends)
        cap = get_sender_daily_cap(sender_email)
        if today_counts.get(sender_email.lower(), 0) >= cap:
            continue

        # Double-check live status right before SMTP send in case a reply or bounce arrived moments ago
        with database.get_connection() as chk_c:
            cur_row = chk_c.execute("SELECT status FROM outreach_leads WHERE id = ?", (lead_id,)).fetchone()
            if not cur_row or cur_row["status"] != "sent":
                continue

        last_subj = (lead.get("last_subject") or "").strip() or (
            "Application: Business / Product Analyst / Management — Vishal Gupta (1 yr exp | NSUT Delhi | Immediate Joiner)"
        )
        subject = last_subj if last_subj.lower().startswith("re:") else f"Re: {last_subj}"

        if stage == 1:
            body = (
                f"Hi {name},\n\n"
                "Following up briefly on my previous note in case it got buried. I’m an immediate joiner (15 days) from NSUT Delhi "
                "with 1 year of hands-on experience across product analytics, conversion optimization, merchant growth, and Python/SQL automation.\n\n"
                f"If there are any openings across Product Analyst, Business Analyst, APM, or Founder's Office roles at {company}, "
                "I’d be grateful for a quick chat or pointer to the right hiring manager.\n\n"
                "Resume: https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing\n"
                "Portfolio: https://vishalgupta.vercel.app/\n\n"
                "Best regards,\n"
                "Vishal Gupta\n"
                "📞 +91 8368387245\n"
                f"✉️ {sender_email}\n"
                "🔗 https://www.linkedin.com/in/vshal-g007"
            )
        else:
            body = (
                f"Hi {name},\n\n"
                "Sending one final quick follow-up so I don't crowd your inbox. If your team is hiring or planning to hire for "
                "Product / Business / Data Analyst or APM roles in the coming weeks, I’d love to be considered.\n\n"
                f"Wishing you and the {company} team continued growth!\n\n"
                "Resume: https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing\n\n"
                "Best regards,\n"
                "Vishal Gupta\n"
                "📞 +91 8368387245\n"
                f"✉️ {sender_email}\n"
                "🔗 https://www.linkedin.com/in/vshal-g007"
            )

        ok = send_cold_email_smtp(email_addr, subject, body, sender_email=sender_email)
        if ok:
            today_counts[sender_email.lower()] = today_counts.get(sender_email.lower(), 0) + 1
            with database.get_connection() as c:
                if stage == 1:
                    c.execute(
                        """
                        UPDATE outreach_leads 
                        SET followup_count = 1, followup_1_at = CURRENT_TIMESTAMP, last_sent_at = CURRENT_TIMESTAMP, last_subject = ?
                        WHERE id = ? AND status = 'sent'
                        """,
                        (subject, lead_id),
                    )
                    fu1_sent += 1
                    print(f"[FollowUp #1] ✅ Sent on thread via {sender_email} -> {name} ({company}, {email_addr})")
                else:
                    c.execute(
                        """
                        UPDATE outreach_leads 
                        SET followup_count = 2, followup_2_at = CURRENT_TIMESTAMP, last_sent_at = CURRENT_TIMESTAMP, status = 'unresponsive'
                        WHERE id = ? AND status = 'sent'
                        """,
                        (lead_id,),
                    )
                    fu2_sent += 1
                    print(f"[FollowUp #2] ✅ Final follow-up sent via {sender_email} -> {name} ({company}, {email_addr})")
            time.sleep(round(random.uniform(5.5, 18.5), 1))
        else:
            record_bounced_email(email_addr, "Follow-up SMTP failure / bounce")

    with database.get_connection() as rem_c:
        due_rem = rem_c.execute(
            """
            SELECT COUNT(*) FROM outreach_leads
            WHERE status = 'sent'
              AND (
                  (COALESCE(followup_count, 0) = 0 AND first_sent_at <= datetime('now', '-3 days'))
                  OR (followup_count = 1 AND followup_1_at <= datetime('now', '-4 days'))
              )
            """
        ).fetchone()[0]

    return {"followup_1_sent": fu1_sent, "followup_2_sent": fu2_sent, "due_remaining": due_rem}

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
