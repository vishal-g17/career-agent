import time
import re
import random
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

JOB_LINK_RE = re.compile(
    r"(https?://(?:www\.|in\.)?linkedin\.com/jobs/view/[^\s\"\'>?]+)",
    re.IGNORECASE
)


def _extract_post_bodies_no_comments(page) -> str:
    """
    Extracts ONLY post body text from the DOM while explicitly stripping any
    rendered comment sections, comment boxes, or commenter replies (per user rule:
    extract email IDs ONLY from post body, NEVER from comments).
    """
    js = """
    () => {
        const cloneRoot = document.querySelector('main');
        if (!cloneRoot) return '';
        const blocks = [];
        const postEls = cloneRoot.querySelectorAll(
            'div.feed-shared-update-v2, div[data-urn*="urn:li:activity"], article'
        );
        if (postEls.length > 0) {
            for (const post of postEls) {
                const clone = post.cloneNode(true);
                clone.querySelectorAll(
                    '.comments-comments-list, .comments-comment-item, .comments-comment-box, ' +
                    '[class*="comments-container"], [class*="comment-item"], [class*="social-details-social-activity"]'
                ).forEach(el => el.remove());
                const txt = (clone.innerText || '').trim();
                if (txt.length > 60) blocks.push('Feed post\\n' + txt);
            }
            return blocks.join('\\n\\n');
        }
        return cloneRoot.innerText || '';
    }
    """
    try:
        return page.evaluate(js)
    except Exception:
        return page.locator("main").inner_text()


