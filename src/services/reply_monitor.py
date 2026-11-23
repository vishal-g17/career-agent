from concurrent.futures import ThreadPoolExecutor
import base64
import email
from email.header import decode_header
import imaplib
import os
import quopri
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


def _extract_clean_reply_body(raw_bytes) -> str:
    """
    Robustly extracts decoded plain text from an inbound email (bytes or string),
    handling multipart streams, base64 payloads, quoted-printable encoding,
    Outlook/Exchange boundaries (_000_), HTML tags, and charset variations.
    """
    if not raw_bytes:
        return ""
    try:
        raw_str = raw_bytes.decode("utf-8", errors="ignore") if isinstance(raw_bytes, bytes) else str(raw_bytes)
        s_strip = raw_str.strip()

        # Handle base64-encoded HTML strings (e.g. PGRp... / PGh0...)
        if s_strip.startswith(("PGRp", "PGh0")):
            b = s_strip.encode("ascii", errors="ignore")
            b = b[:len(b) - (len(b) % 4)]
            try:
                dec = base64.b64decode(b).decode("utf-8", errors="ignore")
                return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dec)).strip()
            except Exception:
                pass

        # If it is a MIME stream missing top headers (starts with boundary like --)
        if s_strip.startswith("--"):
            m = re.match(r"^--([^\r\n\s]+)", s_strip)
            if m:
                fake_boundary = m.group(1).rstrip("-")
                if f'boundary="{fake_boundary}"' not in raw_str[:200]:
                    raw_str = f'Content-Type: multipart/mixed; boundary="{fake_boundary}"\r\n\r\n' + raw_str

        msg = email.message_from_string(raw_str)
        plain_parts: List[str] = []
        html_parts: List[str] = []

        for part in (msg.walk() if msg.is_multipart() else [msg]):
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            cte = (part.get("Content-Transfer-Encoding") or "").lower()
            charset = part.get_content_charset() or "utf-8"

            payload = part.get_payload()
            if isinstance(payload, str):
                txt = payload
                if "base64" in cte:
                    clean_b = re.sub(r"\s+", "", payload).encode("ascii", errors="ignore")
                    clean_b = clean_b[:len(clean_b) - (len(clean_b) % 4)]
                    try:
                        txt = base64.b64decode(clean_b).decode(charset, errors="ignore")
                    except Exception:
                        pass
                elif "quoted-printable" in cte:
                    try:
                        txt = quopri.decodestring(payload.encode("ascii", errors="ignore")).decode(charset, errors="ignore")
                    except Exception:
                        pass

                if ctype == "text/plain":
                    plain_parts.append(txt)
                elif ctype == "text/html":
                    html_parts.append(re.sub(r"<[^>]+>", " ", txt))
            elif isinstance(payload, bytes):
                try:
                    txt = payload.decode(charset, errors="ignore")
                    if ctype == "text/plain":
                        plain_parts.append(txt)
                    elif ctype == "text/html":
                        html_parts.append(re.sub(r"<[^>]+>", " ", txt))
                except Exception:
                    pass

        body = "\n".join(plain_parts).strip() or "\n".join(html_parts).strip()
        if body:
            clean = re.sub(r"<[^>]+>", " ", body)
            clean = re.sub(r"--[a-zA-Z0-9_-]+", " ", clean)
            clean = re.sub(r"Content-(?:Type|Transfer-Encoding):[^\n]+", " ", clean, flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", clean).strip()

        # Fallback regex decoders if walking didn't catch text
        b64_blocks = re.findall(r"(?:base64\s+)([A-Za-z0-9+/=\s]{20,})", raw_str, re.IGNORECASE)
        for block in b64_blocks:
            clean_b = re.sub(r"\s+", "", block).encode("ascii", errors="ignore")
            clean_b = clean_b[:len(clean_b) - (len(clean_b) % 4)]
            try:
                dec = base64.b64decode(clean_b).decode("utf-8", errors="ignore")
                if dec and len(dec.strip()) > 5:
                    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dec)).strip()
            except Exception:
                pass

        qp_blocks = re.findall(r"(?:quoted-printable\s+)([\s\S]+?)(?:--|$)", raw_str, re.IGNORECASE)
        for block in qp_blocks:
            try:
                dec = quopri.decodestring(block.encode("ascii", errors="ignore")).decode("utf-8", errors="ignore")
                if dec and len(dec.strip()) > 5:
                    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dec)).strip()
            except Exception:
                pass

        clean_fallback = re.sub(r"<[^>]+>", " ", raw_str[:500])
        clean_fallback = re.sub(r"--[a-zA-Z0-9_-]+", " ", clean_fallback)
        return re.sub(r"\s+", " ", clean_fallback).strip()
    except Exception:
        return raw_bytes.decode("utf-8", errors="ignore")[:400].strip() if isinstance(raw_bytes, bytes) else str(raw_bytes)[:400].strip()


