import email
from email.header import decode_header
import imaplib
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Set

from .. import config
from ..agents.discord_notifier import DiscordNotifier
from ..db import database
from .exclusion_filter import record_bounced_email

EMAIL_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

BOUNCE_SEARCH_CRITERIA = [
    'FROM "mailer-daemon"',
    'FROM "postmaster"',
    'SUBJECT "Delivery Status Notification"',
    'SUBJECT "Undelivered Mail"',
    'SUBJECT "Undeliverable"',
    'SUBJECT "Mail delivery failed"',
    'SUBJECT "Address not found"',
    'SUBJECT "Message not delivered"',
    'SUBJECT "Returned mail"',
    'SUBJECT "Failure Notice"',
]

BOUNCE_PATTERNS = [
    re.compile(r"X-Failed-Recipients:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE),
    re.compile(r"Final-Recipient:\s*rfc822;\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE),
    re.compile(r"Original-Recipient:\s*rfc822;\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE),
    re.compile(r"Your message wasn't delivered to\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE),
    re.compile(r"Your message to\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\s+couldn't be delivered", re.IGNORECASE),
    re.compile(r"The email account that you tried to reach does not exist.*?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE | re.DOTALL),
    re.compile(r"Delivery to the following recipient(?:s)? failed(?: permanently)?:\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?", re.IGNORECASE),
    re.compile(r"Failed Recipient:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE),
    re.compile(r"<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>:\s*(?:host|550|554|user unknown|address rejected)", re.IGNORECASE),
]


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


def _detect_trash_folder(mail: imaplib.IMAP4_SSL, user_email: str) -> str:
    try:
        res, folders = mail.list()
        if res == "OK" and folders:
            for f in folders:
                line = f.decode("utf-8", errors="ignore") if isinstance(f, bytes) else str(f)
                if "\\Trash" in line:
                    m = re.search(r'"([^"]+)"\s*$', line)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return "[Gmail]/Trash" if "nsut.ac.in" in (user_email or "").lower() else "[Gmail]/Bin"


def _is_system_or_self_address(addr: str, self_emails: Set[str]) -> bool:
    a = addr.strip().lower().strip(".,;<>\"'")
    if not a or "@" not in a:
        return True
    if a in self_emails:
        return True
    local, dom = a.split("@", 1)
    if local in ("mailer-daemon", "postmaster", "noreply", "no-reply"):
        return True
    if any(dom.endswith(sys_dom) for sys_dom in ("googlemail.com", "google.com", "prod.outlook.com", "protection.outlook.com")):
        return True
    return False


def _extract_bounced_recipients(raw_bytes: bytes, self_emails: Set[str], known_db_emails: Set[str]) -> Set[str]:
    found: Set[str] = set()
    decoded_chunks: List[str] = []

    try:
        msg = email.message_from_bytes(raw_bytes)
        for hdr in ("X-Failed-Recipients", "Final-Recipient", "Original-Recipient"):
            for val in msg.get_all(hdr, []):
                for m in EMAIL_TOKEN_RE.findall(str(val)):
                    cand = m.lower().strip(".,;<>")
                    if not _is_system_or_self_address(cand, self_emails):
                        found.add(cand)

        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            if ctype == "message/rfc822":
                payload = part.get_payload()
                if isinstance(payload, list):
                    for sub in payload:
                        to_hdr = sub.get("To", "")
                        for m in EMAIL_TOKEN_RE.findall(str(to_hdr)):
                            cand = m.lower().strip(".,;<>")
                            if not _is_system_or_self_address(cand, self_emails):
                                found.add(cand)
            raw_part = part.get_payload(decode=True)
            if isinstance(raw_part, bytes):
                decoded_chunks.append(raw_part.decode(part.get_content_charset() or "utf-8", errors="ignore"))
            elif isinstance(part.get_payload(), str):
                decoded_chunks.append(part.get_payload())
    except Exception:
        pass

    decoded_chunks.append(raw_bytes.decode("utf-8", errors="ignore"))
    combined = "\n".join(decoded_chunks)

    for pat in BOUNCE_PATTERNS:
        for m in pat.findall(combined):
            cand = m.lower().strip(".,;<>")
            if not _is_system_or_self_address(cand, self_emails):
                found.add(cand)

    # Fallback: intersect all email tokens in the bounce body with known outreach_leads emails
    if not found and known_db_emails:
        for m in EMAIL_TOKEN_RE.findall(combined):
            cand = m.lower().strip(".,;<>")
            if cand in known_db_emails and not _is_system_or_self_address(cand, self_emails):
                found.add(cand)

    return found


def sweep_account_bounces(user_email: str, app_password: str, account_label: str = "") -> Dict[str, Any]:
    """
    Sweeps an IMAP inbox via UID for ANY bounce/NDR messages, extracts failed recipients,
    deletes those contacts from outreach_leads (while recording in bounced_blocklist),
    and deletes the bounce messages from the inbox immediately.
    """
    clean_pwd = (app_password or "").replace(" ", "").strip()
    if not user_email or not clean_pwd:
        return {"inbox_deleted": 0, "db_deleted": 0, "bounced_emails": []}

    self_emails = {
        user_email.strip().lower(),
        getattr(config, "GMAIL_ADDRESS", "vishal90301@gmail.com").strip().lower(),
        getattr(config, "COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in").strip().lower(),
    }

    inbox_deleted = 0
    db_deleted = 0
    all_bounced_emails: Set[str] = set()

    try:
        with database.get_connection() as c:
            known_db_emails = {
                r[0].strip().lower()
                for r in c.execute("SELECT email FROM outreach_leads WHERE email IS NOT NULL").fetchall()
                if r[0]
            }
    except Exception:
        known_db_emails = set()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=25)
        mail.login(user_email, clean_pwd)
        trash_folder = _detect_trash_folder(mail, user_email)
        mail.select("INBOX", readonly=False)

        matching_uids: Set[bytes] = set()
        for crit in BOUNCE_SEARCH_CRITERIA:
            try:
                res, data = mail.uid("search", None, f"({crit})")
                if res == "OK" and data and data[0]:
                    matching_uids.update(data[0].split())
            except Exception:
                continue

        if matching_uids:
            uid_list = sorted(matching_uids, key=lambda u: int(u) if u.isdigit() else 0)
            for uid in uid_list:
                try:
                    res, fdata = mail.uid("fetch", uid, "(RFC822)")
                    if res == "OK" and fdata:
                        for item in fdata:
                            if isinstance(item, tuple) and isinstance(item[1], bytes):
                                extracted = _extract_bounced_recipients(item[1], self_emails, known_db_emails)
                                for b_em in extracted:
                                    all_bounced_emails.add(b_em)
                                    if record_bounced_email(b_em, f"IMAP Bounce ({user_email})"):
                                        db_deleted += 1
                except Exception as fe:
                    print(f"[BounceMonitor] Warning fetching UID {uid} on {user_email}: {fe}")

            # Batch copy to Trash + mark \Deleted + EXPUNGE so inbox stays 100% bounce-free
            batch_size = 200
            for i in range(0, len(uid_list), batch_size):
                batch = uid_list[i : i + batch_size]
                uid_str = b",".join(batch).decode("utf-8")
                try:
                    mail.uid("copy", uid_str, f'"{trash_folder}"')
                except Exception:
                    pass
                mail.uid("store", uid_str, "+FLAGS", "(\\Deleted)")
                inbox_deleted += len(batch)

            mail.expunge()
            print(
                f"[BounceMonitor:{account_label or user_email}] Deleted {inbox_deleted} bounce mail(s) from INBOX; "
                f"extracted {len(all_bounced_emails)} bounced address(es) ({sorted(all_bounced_emails)}); "
                f"removed {db_deleted} row(s) from outreach_leads."
            )

        mail.close()
        mail.logout()
    except Exception as e:
        print(f"[BounceMonitor] Error sweeping {user_email}: {e}")

    return {
        "inbox_deleted": inbox_deleted,
        "db_deleted": db_deleted,
        "bounced_emails": sorted(all_bounced_emails),
    }


def sweep_all_inboxes_for_bounces() -> Dict[str, Any]:
    """
    Sweeps BOTH NSUT Workspace and Personal Gmail inboxes for bounce messages,
    deletes them from the inboxes, and deletes the bounced contacts from outreach_leads.
    """
    college_addr = getattr(config, "COLLEGE_EMAIL_ADDRESS", os.environ.get("COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in"))
    college_pwd = getattr(config, "COLLEGE_EMAIL_APP_PASSWORD", os.environ.get("COLLEGE_EMAIL_APP_PASSWORD", ""))
    gmail_addr = getattr(config, "GMAIL_ADDRESS", os.environ.get("GMAIL_ADDRESS", "vishal90301@gmail.com"))
    gmail_pwd = getattr(config, "GMAIL_APP_PASSWORD", os.environ.get("GMAIL_APP_PASSWORD", ""))

    r1 = sweep_account_bounces(college_addr, college_pwd, "NSUT Workspace") if college_pwd else {"inbox_deleted": 0, "db_deleted": 0, "bounced_emails": []}
    r2 = sweep_account_bounces(gmail_addr, gmail_pwd, "Personal Gmail") if gmail_pwd else {"inbox_deleted": 0, "db_deleted": 0, "bounced_emails": []}

    return {
        "inbox_deleted": r1["inbox_deleted"] + r2["inbox_deleted"],
        "db_deleted": r1["db_deleted"] + r2["db_deleted"],
        "bounced_emails": sorted(set(r1["bounced_emails"]) | set(r2["bounced_emails"])),
    }


def process_and_clean_bounces(mail: imaplib.IMAP4_SSL, user_email: str) -> List[str]:
    # Retained for backwards compatibility if called directly with an open IMAP session
    return []


def check_account_replies(user_email: str, app_password: str, account_label: str) -> List[Dict[str, Any]]:
    """
    Checks a single IMAP inbox for incoming recruiter replies (after sweeping bounces).
    """
    detected = []
    clean_pwd = (app_password or "").replace(" ", "").strip()
    if not clean_pwd:
        return []

    # 1. Sweep & delete bounces first
    sweep_account_bounces(user_email, clean_pwd, account_label)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
        mail.login(user_email, clean_pwd)
        mail.select("INBOX", readonly=True)

        since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, msg_nums = mail.search(None, f'(SINCE "{since_date}")')

        if status != "OK" or not msg_nums[0]:
            mail.logout()
            return []

        conn = database.get_connection()
        active_leads = conn.execute(
            "SELECT id, name, email, company, status, sender_email FROM outreach_leads WHERE status IN ('sent', 'pending')"
        ).fetchall()
        conn.close()
        lead_map = {row["email"].lower(): row for row in active_leads if row["email"]}

        if not lead_map:
            mail.logout()
            return []

        notifier = DiscordNotifier()
        message_ids = msg_nums[0].split()

        for m_id in reversed(message_ids[-40:]):
            res, data = mail.fetch(m_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if res != "OK" or not data or not data[0]:
                continue

            raw_header = data[0][1] if len(data[0]) > 1 else data[0]
            msg = email.message_from_bytes(raw_header) if isinstance(raw_header, bytes) else email.message_from_string(str(raw_header))

            from_header = _decode_str(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", ""))

            sender_match = EMAIL_TOKEN_RE.search(from_header)
            if not sender_match:
                continue

            sender_email = sender_match.group(0).lower()
            if sender_email in lead_map:
                lead = lead_map[sender_email]
                if lead["status"] == "replied":
                    continue
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
                except Exception:
                    snippet = f"New reply received in your {account_label} inbox."

                with database.get_connection() as c:
                    c.execute(
                        """
                        UPDATE outreach_leads 
                        SET status = 'replied', reply_received_at = CURRENT_TIMESTAMP, reply_snippet = ?
                        WHERE id = ?
                        """,
                        (snippet[:400], lead_id),
                    )

                embed = {
                    "title": f"🎉 Recruiter Replied: {name} @ {company}",
                    "description": f"**Subject:** {subject}\n\n```text\n{snippet[:350]}...\n```",
                    "color": 0xF1C40F,
                    "fields": [
                        {"name": "👤 Recruiter", "value": f"{name} (`{sender_email}`)", "inline": True},
                        {"name": "🏢 Company", "value": company, "inline": True},
                        {"name": "📥 Inbox Received", "value": f"`{user_email}` ({account_label})", "inline": True},
                        {"name": "⚡ Action Required", "value": f"Open `{user_email}` inbox to reply and schedule a chat!", "inline": False},
                    ],
                    "footer": {"text": "Dual-Engine Autonomous Reply & Bounce Monitor"},
                }

                notifier.client.send_message(
                    channel_id=config.DISCORD_JOBS_CHANNEL_ID,
                    content=f"🚨 **RECRUITER REPLIED TO YOUR COLD EMAIL** <@{config.DISCORD_USER_ID}>!",
                    embeds=[embed],
                )

                detected.append({
                    "email": sender_email,
                    "name": name,
                    "company": company,
                    "subject": subject,
                    "inbox": user_email,
                })
                del lead_map[sender_email]

        mail.logout()
    except Exception as e:
        print(f"[ReplyMonitor] Error checking replies for {user_email}: {e}")

    return detected


def check_gmail_replies() -> List[Dict[str, Any]]:
    all_replies = []
    college_addr = getattr(config, "COLLEGE_EMAIL_ADDRESS", os.environ.get("COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in"))
    college_pwd = getattr(config, "COLLEGE_EMAIL_APP_PASSWORD", os.environ.get("COLLEGE_EMAIL_APP_PASSWORD", ""))
    if college_pwd:
        all_replies.extend(check_account_replies(college_addr, college_pwd, "NSUT Workspace"))

    gmail_addr = getattr(config, "GMAIL_ADDRESS", os.environ.get("GMAIL_ADDRESS", "vishal90301@gmail.com"))
    gmail_pwd = getattr(config, "GMAIL_APP_PASSWORD", os.environ.get("GMAIL_APP_PASSWORD", ""))
    if gmail_pwd:
        all_replies.extend(check_account_replies(gmail_addr, gmail_pwd, "Personal Gmail"))

    return all_replies
