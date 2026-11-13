import os
import re
import sys
import time
import random
import sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.email_verifier import verify_email
from src.services.exclusion_filter import is_excluded

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/contact-scraper"

EMPLOYER_BLACKLIST = {"magicpin", "lifelong"}

def init_tables():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS connection_contact_scrapes (
            username TEXT PRIMARY KEY,
            email TEXT,
            phone TEXT,
            status TEXT,
            scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def scrape_connection_contacts(limit: int = 50):
    init_tables()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Select candidates from queue or outreach that haven't been scraped yet
    cur.execute('''
        SELECT id, name, headline FROM linkedin_connections_queue
        WHERE id NOT IN (SELECT username FROM connection_contact_scrapes)
        UNION
        SELECT id, name, headline FROM linkedin_connections_outreach
        WHERE id NOT IN (SELECT username FROM connection_contact_scrapes)
        LIMIT ?
    ''', (limit,))
    candidates = cur.fetchall()

    if not candidates:
        print("[ContactScraper] No unscraped candidates found in queue.")
        conn.close()
        return 0

    print(f"\n[ContactScraper] Starting email extraction for {len(candidates)} 1st-degree connections...")

    extracted_count = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        for idx, (username, full_name, headline) in enumerate(candidates, 1):
            if is_excluded(name=full_name, headline=headline or ""):
                cur.execute('''
                    INSERT OR REPLACE INTO connection_contact_scrapes (username, status)
                    VALUES (?, 'excluded')
                ''', (username,))
                conn.commit()
                continue

            profile_url = f"https://www.linkedin.com/in/{username}/"
            print(f"[{idx}/{len(candidates)}] Inspecting {full_name} ({username})...")

            try:
                page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)

                # Close chat bubbles that might block view
                page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')

                # Click Contact info anchor via JS
                clicked = page.evaluate('''() => {
                    const a = Array.from(document.querySelectorAll('a')).find(x => x.innerText.trim() === 'Contact info');
                    if (a) {
                        a.click();
                        return true;
                    }
                    return false;
                }''')

                if not clicked:
                    print(f"  -> No contact info link found for {username}")
                    cur.execute('''
                        INSERT OR REPLACE INTO connection_contact_scrapes (username, status)
                        VALUES (?, 'no_link')
                    ''', (username,))
                    conn.commit()
                    continue

                page.wait_for_timeout(2000)

                # Extract emails from mailto: links
                emails = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('a[href^="mailto:"]'))
                        .map(a => a.href.replace('mailto:', '').split('?')[0].trim().toLowerCase())
                        .filter(e => e.includes('@'));
                }''')

                # Extract phone numbers if present
                phones = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('section.ci-phone span, .pv-contact-info__ci-container--phone span'))
                        .map(s => s.innerText.trim())
                        .filter(p => p.length > 5);
                }''')

                primary_phone = phones[0] if phones else ""
                clean_email = None

                for em in emails:
                    domain = em.split("@")[-1].lower()
                    if any(b in em or b in domain for b in EMPLOYER_BLACKLIST):
                        continue
                    if "linkedin.com" in domain or "example.com" in domain:
                        continue
                    clean_email = em
                    break

                if clean_email:
                    print(f"  ✅ Extracted Email: {clean_email} (Phone: {primary_phone})")
                    cur.execute('''
                        INSERT OR REPLACE INTO connection_contact_scrapes (username, email, phone, status)
                        VALUES (?, ?, ?, 'found')
                    ''', (username, clean_email, primary_phone))

                    # Ingest into outreach_leads if valid MX
                    is_valid, reason, _ = verify_email(clean_email)
                    if is_valid:
                        # Extract company name from headline if possible
                        company = "Tech"
                        if headline and " at " in headline:
                            company = headline.split(" at ")[-1].split("|")[0].split("•")[0].strip()
                        elif headline and " @ " in headline:
                            company = headline.split(" @ ")[-1].split("|")[0].split("•")[0].strip()

                        cur.execute("SELECT id FROM outreach_leads WHERE email = ?", (clean_email,))
                        if not cur.fetchone():
                            cur.execute('''
                                INSERT INTO outreach_leads (name, email, company, role_target, status, created_at)
                                VALUES (?, ?, ?, 'Business / Product Analyst / Management', 'pending', CURRENT_TIMESTAMP)
                            ''', (full_name, clean_email, company[:50]))
                            print(f"  🎯 Queued into Cold Email Worker: {clean_email} ({company[:30]})")
                            extracted_count += 1
                    else:
                        print(f"  ⚠️ MX verification note for {clean_email}: {reason}")
                else:
                    print(f"  -> No public email listed for {username}")
                    cur.execute('''
                        INSERT OR REPLACE INTO connection_contact_scrapes (username, status)
                        VALUES (?, 'none_public')
                    ''', (username,))

                conn.commit()
                # Light human delay between profile views
                time.sleep(random.uniform(4, 7))

            except Exception as e:
                print(f"  ❌ Error on {username}: {e}")
                time.sleep(3)

        page.close()

    conn.close()
    print(f"\n[ContactScraper] Finished. Successfully added {extracted_count} verified emails into outreach pipeline.")
    return extracted_count

if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    scrape_connection_contacts(limit=count)