def queue_discovered_job_url(url: str, role_hint: str, author: str) -> bool:
    clean_url = url.split("?")[0].rstrip("/")
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        job_id = f"feed_{abs(hash(clean_url))}"
        c.execute("SELECT 1 FROM jobs WHERE url LIKE ? LIMIT 1", (f"%{clean_url[-25:]}%",))
        if c.fetchone():
            conn.close()
            return False
        c.execute(
            """
            INSERT OR IGNORE INTO jobs (id, title, company, location, url, status, match_score, created_at)
            VALUES (?, ?, ?, 'India', ?, 'new', 85, CURRENT_TIMESTAMP)
            """,
            (job_id, role_hint[:80] or "Product / Analytics Role", f"Post by {author[:40]}", clean_url),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


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
            
            # Extract emails strictly from post body (comments already stripped from DOM)
            emails = EMAIL_RE.findall(clean)
            emails = [e for e in set(emails) if not any(j in e for j in ["example", "sentry", "w3.org"])]
            
            # Extract application forms & direct LinkedIn job links
            forms = FORM_URL_RE.findall(clean)
            job_links = JOB_LINK_RE.findall(clean)

            lead = {
                "source": source_label,
                "author": author,
                "role_matched": role_matched,
                "emails": emails,
                "forms": forms,
                "job_links": job_links,
                "snippet": clean[:300]
            }
            found_leads.append(lead)

            # Auto-queue any extracted emails into outreach_leads!
            for em in emails:
                queued = queue_email_lead(em, author, "Hiring Lead via LinkedIn Post", role_matched)
                if queued:
                    print(f"  🎯 [Auto-Queued Email from Post Body] {em} (Role: {role_matched} | Post Author: {author})")

            for jl in job_links:
                if queue_discovered_job_url(jl, role_matched, author):
                    print(f"  💼 [Queued Job Link from Post] {jl}")

            for fm in forms:
                print(f"  📋 [Application Form Found] {fm} (Role: {role_matched} | Post Author: {author})")
                try:
                    from ..services.ats_filler import fill_ats_application
                    res = fill_ats_application(fm, role_title=role_matched, auto_submit=True)
                    if res.get("submitted") and res.get("confirmed"):
                        print(f"  ✅ [Form Auto-Submitted & Confirmed] {fm}")
                except Exception:
                    pass

    return found_leads

def scan_personal_feed_and_hiring_posts(duration_minutes: float = 10.0) -> List[Dict[str, Any]]:
    """
    Connects to real logged-in LinkedIn session for ~duration_minutes:
    1. Scans personal home feed (/feed/) for network hiring posts (post body ONLY, never comments).
    2. Searches fresh LinkedIn hiring posts (/search/results/content/) across target queries.
    3. Auto-extracts emails -> outreach_leads, queues job URLs -> jobs, and fills confirmed ATS forms.
    """
    all_leads = []
    deadline = time.time() + max(60.0, duration_minutes * 60.0)
    print(f"\n[FeedScanner] Starting ~{duration_minutes:.1f}-min Feed & Post Hunt (Post Body Only — Comments Ignored)...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0]
            page = context.new_page()
        except Exception as conn_err:
            print(f"[FeedScanner] Could not connect to CDP: {conn_err}")
            return []

        try:
            # 1. SCAN PERSONAL HOME FEED
            from ..services.linkedin_control import sync_control_with_cloud, is_linkedin_logged_out, set_linkedin_enabled
            print("[FeedScanner] Stage 1: Navigating to Personal Feed (https://www.linkedin.com/feed/)...")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            if is_linkedin_logged_out(page):
                set_linkedin_enabled(False, reason="linkedin_logged_out", updated_by="feed_scanner")
                return []

            for _ in range(random.randint(7, 13)):
                if time.time() >= deadline or not sync_control_with_cloud().get("linkedin_enabled", True):
                    break
                page.mouse.wheel(0, random.randint(720, 1450))
                page.wait_for_timeout(random.randint(2400, 5800))

            main_text_feed = _extract_post_bodies_no_comments(page)
            feed_leads = _extract_leads_from_text(main_text_feed, "Personal Feed")
            print(f"[FeedScanner] Stage 1 Complete: Found {len(feed_leads)} hiring posts in personal feed.")
            all_leads.extend(feed_leads)

            # 2. SEARCH FRESH POSTS FOR PRODUCT & ANALYTICS ROLES UNTIL DEADLINE
            search_queries = [
                'hiring ("product analyst" OR "business analyst")',
                'hiring ("APM" OR "associate product manager")',
                'hiring ("founder\'s office" OR "product manager") ("send resume" OR "mail" OR "@")',
                '("hiring" OR "open role") ("data analyst" OR "operations analyst") India',
            ]
            random.shuffle(search_queries)

            for query in search_queries:
                if time.time() >= deadline or not sync_control_with_cloud().get("linkedin_enabled", True):
                    break
                try:
                    search_url = f"https://www.linkedin.com/search/results/content/?keywords={urllib.parse.quote(query)}&sortBy=%22date_posted%22"
                    print(f"\n[FeedScanner] Stage 2: Searching LinkedIn Posts for: {query}...")
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(random.randint(2800, 5200))
                    if is_linkedin_logged_out(page):
                        set_linkedin_enabled(False, reason="linkedin_logged_out", updated_by="feed_scanner")
                        return all_leads

                    for _ in range(random.randint(6, 12)):
                        if time.time() >= deadline or not sync_control_with_cloud().get("linkedin_enabled", True):
                            break
                        page.mouse.wheel(0, random.randint(700, 1400))
                        page.wait_for_timeout(random.randint(2600, 6200))

                    search_text = _extract_post_bodies_no_comments(page)
                    post_leads = _extract_leads_from_text(search_text, f"Post Search: {query}")
                    print(f"[FeedScanner] Found {len(post_leads)} fresh hiring posts for '{query}'.")
                    all_leads.extend(post_leads)
                except Exception as se:
                    print(f"[FeedScanner] Error searching posts for '{query}': {se}")

            # Pace politely if finished slightly before 10-min window
            remaining = deadline - time.time()
            if remaining > 5:
                # Continue light feed scrolling so full 10-min window is utilized productively
                try:
                    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
                    while time.time() < deadline:
                        page.mouse.wheel(0, 900)
                        time.sleep(min(12.0, max(1.0, deadline - time.time())))
                    extra = _extract_leads_from_text(_extract_post_bodies_no_comments(page), "Personal Feed Deep Scroll")
                    all_leads.extend(extra)
                except Exception:
                    pass
        finally:
            try:
                page.close()
            except Exception:
                pass

    total_emails = sum(len(l["emails"]) for l in all_leads)
    total_forms = sum(len(l["forms"]) for l in all_leads)
    print(f"\n[FeedScanner] Total Finished: {len(all_leads)} posts analyzed | {total_emails} post-body emails extracted & queued | {total_forms} forms found.")
    try:
        from scripts.master_autonomous_cycle import sync_new_outreach_leads_to_cloud_vm
        conn_sync = sqlite3.connect(DB_PATH)
        sync_new_outreach_leads_to_cloud_vm(conn_sync)
        conn_sync.close()
    except Exception:
        pass
    return all_leads

# Alias for backwards compatibility with master_autonomous_cycle
def scan_personal_feed_for_jobs(duration_minutes: float = 10.0):
    return scan_personal_feed_and_hiring_posts(duration_minutes=duration_minutes)

if __name__ == "__main__":
    scan_personal_feed_and_hiring_posts()