def classify_inbound_reply(subject: str, body: str, auto_submitted_hdr: str = "", from_addr: str = "") -> str:
    """
    Classifies an inbound recruiter email into one of:
      - 'exited_redirected': Exited employee / deactivated / unmonitored mailbox
      - 'closed_no_opening': Explicit rejection / not hiring / no openings matching profile
      - 'auto_reply': Automated responses (out-of-office / holiday / Diwali / travel auto-responder,
                      ATS receipt, bot ticketing confirmation, Foundit / job board portal auto-ack).
                      CRITICAL: Lead MUST remain eligible for scheduled follow-ups!
      - 'replied': Genuine human reply written personally by recruiter, hiring manager, or founder.
                   CRITICAL: Lead is STRICTLY EXCLUDED from any follow-up emails!
    """
    s_low = (subject or "").lower().strip()
    auto_low = (auto_submitted_hdr or "").lower().strip()
    from_low = (from_addr or "").lower().strip()

    # 1. Header & system bot indicators -> Instant automated reply
    if auto_low and auto_low != "no":
        return "auto_reply"
    if any(bot_id in from_low for bot_id in ("mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply", "notifications@", "notification@", "system@")):
        return "auto_reply"

    # 2. Subject indicators -> Instant automated reply
    ooo_subject_keywords = (
        "automatic reply",
        "auto-reply",
        "autoreply",
        "out of office",
        "ooo",
        "auto response",
        "undeliverable",
        "delivery status notification",
        "failure notice",
        "returned mail",
        "acknowledgment",
        "we received your",
        "thank you for applying",
        "application received",
        "thank you for contacting",
        "thank you for your inquiry",
    )
    if any(k in s_low for k in ooo_subject_keywords):
        return "auto_reply"

    # Strip quoted original message so we classify ONLY the sender's newly written reply text
    top_body = re.split(
        r"\bon\s+(?:mon|tue|wed|thu|fri|sat|sun|\d{1,2}\s+[a-z]{3})|-----original message-----",
        body or "",
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    b_low = top_body.lower().strip()

    # 3. Exited employee / deactivated mailbox
    exited_phrases = (
        "no longer employed",
        "no longer with the company",
        "no longer with ",
        "no longer works at",
        "no longer associated with",
        "no longer active",
        "no longer in use",
        "not being monitored",
        "moved on from my role",
        "moved out of ",
        "moved on to a new opportunity",
        "last working day with the organization",
        "accounts will be deactivated",
        "going forward, please use my ",
        "no longer a student",
    )
    if any(p in b_low for p in exited_phrases):
        return "exited_redirected"

    # 4. Explicit rejection / closed opening / not hiring
    no_opening_phrases = (
        "unfortunately, we are not currently hiring",
        "we are not currently hiring",
        "not hiring at the moment",
        "don't have openings",
        "don’t have openings",
        "do not have any openings",
        "do not have openings",
        "do not have any vacancies",
        "not able to take your candidature forward",
        "will not be moving forward with your application",
        "there is no opening",
        "there are no opening",
        "no current openings",
        "position is filled",
        "role is filled",
        "do not have open role matching",
        "no opening for these mentioned roles",
        "no openings for these mentioned roles",
    )
    if any(p in b_low for p in no_opening_phrases):
        return "closed_no_opening"

    # 5. Out of office / leave / vacation auto-responder phrases (Keep eligible for follow-up!)
    ooo_phrases = (
        "out of office",
        "out of the office",
        "currently on leave",
        "am on leave",
        "will be on leave",
        "on planned leave",
        "annual leave",
        "on pto",
        "am ooo",
        "i will be ooo",
        "i am ooo",
        "limited access to email",
        "limited access to my email",
        "limited access to mails",
        "limited access to emails",
        "no access to email",
        "no access to my email",
        "no access to mails",
        "no access to emails",
        "away from the office",
        "away from my desk",
        "away from office",
        "on business travel",
        "on travel",
        "on holiday",
        "shall respond upon my return",
        "will respond upon my return",
        "will respond once i resume",
        "shall respond on resuming",
        "will respond as soon as possible upon",
        "in my absence",
        "urgent matters, please reach",
        "urgent matters please contact",
        "out of office today",
    )
    if any(p in b_low for p in ooo_phrases):
        return "auto_reply"

    # 6. Automated ATS / Ticketing / Bot Acknowledgment phrases (Keep eligible for follow-up!)
    bot_phrases = (
        "this is an automated response",
        "this is an automatically generated",
        "please do not reply to this email",
        "do not reply to this message",
        "do not reply directly to this",
        "this mailbox is not monitored",
        "this inbox is not monitored",
        "unmonitored mailbox",
        "we have received your application",
        "we received your application",
        "thank you for your interest in the",
        "thank you for submitting your application",
        "complete your foundit profile",
        "to proceed with the application process, please complete your foundit",
        "your request (#",
        "your ticket #",
        "ticket has been created",
        "thank you for contacting the hiring team",
    )
    if any(p in b_low for p in bot_phrases):
        return "auto_reply"

    # 7. Genuine Human Reply (Written personally by human recruiter/founder/HM)
    return "replied"


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
            "SELECT id, name, email, company, status, sender_email, COALESCE(reply_snippet, '') AS reply_snippet FROM outreach_leads WHERE status IN ('sent', 'pending', 'auto_replied')"
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

        res, batch_data = mail.fetch(b",".join(target_ids), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT AUTO-SUBMITTED)])")
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
            auto_sub = _decode_str(msg.get("Auto-Submitted", ""))

            sender_match = EMAIL_TOKEN_RE.search(from_header)
            if not sender_match:
                continue

            sender_email = sender_match.group(0).lower()
            if sender_email in lead_map:
                lead = lead_map[sender_email]
                if lead["status"] in ("replied", "closed_no_opening", "exited_redirected"):
                    continue
                lead_id = lead["id"]
                name = lead["name"] or from_header
                company = lead["company"]

                snippet = ""
                try:
                    b_res, b_data = mail.fetch(m_id, "(BODY.PEEK[])")
                    if b_res == "OK" and b_data and len(b_data) > 0:
                        raw_full = b_data[0][1] if isinstance(b_data[0], tuple) else b_data[0]
                        if isinstance(raw_full, bytes):
                            snippet = _extract_clean_reply_body(raw_full)
                except Exception:
                    snippet = f"New reply received in your {account_label} inbox."

                reply_kind = classify_inbound_reply(subject, snippet, auto_sub, sender_email)

                if reply_kind in ("auto_reply", "ooo_auto_reply"):
                    # Automated response (OOO, holiday, vacation, ATS receipt, bot auto-responder).
                    # Lead remains 100% ELIGIBLE for scheduled cold email follow-ups!
                    clean_auto_snippet = f"[AUTO] {snippet[:380]}"
                    with database.get_connection() as c:
                        c.execute(
                            """
                            UPDATE outreach_leads 
                            SET status = 'auto_replied', reply_snippet = ?
                            WHERE id = ? AND status IN ('sent', 'pending', 'auto_replied')
                            """,
                            (clean_auto_snippet, lead_id),
                        )
                    print(f"[ReplyMonitor] 🤖 Automated response detected for {name} ({company}, {sender_email}) -> Tagged 'auto_replied' (eligible for follow-up).")
                    del lead_map[sender_email]
                    continue

                if reply_kind in ("exited_redirected", "closed_no_opening"):
                    with database.get_connection() as c:
                        c.execute(
                            """
                            UPDATE outreach_leads
                            SET status = ?, reply_received_at = CURRENT_TIMESTAMP, reply_snippet = ?
                            WHERE id = ?
                            """,
                            (reply_kind, snippet[:400], lead_id),
                        )
                    print(f"[ReplyMonitor] 🛑 Lead {name} ({company}) marked as {reply_kind}: {snippet[:80]}")
                    del lead_map[sender_email]
                    continue

                # GENUINE HUMAN REPLY: Written personally by recruiter / founder / HM.
                # Lead is STRICTLY EXCLUDED from any follow-up emails!
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
