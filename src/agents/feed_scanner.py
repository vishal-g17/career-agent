import time
import re
import urllib.parse
import sqlite3
from typing import List, Dict, Any
from pathlib import Path
from playwright.sync_api import sync_playwright
from ..services.cold_mailer import extract_emails_from_text

DB_PATH = Path.home() / ".linkedin_agent.db"
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/feed-job-scanner"

HIRING_TRIGGERS = [
    r"\bhiring\b",
    r"\bwe're hiring\b",
    r"\bwe are hiring\b",
    r"\blooking for an? (?:apm|pm|product manager|product analyst|tech product analyst|business analyst|fde|fdse|founder's office|chief of staff)\b",
    r"\bopen role\b",
    r"\bjoin our team\b",
    r"\bexpanding our (?:product|analytics|data) team\b",
    r"\bdm me\b",
    r"\bshare (?:your )?(?:cv|resume)\b",
    r"\bsend (?:your )?(?:resume|cv)\b",
    r"\bmail (?:your )?(?:resume|cv)\b"
]

TARGET_ROLES_RE = re.compile(
    r"\b(apm|associate product manager|product manager|product analyst|tech product analyst|data analyst|business analyst|operations analyst|growth analyst|category manager|associate category manager|forward deployed engineer|fde|fdse|founder's office|chief of staff|business operations|bizops|generalist|analyst)\b",
    re.IGNORECASE
)

FORM_URL_RE = re.compile(
    r"(https?://(?:forms\.gle|docs\.google\.com/forms|airtable\.com|notion\.site|jobs\.lever\.co|boards\.greenhouse\.io)[^\s\"\'>]+)",
    re.IGNORECASE
)

EMAIL_RE = re.compile(
    r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
)

def queue_email_lead(email: str, author_name: str, company: str, role: str) -> bool:
    """Inserts a freshly scraped email from a LinkedIn post directly into outreach_leads."""
    email_clean = email.strip().lower()
    # Filter junk or example emails
    if any(d in email_clean for d in ["example.com", "domain.com", "linkedin.com", "email.com"]):
        return False

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO outreach_leads (name, email, company, role_target, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        """, (author_name[:80], email_clean, company[:80] or "Startup/Hiring Company", role[:60] or "Product/Analytics"))
        changes = conn.total_changes
        conn.commit()
        conn.close()
        return changes > 0
    except Exception as e:
        print(f"[FeedScanner] Error inserting lead {email_clean}: {e}")
        return False

def _extract_leads_from_text(main_text: str, source_label: str) -> List[Dict[str, Any]]:
    post_blocks = re.split(r"(?:Feed post|Sort by:|Suggested|Promoted)", main_text)
    found_leads = []
    seen = set()

    for block in post_blocks:
        clean = block.strip()
        if len(clean) < 80:
            continue

        is_hiring = any(re.search(pat, clean, re.IGNORECASE) for pat in HIRING_TRIGGERS)
        has_role = TARGET_ROLES_RE.search(clean)

        if is_hiring or has_role:
            snippet_key = clean[:80]
            if snippet_key in seen:
                continue
            seen.add(snippet_key)

            lines = [l.strip() for l in clean.split("\n") if l.strip()]
            author = lines[0] if lines else "LinkedIn Connection"
            role_matched = has_role.group(0) if has_role else "Hiring Role"
            
            # Extract emails
            emails = EMAIL_RE.findall(clean)
            emails = [e for e in set(emails) if not any(j in e for j in ["example", "sentry", "w3.org"])]
            
            # Extract application forms
            forms = FORM_URL_RE.findall(clean)

            lead = {
                "source": source_label,
                "author": author,
                "role_matched": role_matched,
                "emails": emails,
                "forms": forms,
                "snippet": clean[:300]
            }
            found_leads.append(lead)

            # Auto-queue any extracted emails into outreach_leads!
            for em in emails:
                queued = queue_email_lead(em, author, "Hiring Lead via LinkedIn Post", role_matched)
                if queued:
                    print(f"  🎯 [Auto-Queued Email] {em} (Role: {role_matched} | Post Author: {author})")

            for fm in forms:
                print(f"  📋 [Application Form Found] {fm} (Role: {role_matched} | Post Author: {author})")

    return found_leads

def scan_personal_feed_and_hiring_posts() -> List[Dict[str, Any]]:
    """
    Connects to real logged-in LinkedIn session:
    1. Scans personal home feed (/feed/) for network hiring posts.
    2. Searches fresh LinkedIn hiring posts (/search/results/content/) for Product Analyst / BA / APM roles.
    3. Auto-extracts emails and queues them into the cold email engine (outreach_leads).
    """
    all_leads = []
    print("\n[FeedScanner] Connecting to LinkedIn Chrome session via Ridealong...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
        except Exception as conn_err:
            print(f"[FeedScanner] Could not connect to CDP: {conn_err}")
            return []

        # -------------------------------------------------------------
        # 1. SCAN PERSONAL HOME FEED
        # -------------------------------------------------------------
        try:
            print("[FeedScanner] Stage 1: Navigating to Personal Feed (https://www.linkedin.com/feed/)...")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)

            # Scroll 6 times
            for s in range(6):
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(900)

            main_text_feed = page.locator("main").inner_text()
            feed_leads = _extract_leads_from_text(main_text_feed, "Personal Feed")
            print(f"[FeedScanner] Stage 1 Complete: Found {len(feed_leads)} potential hiring posts in personal feed.")
            all_leads.extend(feed_leads)
        except Exception as fe:
            print(f"[FeedScanner] Error scanning personal feed: {fe}")

        # -------------------------------------------------------------
        # 2. SEARCH FRESH POSTS FOR PRODUCT & ANALYTICS ROLES
        # -------------------------------------------------------------
        search_queries = [
            'hiring ("product analyst" OR "business analyst")',
            'hiring ("APM" OR "associate product manager")'
        ]

        for query in search_queries:
            try:
                search_url = f"https://www.linkedin.com/search/results/content/?keywords={urllib.parse.quote(query)}&sortBy=%22date_posted%22"
                print(f"\n[FeedScanner] Stage 2: Searching LinkedIn Posts for: {query}...")
                page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)

                # Scroll 6 times to fetch fresh posts
                for s in range(6):
                    page.mouse.wheel(0, 1000)
                    page.wait_for_timeout(900)

                search_text = page.locator("main").inner_text()
                post_leads = _extract_leads_from_text(search_text, f"Post Search: {query}")
                print(f"[FeedScanner] Stage 2 Complete: Found {len(post_leads)} fresh hiring posts for '{query}'.")
                all_leads.extend(post_leads)
            except Exception as se:
                print(f"[FeedScanner] Error searching posts for '{query}': {se}")

        page.close()

    total_emails = sum(len(l["emails"]) for l in all_leads)
    total_forms = sum(len(l["forms"]) for l in all_leads)
    print(f"\n[FeedScanner] Total Finished: {len(all_leads)} posts analyzed | {total_emails} emails extracted & queued | {total_forms} application links found.")
    return all_leads

# Alias for backwards compatibility with master_autonomous_cycle
def scan_personal_feed_for_jobs():
    return scan_personal_feed_and_hiring_posts()

if __name__ == "__main__":
    scan_personal_feed_and_hiring_posts()
