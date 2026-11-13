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
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/li-connections-agent"

def init_connections_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS linkedin_connections_outreach (
            id TEXT PRIMARY KEY,
            name TEXT,
            headline TEXT,
            status TEXT DEFAULT 'pending',
            exclusion_reason TEXT,
            last_message TEXT,
            sent_at DATETIME
        )
    ''')
    conn.commit()
    conn.close()

import urllib.parse

def send_connection_referral_pitch(page, username: str, first_name: str) -> bool:
    """Opens direct messaging composer, inspects chat history, and sends message if appropriate."""
    compose_url = f"https://www.linkedin.com/messaging/compose/?recipient={username}"
    try:
        page.goto(compose_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
    except Exception as ge:
        print(f"[LIOutreach] Navigation note for {username}: {ge}, trying fallback...")
        try:
            page.goto(compose_url, wait_until="commit", timeout=15000)
            page.wait_for_timeout(2500)
        except Exception as fe:
            print(f"[LIOutreach] Failed to reach composer for {username}: {fe}")
            return False

    # 1. Inspect past chat history (User rule: DO NOT skip; tailor natural follow-up)
    has_past_chat = False
    past_events = page.locator('.msg-s-message-list__event, .msg-s-event-listitem').all()
    if past_events and len(past_events) > 1:
        has_past_chat = True
        print(f"[LIOutreach] Reconnecting with {first_name} (past conversation history detected).")

    # 2. Check composer
    composer = page.locator('div[role="textbox"], .msg-form__contenteditable').first
    if not composer.is_visible():
        print(f"[LIOutreach] Composer not visible for {first_name}.")
        return False

    # 2b. Attach CV PDF (User rule: attach CV PDF directly, NO portfolio link)
    pdf_path = "/Users/admin/linkedin-multiagent/data/resume.pdf"
    file_input = page.locator('input.msg-form__attachment-upload-input[accept*=".pdf"], .msg-form input[type="file"]').last
    try:
        if file_input.count() > 0:
            file_input.set_input_files(pdf_path)
            page.wait_for_timeout(2500)
            print(f"[LIOutreach] Attached CV PDF for {first_name}")
    except Exception as e:
        print(f"[LIOutreach] CV attach note for {first_name}: {e}")

    # 3. Message Template (Human, Authentic, Layoff/Restructuring Sympathy & Past Stints Reframed)
    cv_link = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"
    if has_past_chat:
        message_text = (
            f"Hey {first_name}, following up with a quick update! Following a recent team-wide layoff / startup restructuring, I'm actively back in the market for APM / Product Analyst / Business Analyst roles (1 yr full-time exp, NSUT Delhi, immediate 15-day joiner).\n\n"
            f"Sharing my updated CV here (Drive link + attached PDF below). In case your team has any open roles or if you know of any open opportunities, would deeply appreciate any referral or pointer!\n"
            f"CV: {cv_link}"
        )
    else:
        message_text = (
            f"Hey {first_name}, hope you're doing well!\n\n"
            f"I'm reaching out as I was recently affected by a team-wide layoff / startup restructuring and am actively looking for my next chapter. To share quick context: I bring 1 yr of full-time experience across product & analytics (B.Tech from NSUT Delhi). My prior stints were an off-campus venture with a founder followed by my campus placement, and with the recent restructuring, I'm available to join immediately (15-day notice).\n\n"
            f"Hands-on, I've worked on merchant conversion (reducing drop-offs 10–12% to 4–6%), pricing telemetry, and automated analytics pipelines that eliminated hours of manual ops.\n\n"
            f"Actively exploring APM / Product Analyst / Business Analyst roles and sharing my CV for quick reference (Drive link + attached PDF below). Would love to know if your team has open roles or if you could point me toward the right POC!\n"
            f"CV: {cv_link}\n\n"
            f"Really appreciate any guidance or referral!"
        )

    composer.click()
    page.keyboard.type(message_text, delay=20)
    page.wait_for_timeout(1000)

    # 4. Click Send
    send_btn = page.locator('button.msg-form__send-button, button:has-text("Send")').first
    if send_btn.is_visible() and send_btn.is_enabled():
        send_btn.click()
        page.wait_for_timeout(2500)
        print(f"[LIOutreach] -> Sent message to {first_name}!")
        return True
    else:
        page.keyboard.press("Meta+Enter")
        page.wait_for_timeout(2500)
        print(f"[LIOutreach] -> Dispatched message to {first_name} via shortcut!")
        return True

def run_connections_campaign(target_keywords: List[str] = None, batch_limit: int = 25):
    """Searches 1st-degree connections across segments with pagination, applies E1-E3 exclusions, and reaches out."""
    if target_keywords is None:
        target_keywords = ["product", "recruiter", "talent acquisition", "engineering manager", "NSUT", "founder"]

    init_connections_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM linkedin_connections_outreach WHERE status='sent'")
    total_previously_sent = cur.fetchone()[0]

    print("\n================================================================================")
    print(f"[LIOutreach] MASTER OUTREACH LAUNCH: Previously Sent Total: {total_previously_sent} / 4,000+ Connections")
    print(f"[LIOutreach] Target Batch Size for this run: {batch_limit}")
    print("================================================================================\n")

    sent_count = 0
    excluded_count = 0

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        for kw in target_keywords:
            if sent_count >= batch_limit:
                break

            for page_num in range(1, 10):  # Paginate pages 1 to 9 per segment
                if sent_count >= batch_limit:
                    break

                q_kw = urllib.parse.quote(kw)
                search_url = f"https://www.linkedin.com/search/results/people/?keywords={q_kw}&network=%5B%22F%22%5D&page={page_num}"
                print(f"\n[LIOutreach] Scanning Keyword '{kw}' (Page {page_num})...")
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(3000)
                except Exception as se:
                    print(f"[LIOutreach] Search load note: {se}")

                cards = page.locator('main a[href*="/in/"]:has-text("• 1st")').all()
                if not cards:
                    print(f"[LIOutreach] No more 1st-degree cards for '{kw}' on page {page_num}.")
                    break

                print(f"[LIOutreach] Discovered {len(cards)} 1st-degree connection cards on page {page_num}.")

                # Extract candidates upfront before navigating away
                candidates = []
                for card in cards:
                    try:
                        card_text = card.inner_text().strip()
                        lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                        if not lines:
                            continue
                        name_raw = lines[0].replace("• 1st", "").strip()
                        first_name = name_raw.split()[0] if name_raw else "there"
                        headline = lines[1] if len(lines) > 1 else ""
                        href = card.get_attribute("href") or ""
                        m = re.search(r"/in/([^/?#]+)", href)
                        if not m:
                            continue
                        username = m.group(1)
                        candidates.append({
                            "username": username,
                            "name_raw": name_raw,
                            "first_name": first_name,
                            "headline": headline,
                            "card_text": card_text
                        })
                    except Exception as ce:
                        continue

                for cand in candidates:
                    if sent_count >= batch_limit:
                        break

                    username = cand["username"]
                    name_raw = cand["name_raw"]
                    first_name = cand["first_name"]
                    headline = cand["headline"]

                    # Check exclusions E1, E2, E3
                    reason = is_excluded(name=name_raw, headline=headline, card_text=cand["card_text"])
                    if reason:
                        excluded_count += 1
                        print(f"[LIOutreach] [EXCLUDED {reason}] {name_raw}: {headline[:50]}")
                        continue

                    # Check if already messaged
                    cur.execute('SELECT status FROM linkedin_connections_outreach WHERE id = ?', (username,))
                    existing = cur.fetchone()
                    if existing and existing[0] == 'sent':
                        continue

                    print(f"\n[LIOutreach] [#{total_previously_sent + sent_count + 1} / 4000+] Qualifying: {name_raw} ({headline[:50]}...)")
                    
                    success = send_connection_referral_pitch(page, username, first_name)
                    if success:
                        sent_count += 1
                        cur.execute('''
                            INSERT OR REPLACE INTO linkedin_connections_outreach (id, name, headline, status, sent_at)
                            VALUES (?, ?, ?, 'sent', CURRENT_TIMESTAMP)
                        ''', (username, name_raw, headline))
                        conn.commit()

                        # Safe humanized delay (30s)
                        time.sleep(30)

        page.close()

    cur.execute("SELECT count(*) FROM linkedin_connections_outreach WHERE status='sent'")
    final_total = cur.fetchone()[0]
    print("\n================================================================================")
    print(f"[LIOutreach] Batch Finished!")
    print(f"[LIOutreach] Newly Sent This Run: {sent_count} | Total Messaged Overall: {final_total} / 4,000+")
    print(f"[LIOutreach] Excluded connections filtered out: {excluded_count}")
    print("================================================================================\n")

if __name__ == "__main__":
    run_connections_campaign(batch_limit=25)
