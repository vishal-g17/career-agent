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

    if not leads:
        print("[OutreachCampaign] No pending leads found matching criteria.")
        return {"attempted": 0, "sent": 0, "bounced": 0, "results": []}

    senders = get_active_senders(sender_mode)
    print(f"[OutreachCampaign] Initiating batch of {len(leads)} cold outreach emails across senders: {senders}...")
    results = []
    sent_count = 0
    bounced_count = 0

    for i, lead in enumerate(leads):
        lead_id = lead["id"]
        name = lead["name"] or "Hiring Team"
        email_addr = lead["email"]
        company = lead["company"] or "Your Team"

        # Strict Exclusion Filters E1, E2, E3 + Discarded/Bounced List
        if is_bounced_or_discarded(email_addr):
            print(f"[OutreachCampaign] [DISCARDED_BOUNCE] Permanently skipping {name} ({company}, {email_addr})")
            continue

        exclusion = is_excluded(name=name, headline=f"{company} {email_addr}")
        if exclusion:
            print(f"[OutreachCampaign] [EXCLUDED {exclusion}] Skipping {name} ({company}, {email_addr})")
            with database.get_connection() as c:
                c.execute("UPDATE outreach_leads SET status = 'excluded' WHERE id = ?", (lead_id,))
            continue

        # Round-robin selection across senders still below their daily cap (500 Gmail / 1500 NSUT)
        eligible_senders = [
            s for s in senders
            if today_counts.get(s.lower(), 0) < get_sender_daily_cap(s)
        ]
        if not eligible_senders:
            print(f"[OutreachCampaign] Daily sender caps reached mid-batch ({today_counts}). Stopping batch.")
            return {
                "attempted": i,
                "sent": sent_count,
                "bounced": bounced_count,
                "daily_cap_reached": True,
                "results": results,
            }
        current_sender = eligible_senders[i % len(eligible_senders)]

        # 1. Compose tailored 1-on-1 pitch with appropriate institutional signature
        mail_data = compose_cold_email(
            recipient_email=email_addr,
            role_title="Business / Product Analyst / Management",
            company_name=company,
            sender_email=current_sender
        )

        # 2. Dispatch via verified SMTP with anti-bounce protection
        success = send_cold_email_smtp(
            recipient_email=email_addr,
            subject=mail_data["subject"],
            body=mail_data["body"],
            sender_email=current_sender
        )

        with database.get_connection() as c:
            if success:
                c.execute(
                    """
                    UPDATE outreach_leads 
                    SET status = 'sent', sender_email = ?, first_sent_at = CURRENT_TIMESTAMP, last_sent_at = CURRENT_TIMESTAMP,
                        last_subject = ?
                    WHERE id = ?
                    """,
                    (current_sender, mail_data["subject"], lead_id)
                )
                sent_count += 1
                today_counts[current_sender.lower()] = today_counts.get(current_sender.lower(), 0) + 1
                cap_cur = get_sender_daily_cap(current_sender)
                status_str = f"sent via {current_sender} ({today_counts[current_sender.lower()]}/{cap_cur} today)"
            else:
                c.execute("UPDATE outreach_leads SET status = 'bounced' WHERE id = ?", (lead_id,))
                bounced_count += 1
                status_str = "bounced/blocked"

        results.append({
            "name": name,
            "email": email_addr,
            "company": company,
            "sender": current_sender,
            "status": status_str
        })

        if i < len(leads) - 1:
            # Randomized sub-20-second delay (4.5s - 19.5s)
            jitter = round(random.uniform(4.5, 19.5), 1)
            print(f"[OutreachCampaign] Random <20s timer: waiting {jitter}s before next send ({i+1}/{len(leads)})...")
            time.sleep(jitter)

    print(f"[OutreachCampaign] Batch complete. Sent: {sent_count}, Bounced/Blocked: {bounced_count}")
    return {
        "attempted": len(leads),
        "sent": sent_count,
        "bounced": bounced_count,
        "results": results
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
