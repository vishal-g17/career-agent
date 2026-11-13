import imaplib
import email
from email.header import decode_header
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
from ..db import database
from ..agents.discord_notifier import DiscordNotifier
from .. import config

def _decode_str(header_val: str) -> str:
    if not header_val:
        return ""
    decoded_list = decode_header(header_val)
    parts = []
    for text, enc in decoded_list:
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(str(text))
    return " ".join(parts).strip()

def process_and_clean_bounces(mail: imaplib.IMAP4_SSL, user_email: str) -> List[str]:
    """
    Detects Mailer-Daemon bounce notifications, marks them in SQLite as 'bounced',
    and removes/archives them from the user's Primary Inbox so the user never sees clutter.
    """
    bounced_addresses = []
    try:
        since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        status, nums = mail.search(None, f'(FROM "mailer-daemon@googlemail.com" SINCE "{since_date}")')
        if status != "OK" or not nums[0]:
            return []

        msg_ids = nums[0].split()
        for mid in msg_ids:
            res, data = mail.fetch(mid, "(RFC822)")
            if res != "OK" or not data or not data[0]:
                continue

            raw = data[0][1].decode("utf-8", errors="ignore") if isinstance(data[0][1], bytes) else str(data[0][1])

            # Extract recipient from Google's bounce pattern
            patterns = [
                r'The email account that you tried to reach does not exist.*?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)',
                r"Your message wasn't delivered to ([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
                r'Final-Recipient:\s*rfc822;\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)',
                r'Failed Recipient:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)'
            ]

            bounced_em = None
            for p in patterns:
                m = re.search(p, raw, re.IGNORECASE | re.DOTALL)
                if m:
                    candidate = m.group(1).lower().strip()
                    if candidate != user_email.lower() and not candidate.endswith("googlemail.com") and not candidate.endswith("google.com"):
                        bounced_em = candidate
                        break

            if bounced_em:
                bounced_addresses.append(bounced_em)
                with database.get_connection() as c:
                    # Move to bounced_outreach_leads quarantine table
                    c.execute(
                        """
                        INSERT OR IGNORE INTO bounced_outreach_leads (email, status, reply_snippet, last_sent_at)
                        VALUES (?, 'bounced', 'Address not found / Mailbox deactivated', CURRENT_TIMESTAMP)
                        """,
                        (bounced_em,)
                    )
                    # Remove from active outreach_leads table so it stays clean
                    c.execute("DELETE FROM outreach_leads WHERE LOWER(email) = ?", (bounced_em,))

            # Auto-trash bounce notice from primary inbox
            trash_folder = "[Gmail]/Trash" if "nsut.ac.in" in user_email.lower() else "[Gmail]/Bin"
            try:
                mail.copy(mid, trash_folder)
            except:
                pass
            mail.store(mid, "+FLAGS", "\\Deleted")

        mail.expunge()
        if bounced_addresses:
            print(f"[ReplyMonitor] Processed & cleaned {len(bounced_addresses)} bounce notifications from {user_email}")
    except Exception as e:
        print(f"[ReplyMonitor] Error processing bounces for {user_email}: {e}")

    return bounced_addresses

