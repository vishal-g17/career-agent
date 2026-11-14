from concurrent.futures import ThreadPoolExecutor
import email
from email.header import decode_header
import imaplib
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set

from .. import config
from ..agents.discord_notifier import DiscordNotifier
from ..db import database
from .exclusion_filter import ensure_blocklist_table

EMAIL_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

# Single combined OR search in Gmail IMAP X-GM-RAW for 1-roundtrip instant detection
GMAIL_BOUNCE_RAW_QUERY = (
    'X-GM-RAW "from:mailer-daemon OR from:postmaster '
    'OR subject:\\"Delivery Status Notification\\" '
    'OR subject:\\"Address not found\\" '
    'OR subject:\\"Message not delivered\\" '
    'OR subject:\\"Undelivered Mail\\" '
    'OR subject:\\"Undeliverable\\" '
    'OR subject:\\"Mail delivery failed\\" '
    'OR subject:\\"Failure Notice\\" '
    'OR subject:\\"Returned mail\\""'
)

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


def _extract_bounced_recipients_fast(raw_bytes: bytes, self_emails: Set[str], known_db_emails: Set[str]) -> Set[str]:
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

    # Also match any known outreach_leads address mentioned inside the bounce snippet
    if known_db_emails:
        for m in EMAIL_TOKEN_RE.findall(combined):
            cand = m.lower().strip(".,;<>")
            if cand in known_db_emails and not _is_system_or_self_address(cand, self_emails):
                found.add(cand)

    return found


