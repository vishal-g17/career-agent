import re
import subprocess
import socket
from typing import Dict, Any, List, Tuple

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "guerrillamail.com", "10minutemail.com", "yopmail.com", "trashmail.com"
}

ROLE_PREFIXES = {
    "noreply", "no-reply", "donotreply", "support", "help", "billing", "legal", "security", "privacy"
}

KNOWN_MX_DOMAINS = {
    "gmail.com": ["gmail-smtp-in.l.google.com"],
    "yahoo.com": ["mta5.am0.yahoodns.net"],
    "yahoo.co.in": ["mta5.am0.yahoodns.net"],
    "hotmail.com": ["hotmail-com.olc.protection.outlook.com"],
    "outlook.com": ["outlook-com.olc.protection.outlook.com"],
    "swiggy.in": ["aspmx.l.google.com"],
    "swiggy.com": ["aspmx.l.google.com"],
    "zomato.com": ["aspmx.l.google.com"],
    "urbancompany.com": ["aspmx.l.google.com"],
    "cars24.com": ["aspmx.l.google.com"],
    "nykaa.com": ["aspmx.l.google.com"],
    "redfm.in": ["aspmx.l.google.com"],
    "nsut.ac.in": ["aspmx.l.google.com"]
}

_MX_CACHE: Dict[str, List[str]] = {}

def get_mx_records(domain: str) -> List[str]:
    """
    Queries DNS for MX (Mail Exchange) records of a domain with memory cache and robust timeout.
    """
    domain = domain.lower().strip()
    if domain in KNOWN_MX_DOMAINS:
        return KNOWN_MX_DOMAINS[domain]
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]

    try:
        proc = subprocess.run(
            ["dig", "+short", "MX", domain],
            capture_output=True,
            text=True,
            timeout=8
        )
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        mx_servers = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                mx_servers.append(parts[1].rstrip("."))
            elif len(parts) == 1:
                mx_servers.append(parts[0].rstrip("."))
        if mx_servers:
            _MX_CACHE[domain] = mx_servers
            return mx_servers
    except Exception as e:
        print(f"[EmailVerifier] DNS lookup error for {domain}: {e}")

    # Fallback to socket gethostbyname
    try:
        socket.gethostbyname(domain)
        fallback = [f"mail.{domain}"]
        _MX_CACHE[domain] = fallback
        return fallback
    except Exception:
        pass

    return []

def verify_email(email: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Verifies an email address without sending an actual message.
    Returns: (is_valid, reason, metadata)
    """
    clean_email = email.strip().lower()

    # 1. Syntax validation
    if not EMAIL_REGEX.match(clean_email):
        return False, "Invalid email syntax format", {"email": clean_email}

    username, domain = clean_email.split("@", 1)

    # 2. Check for disposable domain
    if domain in DISPOSABLE_DOMAINS:
        return False, "Disposable/temporary email provider", {"domain": domain}

    # 3. Check for automated/no-reply accounts
    if username in ROLE_PREFIXES:
        return False, f"Automated role account ({username}@) does not accept inquiries", {"username": username}

    # 4. Check DNS MX records (The 'Address not found' filter)
    mx_records = get_mx_records(domain)
    if not mx_records:
        return False, f"Domain '{domain}' has no active Mail Exchange (MX) records (Address not found / Dead domain)", {
            "domain": domain,
            "mx_found": False
        }

    return True, "Valid deliverable domain with active mail servers", {
        "email": clean_email,
        "domain": domain,
        "mx_servers": mx_records[:2],
        "is_role_based": username in {"careers", "hiring", "jobs", "hr", "recruiting", "founder", "founders"}
    }

def clean_email_batch(emails: List[str]) -> Dict[str, Any]:
    """
    Takes a raw list of email addresses, cleans, dedupes, and verifies them all.
    Separates into 'ready_to_send', 'risky', and 'dead_bounced'.
    """
    unique_emails = list(dict.fromkeys([e.strip() for e in emails if e and "@" in e]))
    ready_to_send = []
    dead_bounced = []
    risky = []

    print(f"[EmailVerifier] Verifying batch of {len(unique_emails)} email addresses...")

    for em in unique_emails:
        is_valid, reason, meta = verify_email(em)
        item = {
            "email": em,
            "reason": reason,
            "meta": meta
        }
        if is_valid:
            if meta.get("is_role_based"):
                risky.append(item)
            else:
                ready_to_send.append(item)
        else:
            dead_bounced.append(item)

    return {
        "total_analyzed": len(unique_emails),
        "deliverable_count": len(ready_to_send),
        "dead_bounced_count": len(dead_bounced),
        "risky_count": len(risky),
        "ready_to_send": ready_to_send,
        "dead_bounced": dead_bounced,
        "risky": risky
    }
