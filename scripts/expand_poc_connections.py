import time
import os
import re
import sys
import sqlite3
from pathlib import Path
from typing import Dict, Any, List
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.exclusion_filter import is_excluded

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/expand-poc"
CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"

def init_poc_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS poc_connection_requests (
            id TEXT PRIMARY KEY,
            name TEXT,
            headline TEXT,
            status TEXT DEFAULT 'pending',
            note TEXT,
            sent_at DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def send_poc_connect_request(page, username: str, full_name: str, headline: str) -> bool:
    """Navigates to 2nd-degree POC profile and sends connection request with tailored note."""
    first_name = full_name.split()[0] if full_name else "there"
    profile_url = f"https://www.linkedin.com/in/{username}/"
    page.goto(profile_url, wait_until="commit", timeout=30000)
    page.wait_for_timeout(3000)

    # Remove floating overlay banner that may intercept clicks
    page.evaluate('() => document.querySelectorAll(".i87lly, aside, .msg-overlay-list-bubble").forEach(el => el.remove())')

    # Look for Connect button
    connect_btn = page.locator('main button:has-text("Connect"), [aria-label*="Invite ' + first_name + ' to connect"]').first
    
    if not connect_btn.is_visible():
        # Check inside 'More' dropdown
        more_btn = page.locator('main button:has-text("More")').first
        if more_btn.is_visible():
            more_btn.click(force=True)
            page.wait_for_timeout(800)
            connect_btn = page.locator('.artdeco-dropdown__item:has-text("Connect"), button:has-text("Connect")').first

    if not connect_btn.is_visible():
        print(f"[POCExpand] No connect button available for {full_name}.")
        return False

    connect_btn.click(force=True)
    page.wait_for_timeout(1500)

    # Check for "Add a note" modal
    add_note_btn = page.locator('button:has-text("Add a note"), button[aria-label*="Add a note"]').first
    if add_note_btn.is_visible():
        add_note_btn.click()
        page.wait_for_timeout(800)

        note_box = page.locator('textarea[name="message"], textarea#custom-message').first
        if note_box.is_visible():
            note_text = (
                f"Hi {first_name}, saw your work in product/tech. "
                f"I bring 1 yr exp in product & analytics (immediate 15-day joiner) and would love to connect and follow your team's updates! "
                f"CV: {CV_LINK}"
            )
            # Ensure within 300 char LinkedIn limit
            note_text = note_text[:300]
            note_box.fill(note_text)
            page.wait_for_timeout(500)

    # Click Send invitation
    send_inv_btn = page.locator('button:has-text("Send"), button[aria-label*="Send invitation"]').first
    if send_inv_btn.is_visible() and send_inv_btn.is_enabled():
        send_inv_btn.click()
        page.wait_for_timeout(2000)
        print(f"[POCExpand] -> Successfully sent connection request + note to {full_name} ({headline[:40]})")
        return True

    return False

def expand_poc_network(target_keywords: List[str] = None, daily_limit: int = 20):
    if target_keywords is None:
        target_keywords = ['"Technical Recruiter"', '"Product Manager"', 'NSUT "Product"']

    init_poc_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    sent_count = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        for kw in target_keywords:
            if sent_count >= daily_limit:
                break

            search_url = f"https://www.linkedin.com/search/results/people/?keywords={kw}&network=%5B%22S%22%5D"
            print(f"\n[POCExpand] Searching 2nd-degree POCs for: {kw}")
            page.goto(search_url, wait_until="commit", timeout=30000)
            page.wait_for_timeout(3500)

            # Extract 2nd degree cards
            cards = page.locator('main a[href*="/in/"]:has-text("• 2nd")').all()
            print(f"[POCExpand] Found {len(cards)} 2nd-degree connection cards.")

            for card in cards:
                if sent_count >= daily_limit:
                    break

                try:
                    card_text = card.inner_text().strip()
                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                    if not lines:
                        continue

                    name_raw = lines[0].replace("• 2nd", "").strip()
                    headline = lines[1] if len(lines) > 1 else ""

                    href = card.get_attribute("href") or ""
                    m = re.search(r"/in/([^/?#]+)", href)
                    if not m:
                        continue
                    username = m.group(1)

                    # Exclusions E1, E2, E3
                    exclusion = is_excluded(name=name_raw, headline=headline, card_text=card_text)
                    if exclusion:
                        print(f"[POCExpand] [EXCLUDED {exclusion}] {name_raw}")
                        continue

                    # Check already contacted
                    cur.execute('SELECT status FROM poc_connection_requests WHERE id = ?', (username,))
                    if cur.fetchone():
                        continue

                    # Send request
                    success = send_poc_connect_request(page, username, name_raw, headline)
                    if success:
                        sent_count += 1
                        cur.execute('''
                            INSERT OR REPLACE INTO poc_connection_requests (id, name, headline, status, sent_at)
                            VALUES (?, ?, ?, 'sent', CURRENT_TIMESTAMP)
                        ''', (username, name_raw, headline))
                        conn.commit()

                        # Humanized delay (30-45s) between requests
                        time.sleep(35)

                        # Back to search
                        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)

                except Exception as e:
                    print(f"[POCExpand] Error on card: {e}")

        page.close()

    print(f"\n[POCExpand] Session complete. Total requests sent: {sent_count}")

if __name__ == "__main__":
    expand_poc_network(daily_limit=20)
