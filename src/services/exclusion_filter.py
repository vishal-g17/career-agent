import re
from typing import Tuple

FORBIDDEN_COMPANIES = ["magicpin", "lifelong", "nom"]

# Strictly exclude batches graduating AFTER 2026 and college juniors / unverified students
# (Graduates till 2026, i.e. 2024, 2025, 2026, and working alumni are ALLOWED per user directive)
AFTER_26_JUNIOR_PATTERNS = [
    # 1. Graduating classes strictly AFTER 2026 (2027 to 2035)
    r"['’]2[7-9]\b",
    r"['’]3[0-5]\b",
    r"\b202[7-9]\b",
    r"\b203[0-5]\b",
    r"\bbatch of 202[7-9]\b",
    r"\bclass of 202[7-9]\b",
    r"\bbatch of 203[0-5]\b",
    r"\bclass of 203[0-5]\b",

    # 2. Explicit junior / lower-class student keywords (undergrads graduating after 2026)
    r"\bstudent\b",
    r"\bundergrad\b",
    r"\bundergraduate\b",
    r"\bpursuing\b",
    r"\bstudying\b",
    r"\bjuniors?\b",
    r"\bsophomores?\b",
    r"\bfreshm[ae]n\b",
    r"\bpre-final\b",
    r"\b1st year\b",
    r"\b2nd year\b",
    r"\b3rd year\b",
    r"\bfirst year\b",
    r"\bsecond year\b",
    r"\bthird year\b",

    # 3. Aspirants / Inexperienced Seekers
    r"\baspiring\b",
    r"\bseeking\b",
    r"\bmentee\b",
    r"\blearner\b"
]

ALLOWED_YEAR_PATTERN = re.compile(r"['’]2[4-6]\b|\b202[4-6]\b", re.IGNORECASE)
STUDENT_BASE_PATTERN = re.compile(r"\bstudent\b|\bundergrad\b|\bundergraduate\b|\bpursuing\b|\bstudying\b", re.IGNORECASE)

IRRELEVANT_PATTERNS = [
    r"\bprof\.?\b",
    r"\bprofessor\b",
    r"\bdr\.?\b",
    r"\bfaculty\b",
    r"\bhod\b",
    r"\bdean\b",
    r"\blecturer\b",
    r"\bteacher\b",
    r"\bcivil services\b",
    r"\bupsc\b",
    r"\badvocate\b",
    r"\blawyer\b",
    r"\bjudge\b",
    r"\bdoctor\b",
    r"\bnurse\b",
    r"\bartist\b",
    r"\bphotographer\b",
    r"\bchartered accountant\b",
    r"\bca\b",
    r"\bstylist\b",
    r"\btrainer\b",
    r"\bfitness\b",
    r"\bcustomer support\b",
    r"\bsupport executive\b",
    r"\btelecaller\b",
    r"\bbpo\b",
    r"\bcall center\b"
]

def is_excluded(name: str = "", headline: str = "", card_text: str = "") -> str:
    """
    Checks candidate/connection against strict exclusion filters E1, E2, E3.
    Returns:
        - Reason string if excluded
        - Empty string "" if candidate is valid, senior, and eligible
    """
    full_text = f"{name} {headline} {card_text}".lower()

    # E1: Confidentiality / Former Employers
    for fc in FORBIDDEN_COMPANIES:
        if fc in full_text:
            return f"E1_CONFIDENTIAL_COMPANY_{fc.upper()}"

    # E2: Students / Juniors graduating AFTER 2026 (Grads till 2026 are allowed)
    for st in AFTER_26_JUNIOR_PATTERNS:
        if re.search(st, full_text, re.IGNORECASE):
            return f"E2_JUNIOR_AFTER_2026 ({st})"
            
    # If explicitly tagged as student/undergrad without an allowed graduation year <= 2026, exclude
    if STUDENT_BASE_PATTERN.search(full_text) and not ALLOWED_YEAR_PATTERN.search(full_text):
        return "E2_UNVERIFIED_STUDENT_WITHOUT_2026_BATCH"

    # E3: Irrelevant non-decision makers (exempt recruiters / HR / talent acquisition)
    is_recruiter = any(r in full_text for r in ["recruiter", "talent acquisition", "hiring", "sourcer", "headhunter"])
    if not is_recruiter:
        for irr in IRRELEVANT_PATTERNS:
            if re.search(irr, full_text, re.IGNORECASE):
                return f"E3_IRRELEVANT_ROLE ({irr})"

    return ""

_BOUNCED_CACHE = None
_BOUNCED_CACHE_TS = 0.0


def ensure_blocklist_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bounced_blocklist (
            email TEXT PRIMARY KEY,
            reason TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def record_bounced_email(email_addr: str, reason: str = "Bounce / Invalid Mailbox") -> bool:
    """
    Records a bounced/discarded email in internal bounced_blocklist (so we never re-send)
    AND deletes it completely from active outreach_leads data.
    Returns True if a row was deleted from outreach_leads.
    """
    global _BOUNCED_CACHE
    if not email_addr:
        return False
    clean_email = email_addr.strip().lower()
    deleted = False
    try:
        import os
        import sqlite3
        from pathlib import Path
        db_file = Path(os.environ.get("DATABASE_PATH", str(Path.home() / ".linkedin_agent.db")))
        conn = sqlite3.connect(str(db_file), timeout=30.0)
        ensure_blocklist_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO bounced_blocklist (email, reason, detected_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (clean_email, reason),
        )
        cur = conn.execute("DELETE FROM outreach_leads WHERE LOWER(TRIM(email)) = ?", (clean_email,))
        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        if _BOUNCED_CACHE is not None:
            _BOUNCED_CACHE.add(clean_email)
    except Exception as e:
        print(f"[ExclusionFilter] Error recording/deleting bounce {clean_email}: {e}")
    return deleted


def is_bounced_or_discarded(email_addr: str) -> bool:
    """
    Returns True if an email is in the internal bounced_blocklist and must NEVER be contacted again.
    Refreshes cache every 30s so newly caught IMAP bounces take effect immediately.
    """
    global _BOUNCED_CACHE, _BOUNCED_CACHE_TS
    if not email_addr:
        return False

    import time
    clean_email = email_addr.strip().lower()
    now = time.time()
    if _BOUNCED_CACHE is None or (now - _BOUNCED_CACHE_TS) > 30.0:
        try:
            import os
            import sqlite3
            from pathlib import Path
            db_file = Path(os.environ.get("DATABASE_PATH", str(Path.home() / ".linkedin_agent.db")))
            if db_file.exists():
                conn = sqlite3.connect(str(db_file), timeout=15.0)
                ensure_blocklist_table(conn)
                rows1 = conn.execute("SELECT LOWER(TRIM(email)) FROM bounced_blocklist WHERE email IS NOT NULL").fetchall()
                rows2 = conn.execute(
                    "SELECT LOWER(TRIM(email)) FROM outreach_leads WHERE status IN ('bounced', 'excluded') AND email IS NOT NULL"
                ).fetchall()
                conn.close()
                _BOUNCED_CACHE = {r[0] for r in rows1 if r[0]} | {r[0] for r in rows2 if r[0]}
            else:
                _BOUNCED_CACHE = set()
            _BOUNCED_CACHE_TS = now
        except Exception:
            if _BOUNCED_CACHE is None:
                _BOUNCED_CACHE = set()

    return clean_email in _BOUNCED_CACHE