def check_account_replies(user_email: str, app_password: str, account_label: str) -> List[Dict[str, Any]]:
    """
    Checks a single IMAP inbox for incoming recruiter replies and automatically purges bounce notifications.
    """
    detected = []
    clean_pwd = app_password.replace(" ", "").strip()
    if not clean_pwd:
        return []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
        mail.login(user_email, clean_pwd)
        mail.select("INBOX", readonly=False)

        # 1. Clean incoming bounces first
        process_and_clean_bounces(mail, user_email)

        # 2. Check for real recruiter replies
        since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, msg_nums = mail.search(None, f'(SINCE "{since_date}")')

        if status != "OK" or not msg_nums[0]:
            mail.logout()
            return []

        conn = database.get_connection()
        active_leads = conn.execute(
            "SELECT id, name, email, company, status, sender_email FROM outreach_leads WHERE status IN ('sent', 'pending')"
        ).fetchall()
        lead_map = {row["email"].lower(): row for row in active_leads}

        if not lead_map:
            mail.logout()
            return []

        notifier = DiscordNotifier()
        message_ids = msg_nums[0].split()

        for m_id in reversed(message_ids[-30:]):
            res, data = mail.fetch(m_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if res != "OK" or not data or not data[0]:
                continue

            raw_header = data[0][1] if len(data[0]) > 1 else data[0]
            if isinstance(raw_header, bytes):
                msg = email.message_from_bytes(raw_header)
            else:
                msg = email.message_from_string(str(raw_header))

            from_header = _decode_str(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", ""))

            sender_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', from_header)
            if not sender_match:
                continue

            sender_email = sender_match.group(0).lower()

            if sender_email in lead_map:
                lead = lead_map[sender_email]
                lead_id = lead["id"]
                name = lead["name"] or from_header
                company = lead["company"]

                snippet = ""
                try:
                    b_res, b_data = mail.fetch(m_id, "(BODY.PEEK[TEXT])")
                    if b_res == "OK" and b_data and len(b_data) > 0:
                        raw_body = b_data[0][1] if isinstance(b_data[0], tuple) else b_data[0]
                        if isinstance(raw_body, bytes):
                            snippet = raw_body.decode("utf-8", errors="ignore")[:350].strip()
                except:
                    snippet = f"New reply received in your {account_label} inbox."

                with database.get_connection() as c:
                    c.execute(
                        """
                        UPDATE outreach_leads 
                        SET status = 'replied', reply_received_at = CURRENT_TIMESTAMP, reply_snippet = ?
                        WHERE id = ?
                        """,
                        (snippet[:400], lead_id)
                    )

                embed = {
                    "title": f"🎉 Recruiter Replied: {name} @ {company}",
                    "description": f"**Subject:** {subject}\n\n```text\n{snippet[:350]}...\n```",
                    "color": 0xF1C40F,
                    "fields": [
                        {"name": "👤 Recruiter", "value": f"{name} (`{sender_email}`)", "inline": True},
                        {"name": "🏢 Company", "value": company, "inline": True},
                        {"name": "📥 Inbox Received", "value": f"`{user_email}` ({account_label})", "inline": True},
                        {"name": "⚡ Action Required", "value": f"Open `{user_email}` inbox to reply and schedule a chat!", "inline": False}
                    ],
                    "footer": {"text": "Dual-Engine Autonomous Reply Monitor"}
                }

                notifier.client.send_message(
                    channel_id=config.DISCORD_JOBS_CHANNEL_ID,
                    content=f"🚨 **RECRUITER REPLIED TO YOUR COLD EMAIL** <@{config.DISCORD_USER_ID}>!",
                    embeds=[embed]
                )

                detected.append({
                    "email": sender_email,
                    "name": name,
                    "company": company,
                    "subject": subject,
                    "inbox": user_email
                })
                del lead_map[sender_email]

        mail.logout()
    except Exception as e:
        print(f"[ReplyMonitor] Error checking replies for {user_email}: {e}")

    return detected

def check_gmail_replies() -> List[Dict[str, Any]]:
    """
    Monitors all configured Google email inboxes (NSUT Google Workspace + Personal Gmail)
    for incoming recruiter responses and automatically purges bounce notifications.
    """
    all_replies = []

    # 1. Check NSUT Google Workspace Inbox
    college_addr = getattr(config, "COLLEGE_EMAIL_ADDRESS", os.environ.get("COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in"))
    college_pwd = getattr(config, "COLLEGE_EMAIL_APP_PASSWORD", os.environ.get("COLLEGE_EMAIL_APP_PASSWORD", ""))
    if college_pwd:
        all_replies.extend(check_account_replies(college_addr, college_pwd, "NSUT Workspace"))

    # 2. Check Personal Gmail Inbox
    gmail_addr = getattr(config, "GMAIL_ADDRESS", os.environ.get("GMAIL_ADDRESS", "vishal90301@gmail.com"))
    gmail_pwd = getattr(config, "GMAIL_APP_PASSWORD", os.environ.get("GMAIL_APP_PASSWORD", ""))
    if gmail_pwd:
        all_replies.extend(check_account_replies(gmail_addr, gmail_pwd, "Personal Gmail"))

    return all_replies