def _bulk_purge_bounced_from_db(emails: Set[str], source_label: str) -> int:
    if not emails:
        return 0
    clean_list = sorted({e.strip().lower() for e in emails if e and "@" in e})
    if not clean_list:
        return 0
    db_file = Path(os.environ.get("DATABASE_PATH", str(Path.home() / ".linkedin_agent.db")))
    deleted_total = 0
    try:
        conn = sqlite3.connect(str(db_file), timeout=45.0)
        ensure_blocklist_table(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO bounced_blocklist (email, reason, detected_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            [(em, f"IMAP Bounce ({source_label})") for em in clean_list],
        )
        for em in clean_list:
            cur = conn.execute("DELETE FROM outreach_leads WHERE LOWER(TRIM(email)) = ?", (em,))
            deleted_total += cur.rowcount
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[BounceMonitor] DB purge error: {e}")
    return deleted_total


def sweep_account_bounces(user_email: str, app_password: str, account_label: str = "", purge_trash: bool = True) -> Dict[str, Any]:
    """
    Ultra-fast pipelined IMAP UID bounce sweeper:
      1. Finds all bounce UIDs in 1 single X-GM-RAW IMAP query.
      2. Fetches ONLY first 16 KB (BODY.PEEK[]<0.16384>) in batches of 100 UIDs (~0.3s/100 mails, skipping attachments!).
      3. Immediately deletes those bounce mails from INBOX AND permanently expunges them from Trash/Bin.
      4. Bulk-deletes every bounced recipient from outreach_leads (and records in internal bounced_blocklist).
    """
    clean_pwd = (app_password or "").replace(" ", "").strip()
    if not user_email or not clean_pwd:
        return {"inbox_deleted": 0, "trash_deleted": 0, "db_deleted": 0, "bounced_emails": []}

    self_emails = {
        user_email.strip().lower(),
        getattr(config, "GMAIL_ADDRESS", "vishal90301@gmail.com").strip().lower(),
        getattr(config, "COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in").strip().lower(),
    }

    inbox_deleted = 0
    trash_deleted = 0
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

        # --- STEP A: Sweep INBOX in pipelined 100-UID chunks ---
        mail.select("INBOX", readonly=False)
        res, data = mail.uid("search", None, GMAIL_BOUNCE_RAW_QUERY)
        if res != "OK":
            # Fallback if X-GM-RAW unsupported
            res, data = mail.uid("search", None, '(OR FROM "mailer-daemon" FROM "postmaster")')

        uid_list = data[0].split() if (res == "OK" and data and data[0]) else []
        if uid_list:
            batch_size = 100
            for i in range(0, len(uid_list), batch_size):
                batch = uid_list[i : i + batch_size]
                uid_str = b",".join(batch).decode("utf-8")
                batch_bounced: Set[str] = set()

                # Pipelined partial fetch (first 16KB only -> skips heavy PDF attachments!)
                fres, fdata = mail.uid("fetch", uid_str, "(BODY.PEEK[]<0.16384>)")
                if fres == "OK" and fdata:
                    for item in fdata:
                        if isinstance(item, tuple) and isinstance(item[1], bytes):
                            batch_bounced.update(_extract_bounced_recipients_fast(item[1], self_emails, known_db_emails))

                if batch_bounced:
                    all_bounced_emails.update(batch_bounced)
                    db_deleted += _bulk_purge_bounced_from_db(batch_bounced, account_label or user_email)

                # Immediately move to Trash + mark \Deleted + EXPUNGE right inside this batch!
                try:
                    mail.uid("copy", uid_str, f'"{trash_folder}"')
                except Exception:
                    pass
                mail.uid("store", uid_str, "+FLAGS.SILENT", "(\\Deleted)")
                mail.expunge()
                inbox_deleted += len(batch)

        # --- STEP B: Permanently delete bounce mails from Trash/Bin too so they are 100% gone ---
        if purge_trash and trash_folder:
            sel_res, _ = mail.select(f'"{trash_folder}"', readonly=False)
            if sel_res == "OK":
                tres, tdata = mail.uid("search", None, GMAIL_BOUNCE_RAW_QUERY)
                if tres != "OK":
                    tres, tdata = mail.uid("search", None, '(OR FROM "mailer-daemon" FROM "postmaster")')
                tuids = tdata[0].split() if (tres == "OK" and tdata and tdata[0]) else []
                if tuids:
                    for i in range(0, len(tuids), 200):
                        tbatch = tuids[i : i + 200]
                        tuid_str = b",".join(tbatch).decode("utf-8")
                        mail.uid("store", tuid_str, "+FLAGS.SILENT", "(\\Deleted)")
                        trash_deleted += len(tbatch)
                    mail.expunge()

        if inbox_deleted or trash_deleted or db_deleted:
            print(
                f"[BounceMonitor:{account_label or user_email}] INSTANT WIPE -> "
                f"INBOX deleted: {inbox_deleted}, Trash permanently wiped: {trash_deleted}, "
                f"DB leads deleted: {db_deleted} (addresses={len(all_bounced_emails)})."
            )

        mail.close()
        mail.logout()
    except Exception as e:
        print(f"[BounceMonitor] Error sweeping {user_email}: {e}")

    return {
        "inbox_deleted": inbox_deleted,
        "trash_deleted": trash_deleted,
        "db_deleted": db_deleted,
        "bounced_emails": sorted(all_bounced_emails),
    }


def sweep_all_inboxes_for_bounces() -> Dict[str, Any]:
    """
    Sweeps BOTH NSUT Workspace and Personal Gmail SIMULTANEOUSLY in parallel threads.
    """
    college_addr = getattr(config, "COLLEGE_EMAIL_ADDRESS", os.environ.get("COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in"))
    college_pwd = getattr(config, "COLLEGE_EMAIL_APP_PASSWORD", os.environ.get("COLLEGE_EMAIL_APP_PASSWORD", ""))
    gmail_addr = getattr(config, "GMAIL_ADDRESS", os.environ.get("GMAIL_ADDRESS", "vishal90301@gmail.com"))
    gmail_pwd = getattr(config, "GMAIL_APP_PASSWORD", os.environ.get("GMAIL_APP_PASSWORD", ""))

    tasks = []
    if college_pwd:
        tasks.append((college_addr, college_pwd, "NSUT Workspace"))
    if gmail_pwd:
        tasks.append((gmail_addr, gmail_pwd, "Personal Gmail"))

    if not tasks:
        return {"inbox_deleted": 0, "trash_deleted": 0, "db_deleted": 0, "bounced_emails": []}

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        results = list(pool.map(lambda t: sweep_account_bounces(t[0], t[1], t[2]), tasks))

    return {
        "inbox_deleted": sum(r["inbox_deleted"] for r in results),
        "trash_deleted": sum(r.get("trash_deleted", 0) for r in results),
        "db_deleted": sum(r["db_deleted"] for r in results),
        "bounced_emails": sorted({em for r in results for em in r["bounced_emails"]}),
    }


def process_and_clean_bounces(mail: imaplib.IMAP4_SSL, user_email: str) -> List[str]:
    return []


def check_account_replies(user_email: str, app_password: str, account_label: str) -> List[Dict[str, Any]]:
    detected = []
    clean_pwd = (app_password or "").replace(" ", "").strip()
    if not clean_pwd:
        return []

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
        target_ids = message_ids[-250:]
        if not target_ids:
            mail.logout()
            return []

        res, batch_data = mail.fetch(b",".join(target_ids), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if res != "OK" or not batch_data:
            mail.logout()
            return []

        header_items = []
        for item in batch_data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                meta = item[0].decode("utf-8", errors="ignore") if isinstance(item[0], bytes) else str(item[0])
                seq_m = re.match(r"^\s*(\d+)", meta)
                if seq_m:
                    header_items.append((seq_m.group(1).encode("ascii"), item[1]))

        for m_id, raw_header in reversed(header_items):
            msg = email.message_from_bytes(raw_header)

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
