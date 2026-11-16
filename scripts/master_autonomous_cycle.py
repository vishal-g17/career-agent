import time
import os
import re
import sys
import random
import sqlite3
import subprocess
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.easy_apply import apply_to_easy_apply_job
from src.services.naukri_filler import apply_to_naukri_job
from src.agents.feed_scanner import scan_personal_feed_for_jobs
from src.services.exclusion_filter import is_excluded, has_forbidden_company
from src.services.linkedin_control import (
    sync_control_with_cloud,
    set_linkedin_enabled,
    is_linkedin_logged_out,
)
from scripts.scrape_connection_emails import scrape_connection_contacts

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/master-cycle"
CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"
PDF_PATH = str(PROJECT_ROOT / "data" / "resume.pdf")

def init_master_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS linkedin_connections_queue (
            id TEXT PRIMARY KEY,
            name TEXT,
            headline TEXT,
            exclusion_reason TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS linkedin_connections_outreach (
            id TEXT PRIMARY KEY,
            name TEXT,
            headline TEXT,
            status TEXT DEFAULT 'pending',
            exclusion_reason TEXT,
            sent_at DATETIME,
            followup_sent_at DATETIME
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS poc_connection_requests (
            id TEXT PRIMARY KEY,
            name TEXT,
            headline TEXT,
            status TEXT DEFAULT 'requested',
            note TEXT,
            requested_at DATETIME,
            accepted_at DATETIME,
            cold_msg_sent_at DATETIME
        )
    ''')
    conn.commit()
    conn.close()

# -------------------------------------------------------------
# 0. QUEUE REFILLER (Scrapes connections without search rate-limits)
# -------------------------------------------------------------
def refill_connections_queue(page, conn, min_needed: int = 50, max_scrolls: int = 80) -> int:
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM linkedin_connections_queue WHERE status='pending'")
    pending_count = cur.fetchone()[0]
    if pending_count >= min_needed:
        return 0

    needed_to_add = min_needed - pending_count
    print(f"[Queue:Refill] Queue low ({pending_count} pending). Dynamically replenishing at least {needed_to_add} fresh connections...")
    try:
        js_extract = """
        () => {
            const results = [];
            const seen = new Set();
            const links = Array.from(document.querySelectorAll("main a[href*='/in/']"));
            for (const a of links) {
                const href = a.href || "";
                const parts = href.split("/in/");
                if (parts.length < 2) continue;
                const uname = parts[1].split("/")[0].split("?")[0].trim();
                if (!uname || uname === "vshal-g007" || seen.has(uname)) continue;

                const linkTxt = (a.innerText || "").trim();
                if (!linkTxt || linkTxt.length < 3) continue;

                let row = a;
                let rowTxt = "";
                for (let d = 0; d < 5 && row; d++) {
                    const t = (row.innerText || "").trim();
                    if ((t.includes("Connected on") || t.includes("Message") || t.includes("• 1st")) && t.length < 650) {
                        rowTxt = t;
                        break;
                    }
                    row = row.parentElement;
                }
                if (!rowTxt) continue;

                const lines = rowTxt.split("\\n").map(l => l.trim()).filter(Boolean);
                const name = (lines[0] || linkTxt.split("\\n")[0].trim() || uname).replace("• 1st", "").trim();
                let headline = "";
                for (let i = 1; i < lines.length; i++) {
                    const ln = lines[i];
                    if (ln.startsWith("•") || ln.startsWith("Connected on") || ln === "Message" || ln.includes("mutual connection")) continue;
                    headline = ln;
                    break;
                }

                seen.add(uname);
                results.push({ username: uname, name: name, headline: headline });
            }
            return results;
        }
        """

        def _ingest_extracted(extracted_list) -> int:
            newly_added = 0
            for item in extracted_list:
                uname = item["username"]
                name = item["name"]
                headline = item["headline"]

                cur.execute("SELECT id FROM linkedin_connections_outreach WHERE id=?", (uname,))
                if cur.fetchone():
                    continue
                cur.execute("SELECT id FROM linkedin_connections_queue WHERE id=?", (uname,))
                if cur.fetchone():
                    continue

                exclusion = is_excluded(name=name, headline=headline)
                status = 'excluded' if exclusion else 'pending'
                excl_val = exclusion if exclusion else None
                cur.execute('''
                    INSERT OR REPLACE INTO linkedin_connections_queue (id, name, headline, exclusion_reason, status)
                    VALUES (?, ?, ?, ?, ?)
                ''', (uname, name, headline, excl_val, status))
                if not exclusion:
                    newly_added += 1
            conn.commit()
            return newly_added

        added = 0

        # Step 1: Check top of "Recently added" connections first so newly accepted 1st-degree connections are queued immediately
        try:
            page.goto("https://www.linkedin.com/mynetwork/invite-connect/connections/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2600)
            added += _ingest_extracted(page.evaluate(js_extract))
        except Exception:
            pass

        # Step 2: Direct pagination into 1st-degree people search (`network=["F"]`) to reach unmessaged connections across all 4,250+ connections in seconds!
        if added < needed_to_add:
            cur.execute("SELECT (SELECT COUNT(*) FROM linkedin_connections_outreach) + (SELECT COUNT(*) FROM linkedin_connections_queue)")
            total_processed = cur.fetchone()[0] or 0
            base_page = max(1, min(90, (total_processed // 15) + 1))
            candidate_pages = list(range(base_page, min(100, base_page + 12))) + random.sample(range(15, 98), k=6)

            for pg_num in candidate_pages:
                if added >= needed_to_add:
                    break
                p_url = f"https://www.linkedin.com/search/results/people/?network=%5B%22F%22%5D&page={pg_num}"
                try:
                    page.goto(p_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2400)
                    batch_new = _ingest_extracted(page.evaluate(js_extract))
                    added += batch_new
                    print(f"[Queue:Refill] 1st-degree page {pg_num}: +{batch_new} ({added}/{needed_to_add} queued).")
                except Exception as pe:
                    if "429" in str(pe):
                        time.sleep(60)
                        break

        print(f"[Queue:Refill] Replenished queue with {added} fresh eligible connections.")
        return added
    except Exception as e:
        print(f"[Queue:Refill] Error: {e}")
        return 0

# -------------------------------------------------------------
# 1. FULL-PROFILE EXPERIENCE & COMPANY EXCLUSION GUARD
# -------------------------------------------------------------
JS_EXTRACT_PROFILE_BODY_ONLY = """() => {
    const root = document.querySelector('main') || document.body;
    const clone = root.cloneNode(true);
    clone.querySelectorAll('aside').forEach(el => el.remove());
    clone.querySelectorAll('section').forEach(sec => {
        const txt = (sec.innerText || '').slice(0, 140).toLowerCase();
        if (
            txt.includes('more profiles for you') ||
            txt.includes('people you may know') ||
            txt.includes('people also viewed') ||
            txt.includes('you might like') ||
            txt.includes('explore premium')
        ) {
            sec.remove();
        }
    });
    const aria = Array.from(clone.querySelectorAll('[aria-label], img[alt]'))
        .map(e => (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('alt') || ''))
        .join(' ');
    return (clone.innerText || '') + ' ' + aria;
}"""


def _mark_linkedin_user_excluded(username: str, reason: str) -> None:
    try:
        db_c = sqlite3.connect(DB_PATH, timeout=15.0)
        db_c.execute(
            "UPDATE linkedin_connections_queue SET status='excluded', exclusion_reason=? WHERE id=?",
            (reason, username),
        )
        db_c.execute(
            "UPDATE linkedin_connections_outreach SET status='excluded', exclusion_reason=? WHERE id=?",
            (reason, username),
        )
        db_c.commit()
        db_c.close()
    except Exception:
        pass


def verify_linkedin_profile_safe(page, username: str) -> bool:
    """
    Fail-closed full LinkedIn profile scanner:
    Opens /in/{username}/, scrolls so Top Card + Experience render, and checks the
    candidate's full Experience & employer badges against FORBIDDEN_COMPANIES.
    Catches people who omit their employer name from their 1-line headline!
    """
    profile_url = f"https://www.linkedin.com/in/{username}/"
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=22000)
        page.wait_for_timeout(2600)
        if is_linkedin_logged_out(page):
            _handle_linkedin_logout(page)
            return False
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(1300)
        profile_txt = page.evaluate(JS_EXTRACT_PROFILE_BODY_ONLY) or ""
        if len(profile_txt.strip()) < 80:
            print(f"[ExclusionGuard] ⚠️ Profile text unverified for {username}; failing closed (skipping).")
            return False
        forbidden_hit = has_forbidden_company(profile_txt)
        if forbidden_hit:
            print(f"[ExclusionGuard] 🛑 BLOCKED {username}: matched restricted employer in full profile/Experience!")
            _mark_linkedin_user_excluded(username, forbidden_hit)
            return False
        return True
    except Exception as pe:
        print(f"[ExclusionGuard] ⚠️ Could not verify profile {username} ({pe}); failing closed.")
        return False


# -------------------------------------------------------------
# 1b. TURBO 1ST-DEGREE MESSAGE SENDER
# -------------------------------------------------------------
def turbo_send_1st_degree(page, username: str, first_name: str) -> bool:
    """Fast, humanized direct pitch with attached PDF CV and Drive link reusing page."""
    # 0. Fast DB check BEFORE browser navigation so duplicates/exclusions never even open compose
    try:
        db_conn = sqlite3.connect(DB_PATH)
        db_cur = db_conn.cursor()
        db_cur.execute("SELECT status FROM linkedin_connections_outreach WHERE id = ?", (username,))
        existing = db_cur.fetchone()
        db_conn.close()
        if existing and existing[0] == "excluded":
            print(f"[ExclusionGuard] 🛑 {username} already marked excluded; skipping.")
            return False
        if existing and existing[0] == "sent":
            print(f"[Cycle:1stDegree] Prior pitch already sent to {username}; skipping duplicate.")
            return False
    except Exception:
        pass

    # MANDATORY FULL-PROFILE EXPERIENCE CHECK (prevents messaging anyone at restricted employers even when omitted from headline!)
    if not verify_linkedin_profile_safe(page, username):
        return False

    compose_url = f"https://www.linkedin.com/messaging/compose/?recipient={username}"
    try:
        try:
            page.goto(compose_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            if is_linkedin_logged_out(page):
                _handle_linkedin_logout(page)
                return False
        except Exception as e:
            err_str = str(e)
            if "ERR_HTTP_RESPONSE_CODE_FAILURE" in err_str or "429" in err_str:
                # Differentiate between a non-connection 429/404 and a real session block
                is_real_rate_limit = False
                try:
                    test_page = page.context.new_page()
                    resp = test_page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=8000)
                    if resp and resp.status == 429:
                        is_real_rate_limit = True
                    test_page.close()
                except Exception:
                    pass

                if is_real_rate_limit:
                    print(f"[Cycle:RateLimit] Global LinkedIn session 429 throttle detected. Cooling down for 150s...")
                    time.sleep(150)
                else:
                    print(f"[Cycle:1stDegree] {username} is not a 1st-degree connection or direct compose is closed. Skipping without cooldown.")
            return False

        # Check if OUR AGENT has sent a prior outreach pitch to this person
        has_agent_sent_prior_pitch = False
        other_person_replied = False

        # 1. Database check: did our agent record a sent message to this username?
        try:
            db_conn = sqlite3.connect(DB_PATH)
            db_cur = db_conn.cursor()
            db_cur.execute("SELECT status, sent_at FROM linkedin_connections_outreach WHERE id = ? AND status = 'sent'", (username,))
            db_record = db_cur.fetchone()
            db_conn.close()
            if db_record:
                has_agent_sent_prior_pitch = True
        except Exception:
            pass

        # 2. Live chat text check: inspect ONLY the active thread message list (NEVER 'main', which includes left-sidebar inbox previews!)
        chat_text = ""
        try:
            chat_container = page.locator('.msg-s-message-list, .msg-s-message-list-container').first
            if chat_container.count() > 0 and chat_container.is_visible():
                chat_text = chat_container.inner_text().lower()
                if any(sig in chat_text for sig in ["drive.google.com", "1i-4mk7lm3food_6e6gkfuyx9l_ikhk4q", "pricing telemetry", "merchant conversion"]):
                    has_agent_sent_prior_pitch = True
        except Exception:
            pass

        # 3. Check if recipient has already replied
        try:
            past_events = page.locator('.msg-s-message-list__event, .msg-s-event-listitem').all()
            if past_events:
                last_event_text = past_events[-1].inner_text().lower()
                if "vishal" not in last_event_text[:40] and len(last_event_text) > 10:
                    other_person_replied = True
        except Exception:
            pass

        # Composer check - wait up to 8s for React to mount the messaging box
        composer = page.locator('div[role="textbox"], .msg-form__contenteditable').first
        try:
            composer.wait_for(state="visible", timeout=8000)
        except Exception:
            return False

        # E4 Rule Directive: NEVER skip or exclude connections due to past chat or replies.
        # If agent previously sent a pitch -> send follow-up update.
        # Otherwise -> send standard primary pitch to all.

        if has_agent_sent_prior_pitch:
            print(f"[Cycle:1stDegree] Prior pitch already sent to {username}; skipping duplicate.")
            return False

        # Attach PDF CV
        file_input = page.locator('input.msg-form__attachment-upload-input[accept*=".pdf"], .msg-form input[type="file"]').last
        try:
            if file_input.count() > 0:
                file_input.set_input_files(PDF_PATH)
                page.wait_for_timeout(1500)
        except:
            pass

        # Send standard primary pitch with updated CV
        print(f"[Cycle:1stDegree] Sending standard primary pitch with updated CV to {first_name} ({username}).")
        msg = (
            f"Hey {first_name}, hope you're doing well!\n\n"
            f"I'm reaching out as I was recently affected by a team-wide layoff / startup restructuring and am actively looking for my next chapter. To share quick context: I bring 1 yr of full-time experience across product & analytics (B.Tech from NSUT Delhi). My prior stints were an off-campus venture with a founder followed by my campus placement, and with the recent restructuring, I'm available to join immediately (15-day notice).\n\n"
            f"Hands-on, I've worked on merchant conversion (reducing drop-offs 10–12% to 4–6%), pricing telemetry, and automated analytics pipelines that eliminated hours of manual ops.\n\n"
            f"Actively exploring APM / Product Analyst / Business Analyst roles and sharing my CV for quick reference (Drive link + attached PDF below). Would love to know if your team has open roles or if you could point me toward the right POC!\n"
            f"CV: {CV_LINK}\n\n"
            f"Really appreciate any guidance or referral!"
        )

        composer.click()
        page.wait_for_timeout(random.randint(600, 1800))
        page.keyboard.insert_text(msg)
        page.wait_for_timeout(random.randint(1200, 3400))

        # Click Send and verify dispatch
        send_btn = page.locator('button.msg-form__send-button, button[type="submit"]:has-text("Send"), button:has-text("Send")').first
        if send_btn.is_visible() and send_btn.is_enabled():
            send_btn.click()
        else:
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            if composer.inner_text().strip():
                page.keyboard.press("Meta+Enter")

        page.wait_for_timeout(2500)
        remaining_draft = ""
        try:
            remaining_draft = composer.inner_text().strip()
        except Exception:
            pass
        if len(remaining_draft) > 40:
            print(f"[Cycle:1stDegree] ⚠️ Composer still contains unsent draft for {username}; not counting as sent.")
            return False
        print(f"[Cycle:1stDegree] ✅ Verified message dispatched to {first_name} ({username})!")
        return True
    finally:
        try:
            page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')
        except:
            pass

# -------------------------------------------------------------
# 2. 2ND-DEGREE POC CONNECTION REQUEST SENDER
# -------------------------------------------------------------
def send_poc_request(page, username: str, full_name: str, custom_note: str = "") -> bool:
    first_name = full_name.split()[0] if full_name else "there"
    profile_url = f"https://www.linkedin.com/in/{username}/"
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2500)

        # Remove chat bubbles
        page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')

        # Full-profile Experience & employer badge exclusion check before sending Connect!
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(1000)
        prof_txt = page.evaluate(JS_EXTRACT_PROFILE_BODY_ONLY) or ""
        if len(prof_txt.strip()) < 60:
            return False
        if has_forbidden_company(prof_txt):
            print(f"[ExclusionGuard] 🛑 BLOCKED 2nd-degree Connect for {username}: matched restricted employer on profile!")
            _mark_linkedin_user_excluded(username, "E1_CONFIDENTIAL_COMPANY")
            return False

        # Check if already Pending on profile
        pending_el = page.locator('main a:has-text("Pending"), main button:has-text("Pending"), main [aria-label*="Pending"]').first
        if pending_el.count() > 0 and pending_el.is_visible():
            return False

        # LinkedIn 2026 UI uses `/preload/custom-invite/?vanityName={username}` (anchor link on profile/search)!
        invite_url = f"https://www.linkedin.com/preload/custom-invite/?vanityName={username}"
        page.goto(invite_url, wait_until="domcontentloaded", timeout=22000)
        page.wait_for_timeout(2400)

        body_lower = (page.locator("body").inner_text() or "").lower()
        if (
            "weekly invitation limit" in body_lower
            or "reached the weekly" in body_lower
            or "reached the limit" in body_lower
            or "out of invitations" in body_lower
        ):
            print("[Cycle:2ndDegree] LinkedIn weekly invitation limit reached. Handled gracefully.")
            dismiss_btn = page.locator('button:has-text("Got it"), button[aria-label="Dismiss"]').first
            if dismiss_btn.count() > 0 and dismiss_btn.is_visible():
                dismiss_btn.click(no_wait_after=True)
            return "LIMIT_REACHED"

        # Prefer Send without a note if custom note modal is upselling Premium, else try Add a note first
        send_without_note = page.locator('button:has-text("Send without a note"), button[aria-label*="Send without a note"]').first
        add_note_btn = page.locator('button:has-text("Add a note"), button[aria-label="Add a note"]').first

        if add_note_btn.count() > 0 and add_note_btn.is_visible() and add_note_btn.is_enabled():
            try:
                add_note_btn.click(no_wait_after=True, timeout=3000)
                page.wait_for_timeout(900)
                note_box = page.locator('textarea#custom-message, textarea[name="message"], div[role="dialog"] textarea').first
                if note_box.count() > 0 and note_box.is_visible():
                    note_text = (
                        custom_note[:180]
                        if custom_note
                        else f"Hi {first_name}, admire your work in product/talent! NSUT '26 B.Tech with 1 yr product & analytics exp (immediate 15-day joiner) - would love to connect!"[:180]
                    )
                    note_box.click(timeout=2500)
                    page.keyboard.insert_text(note_text)
                    page.wait_for_timeout(600)
                    send_btn = page.locator('div[role="dialog"] button[aria-label="Send invitation"], div[role="dialog"] button:has-text("Send")').last
                    if send_btn.count() > 0 and send_btn.is_visible() and send_btn.is_enabled():
                        send_btn.click(no_wait_after=True, timeout=3000)
                        page.wait_for_timeout(2200)
                        post_txt = (page.locator("body").inner_text() or "").lower()
                        if "weekly invitation limit" in post_txt or "reached the limit" in post_txt:
                            return "LIMIT_REACHED"
                        print(f"[Cycle:2ndDegree] -> ✅ Sent personalized connection request to {full_name} ({username})!")
                        return True
                else:
                    # Free monthly custom-note cap hit or upsell shown -> reopen custom-invite and click Send without a note
                    page.goto(invite_url, wait_until="domcontentloaded", timeout=18000)
                    page.wait_for_timeout(1800)
            except Exception as ne:
                print(f"[Cycle:2ndDegree] Note fallback on {username}: {ne}")
                try:
                    page.goto(invite_url, wait_until="domcontentloaded", timeout=18000)
                    page.wait_for_timeout(1800)
                except Exception:
                    pass

        # Reliable fallback: Send without a note
        send_without_note = page.locator('button:has-text("Send without a note"), button[aria-label*="Send without a note"]').first
        if send_without_note.count() > 0 and send_without_note.is_visible() and send_without_note.is_enabled():
            send_without_note.click(no_wait_after=True)
            page.wait_for_timeout(2200)
            post_txt = (page.locator("body").inner_text() or "").lower()
            if "weekly invitation limit" in post_txt or "reached the limit" in post_txt:
                return "LIMIT_REACHED"
            print(f"[Cycle:2ndDegree] -> ✅ Sent connection request (without note) to {full_name} ({username})!")
            return True

        return False
    finally:
        try:
            page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')
        except Exception:
            pass


def discover_and_send_poc_invitation(page, context, conn) -> bool:
    """Finds a 2nd-degree product/talent/founder leader and sends a connection invitation (LinkedIn 2026 DOM)."""
    cur = conn.cursor()
    keywords = [
        '"Technical Recruiter" India',
        '"Product Recruiter" OR "Talent Acquisition" Product',
        '"Product Manager" Gurugram OR Bengaluru',
        'NSUT "Product Manager"',
        '"Head of Product" OR "Founder" Gurugram OR Bengaluru',
    ]
    kw = random.choice(keywords)
    q_kw = urllib.parse.quote(kw)
    search_url = f"https://www.linkedin.com/search/results/people/?keywords={q_kw}&network=%5B%22S%22%5D"

    try:
        if page is None or page.is_closed():
            page = context.pages[0] if context.pages else context.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3200)

        # Extract 2nd-degree candidates that actually expose an active Connect link (`vanityName=...` or `• 2nd`)
        candidates = page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const inviteLinks = Array.from(document.querySelectorAll('main a[href*="/preload/search-custom-invite/"], main a[aria-label*="Invite"][aria-label*="connect" i]'));
                for (const inv of inviteLinks) {
                    const href = inv.getAttribute('href') || '';
                    const aria = inv.getAttribute('aria-label') || '';
                    let uname = '';
                    const m = href.match(/vanityName=([^&#]+)/);
                    if (m) uname = decodeURIComponent(m[1]).trim();
                    const row = inv.closest('div[role="listitem"], li') || inv.parentElement?.parentElement?.parentElement;
                    if (!uname && row) {
                        const pLink = row.querySelector('a[href*="/in/"]');
                        if (pLink) {
                            const pm = pLink.href.match(/\\/in\\/([^/?#]+)/);
                            if (pm) uname = pm[1];
                        }
                    }
                    if (!uname || seen.has(uname)) continue;
                    const rowTxt = (row ? row.innerText : aria) || '';
                    const lines = rowTxt.split('\\n').map(s => s.trim()).filter(Boolean);
                    let name = aria.replace(/^Invite\\s+/i, '').replace(/\\s+to connect$/i, '').trim() || (lines[0] || uname).replace('• 2nd', '').trim();
                    let headline = '';
                    for (const l of lines) {
                        if (l.includes(name) || l.startsWith('•') || l === 'Connect' || l.includes('mutual connection')) continue;
                        headline = l;
                        break;
                    }
                    seen.add(uname);
                    out.push({ username: uname, name, headline, cardText: rowTxt.slice(0, 350) });
                }
                return out.slice(0, 10);
            }"""
        )

        for cand in candidates:
            try:
                username = cand["username"]
                name_raw = cand["name"]
                headline = cand["headline"]
                card_text = cand["cardText"]

                if is_excluded(name=name_raw, headline=headline, card_text=card_text):
                    continue
                if re.search(r"\b(intern|internship|trainee|campus ambassador|fellowship)\b", headline, re.IGNORECASE):
                    continue

                cur.execute("SELECT status FROM poc_connection_requests WHERE id = ?", (username,))
                if cur.fetchone():
                    continue

                print(f"[Cycle:2ndDegree] Targeting 2nd-degree POC: {name_raw} ({headline[:45]})...")
                res = send_poc_request(page, username, name_raw)
                if res == "LIMIT_REACHED":
                    return "LIMIT_REACHED"
                if res:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO poc_connection_requests (id, name, headline, status, requested_at)
                        VALUES (?, ?, ?, 'requested', CURRENT_TIMESTAMP)
                        """,
                        (username, name_raw, headline),
                    )
                    conn.commit()
                    return True
            except Exception:
                continue
    except Exception as se:
        print(f"[Cycle:2ndDegree] Search error: {se}")
    return False

# -------------------------------------------------------------
# 3. ACCEPTANCE DETECTOR & IMMEDIATE COLD PITCH
# -------------------------------------------------------------
def check_and_message_accepted_connections(page, conn) -> int:
    """Checks recently added connections; if a requested POC accepted, sends Option A cold message immediately."""
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM poc_connection_requests WHERE status='requested' AND cold_msg_sent_at IS NULL")
    pending_pocs = {r[0]: r[1] for r in cur.fetchall()}
    if not pending_pocs:
        return 0

    print(f"[Cycle:Acceptance] Checking {len(pending_pocs)} pending connection requests for acceptances...")
    connections_url = "https://www.linkedin.com/mynetwork/invite-connect/connections/"
    try:
        page.goto(connections_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
    except:
        return 0

    js_extract = """
    () => {
        const results = [];
        const seen = new Set();
        const links = Array.from(document.querySelectorAll('a[href*="/in/"]'));
        for (const l of links) {
            const parts = l.href.split('/in/');
            if (parts.length < 2) continue;
            const uname = parts[1].split('/')[0].split('?')[0];
            if (!uname || seen.has(uname)) continue;
            const text = l.innerText.trim();
            if (text.length > 2) {
                seen.add(uname);
                results.push({ username: uname, text: text });
            }
        }
        return results;
    }
    """
    recent_conns = page.evaluate(js_extract)
    messaged = 0

    for conn_item in recent_conns[:20]:
        uname = conn_item["username"]
        if uname in pending_pocs:
            name = pending_pocs[uname]
            first_name = name.split()[0] if name else "there"
            print(f"[Cycle:Acceptance] 🎯 POC Accepted Connection: {name} ({uname})! Sending Option A pitch...")
            success = turbo_send_1st_degree(page, uname, first_name)
            if success:
                cur.execute('''
                    UPDATE poc_connection_requests
                    SET status='accepted', accepted_at=CURRENT_TIMESTAMP, cold_msg_sent_at=CURRENT_TIMESTAMP
                    WHERE id=?
                ''', (uname,))
                conn.commit()
                messaged += 1
                time.sleep(10)
    return messaged

# -------------------------------------------------------------
# 4. AUTOMATED FOLLOW-UP ON UNREPLIED MESSAGES
# -------------------------------------------------------------
def run_followup_cycle(page, conn, max_followups: int = 3) -> int:
    """Finds sent messages older than 48 hours without reply and sends a polite follow-up."""
    cur = conn.cursor()
    cur.execute('''
        SELECT id, name FROM linkedin_connections_outreach
        WHERE status='sent' AND followup_sent_at IS NULL
        AND julianday('now') - julianday(sent_at) >= 2.0
        LIMIT ?
    ''', (max_followups,))
    candidates = cur.fetchall()
    if not candidates:
        return 0

    print(f"[Cycle:Followup] Found {len(candidates)} candidates for 48h follow-up.")
    followed_up = 0
    for username, name in candidates:
        if not verify_linkedin_profile_safe(page, username):
            continue
        first_name = name.split()[0] if name else "there"
        compose_url = f"https://www.linkedin.com/messaging/compose/?recipient={username}"
        try:
            page.goto(compose_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)

            # Check if they replied
            past_events = page.locator('.msg-s-message-list__event, .msg-s-event-listitem').all()
            if past_events:
                last_event = past_events[-1].inner_text().lower()
                # If recipient already replied, don't bump
                if "vishal" not in last_event[:30]:
                    print(f"[Cycle:Followup] {name} has already replied. Skipping follow-up.")
                    cur.execute("UPDATE linkedin_connections_outreach SET followup_sent_at=CURRENT_TIMESTAMP WHERE id=?", (username,))
                    conn.commit()
                    continue

            composer = page.locator('div[role="textbox"], .msg-form__contenteditable').first
            if composer.is_visible():
                followup_text = (
                    f"Hey {first_name}, just following up quickly on my note above—in case your team has any open APM or Product Analyst roles, or if you could point me towards the hiring manager. Thanks so much!"
                )
                composer.click()
                page.keyboard.insert_text(followup_text)
                page.wait_for_timeout(500)
                send_btn = page.locator('button.msg-form__send-button, button:has-text("Send")').first
                if send_btn.is_visible():
                    send_btn.click()
                    page.wait_for_timeout(1500)
                    cur.execute("UPDATE linkedin_connections_outreach SET followup_sent_at=CURRENT_TIMESTAMP WHERE id=?", (username,))
                    conn.commit()
                    followed_up += 1
                    print(f"[Cycle:Followup] -> Sent follow-up to {name}!")
                    time.sleep(10)
        except Exception as e:
            continue
    return followed_up

# -------------------------------------------------------------
# 5. PORTAL AUTO-APPLY & FEED SCRAPER
# -------------------------------------------------------------
def run_portal_apply_stage(conn, max_naukri: int = 3, max_redirects: int = 3, **kwargs):
    """Processes pending Naukri applications and external ATS redirects in isolated subprocesses."""
    print("[Cycle:Portals] Executing isolated portal auto-apply batch...")
    try:
        naukri_script = str(PROJECT_ROOT / "scripts" / "run_naukri_batch.py")
        subprocess.run([sys.executable, naukri_script, str(max_naukri)], timeout=180)
    except Exception as ne:
        print(f"[Cycle:Naukri] Subprocess error: {ne}")

    try:
        redirect_script = str(PROJECT_ROOT / "scripts" / "process_external_redirects.py")
        subprocess.run([sys.executable, redirect_script, str(max_redirects)], timeout=180)
    except Exception as re_err:
        print(f"[Cycle:Redirects] Subprocess error: {re_err}")

def sync_new_outreach_leads_to_cloud_vm(conn) -> int:
    """Pushes recent 'pending' outreach_leads scraped from LinkedIn posts to the 24/7 OCI Cloud Email Worker DB."""
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if not os.path.exists(ssh_key):
        return 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name, email, company, role_target
            FROM outreach_leads
            WHERE status = 'pending'
            ORDER BY id DESC
            LIMIT 40
            """
        )
        rows = cur.fetchall()
        if not rows:
            return 0
        import json, base64
        b64_payload = base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
        remote_py = (
            "import base64, json, sqlite3; "
            f"rows = json.loads(base64.b64decode(\"{b64_payload}\").decode()); "
            "conn = sqlite3.connect(\"/home/opc/.linkedin_agent.db\"); "
            "c = conn.cursor(); "
            "c.execute(\"CREATE TABLE IF NOT EXISTS bounced_blocklist (email TEXT PRIMARY KEY, reason TEXT, detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)\"); "
            "blocked = {r[0] for r in c.execute(\"SELECT LOWER(TRIM(email)) FROM bounced_blocklist WHERE email IS NOT NULL\").fetchall()}; "
            "rows = [r for r in rows if r[1] and r[1].strip().lower() not in blocked]; "
            "before = conn.total_changes; "
            "c.executemany(\"INSERT OR IGNORE INTO outreach_leads (name, email, company, role_target, status, created_at) "
            "VALUES (?, ?, ?, ?, \\x27pending\\x27, CURRENT_TIMESTAMP)\", rows); "
            "added = conn.total_changes - before; conn.commit(); conn.close(); print(added)"
        )
        proc = subprocess.run(
            [
                "ssh", "-i", ssh_key,
                "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                "opc@130.210.108.55",
                f"/home/opc/career-agent/venv/bin/python3 -c '{remote_py}'"
            ],
            capture_output=True, text=True, timeout=15
        )
        added_remote = int((proc.stdout or "0").strip() or "0")
        if added_remote > 0:
            print(f"[Cycle:CloudSync] ☁️ Synced {added_remote} fresh post-body email lead(s) to OCI Cloud Email Worker!")
        return added_remote
    except Exception as e:
        print(f"[Cycle:CloudSync] Notice: {e}")
        return 0


def _handle_linkedin_logout(page) -> None:
    """Hard-stops all LinkedIn automation on logout/checkpoint until user logs back in or restarts via CLI/#prompt."""
    cur_url = getattr(page, "url", "") if page else ""
    print(f"[Cycle:LogoutGuard] 🛑 LinkedIn logout/checkpoint detected ({cur_url})! Hard-stopping all LinkedIn automation.")
    set_linkedin_enabled(False, reason="linkedin_logged_out", updated_by="auto_logout_guard")
    try:
        from src.services.system_alerter import send_system_alert
        send_system_alert(
            platform="LinkedIn",
            issue_type="logged_out",
            summary="LinkedIn logged out or hit a security checkpoint — all LinkedIn tasks safely paused.",
            fix_steps="Open Ridealong (`http://127.0.0.1:8083`) -> sign back into LinkedIn (the daemon auto-resumes as soon as login completes, or reply `start linkedin` in `#prompt`).",
            details=cur_url,
            cooldown_minutes=30,
        )
    except Exception:
        pass


def _sleep_respecting_control(seconds: float) -> bool:
    """Sleeps in short slices; returns False immediately if LinkedIn automation is stopped via #prompt/CLI."""
    end_ts = time.time() + seconds
    while time.time() < end_ts:
        ctl = sync_control_with_cloud()
        if not ctl.get("linkedin_enabled", True):
            return False
        time.sleep(min(5.0, max(0.5, end_ts - time.time())))
    return True


def send_discord_cycle_note(message: str) -> None:
    """Sends a verified progress log to Discord #agent channel."""
    try:
        from src.agents.discord_notifier import DiscordNotifier
        from src import config
        DiscordNotifier().client.send_message(config.DISCORD_AGENT_CHANNEL_ID, message)
    except Exception:
        pass


def run_feed_scan_stage(conn=None, duration_minutes: float = 10.0) -> int:
    """Scans LinkedIn personal feed & hiring posts (post body ONLY, never comments) in an isolated subprocess."""
    print(f"[Cycle:Feed] Executing {duration_minutes:.1f}-minute personal feed & hiring post hunt (post bodies only)...")
    try:
        feed_py = (
            f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
            f"from src.agents.feed_scanner import scan_personal_feed_for_jobs; "
            f"scan_personal_feed_for_jobs(duration_minutes={float(duration_minutes):.2f})"
        )
        subprocess.run([sys.executable, "-u", "-c", feed_py], timeout=int(duration_minutes * 60 + 240))
        if conn is not None:
            sync_new_outreach_leads_to_cloud_vm(conn)
    except Exception as fe:
        print(f"[Cycle:Feed] Feed scan error: {fe}")
    return 1


def run_connection_or_feed_branch(page, context, conn, window_minutes: float = 10.0) -> None:
    """
    Stage B of User Cycle:
    1. Check connection limit by attempting 1 connection invite.
    2. If allowed -> keep sending connection invites (~1 min apart) for up to 10 minutes.
       If limit triggers mid-window, spend remaining time in Feed Scraper.
    3. If limit reached -> run 10-minute Feed Scraper (post-body emails + job links/forms, ignoring comments).
    """
    deadline = time.time() + (window_minutes * 60.0)
    print("[ConnectionLimitCheck] Testing if LinkedIn weekly connection limit allows new invites...")

    res = discover_and_send_poc_invitation(page, context, conn)
    if res is True:
        sent_invites = 1
        send_discord_cycle_note(
            "🎉 **Stage B — Connection Limit Check: ALLOWED!**\n"
            "• Invitation #1 sent successfully.\n"
            f"• Continuing paced connection invites (~1 min gap) for the {int(window_minutes)}-minute window."
        )
        while time.time() < deadline - 45:
            time.sleep(random.uniform(55, 68))
            if time.time() >= deadline:
                break
            r = discover_and_send_poc_invitation(page, context, conn)
            if r is True:
                sent_invites += 1
            elif r == "LIMIT_REACHED":
                print(f"[ConnectionLimitCheck] Limit reached after {sent_invites} invite(s). Switching remaining time to Feed Hunt.")
                break
            else:
                # Could not find a Connect button in that search; pause briefly and retry
                time.sleep(10)

        remaining_mins = (deadline - time.time()) / 60.0
        if remaining_mins >= 1.5:
            print(f"[InterBatch:Branch] {remaining_mins:.1f}m left in 10-min slot -> switching to Feed Hunt...")
            run_feed_scan_stage(conn=conn, duration_minutes=remaining_mins)
    else:
        reason_str = "Weekly invitation limit active" if res == "LIMIT_REACHED" else "Connection limit / no open invite slot"
        print(f"[ConnectionLimitCheck] {reason_str} -> Switching to {window_minutes:.0f}-minute Feed & Post Email Hunt (post body only, no comments)...")
        send_discord_cycle_note(
            f"🔄 **Stage B — {reason_str}**\n"
            f"• Switching to **{int(window_minutes)}-minute LinkedIn Feed & Post Hunt** (extracting emails strictly from post bodies, ignoring comments, and queuing job links/forms)."
        )
        run_feed_scan_stage(conn=conn, duration_minutes=window_minutes)


def run_inter_batch_activities(page, context, conn, batch_num: int = 1):
    """Executes organic randomized waits & sequential stages after each message batch:
    1. Random Wait #1 (< 20m: 4–17.5m) + Connection Request Limit Test (if renewed, sends invites at random 48–115s gaps).
    2. Random Wait #2 (1.5–5m) + Randomized Feed Hunt (5.5–11.5m, post bodies ONLY, never comments).
    3. Random Wait #3 (1.5–5m) + Randomized LinkedIn Jobs Apply (6–12m, strict receipt verification).
    4. Random Cool-Down (1.5–4.5m) before starting the next randomized message batch.
    """
    wait1_mins = round(random.uniform(3.0, 9.0), 1)
    feed_gap_mins = round(random.uniform(1.0, 3.0), 1)
    feed_dur_mins = round(random.uniform(5.0, 9.0), 1)
    jobs_gap_mins = round(random.uniform(1.0, 3.0), 1)
    jobs_dur_mins = random.randint(8, 13)
    jobs_max_apps = random.randint(6, 10)
    end_gap_mins = round(random.uniform(1.5, 3.5), 1)

    print("\n" + "="*70)
    print(
        f"🎲 BATCH #{batch_num} COMPLETE -> RANDOMIZED ANTI-PATTERN SCHEDULE:\n"
        f"  • Gap #1 + Connection Test: ~{wait1_mins}m (<20m) | Feed Gap: ~{feed_gap_mins}m -> Feed Hunt: ~{feed_dur_mins}m\n"
        f"  • Jobs Gap: ~{jobs_gap_mins}m -> Jobs Apply: ~{jobs_dur_mins}m | Pre-Batch Breather: ~{end_gap_mins}m"
    )
    print("="*70)

    # 1. Random Gap (<20 min) + Connection Limit Test
    pre_test_s = wait1_mins * 60.0 * random.uniform(0.45, 0.7)
    post_test_s = (wait1_mins * 60.0) - pre_test_s
    print(f"[InterBatch:Wait1] Resting randomly for {pre_test_s/60.0:.1f}m before testing connection limit...")
    sync_control_with_cloud(stage_info=f"Batch #{batch_num} Random Cool-Down ({wait1_mins}m) + Connection Test", force=True)
    if not _sleep_respecting_control(pre_test_s):
        return
    if page is None or page.is_closed():
        page = context.pages[0] if context.pages else context.new_page()
    if is_linkedin_logged_out(page):
        _handle_linkedin_logout(page)
        return

    print("[ConnectionLimitCheck] Testing 1 connection invite during <20m randomized window...")
    res = discover_and_send_poc_invitation(page, context, conn)
    if res is True:
        sent_invites = 1
        invite_window_end = time.time() + max(post_test_s, random.uniform(300, 660))
        send_discord_cycle_note(
            "🎉 **Connection Limit Check: ALLOWED!**\n"
            f"• Invitation #1 sent; continuing with random `48–115s` gaps during this randomized window."
        )
        while time.time() < invite_window_end - 50:
            if not _sleep_respecting_control(random.uniform(48, 115)):
                return
            r = discover_and_send_poc_invitation(page, context, conn)
            if r is True:
                sent_invites += 1
            elif r == "LIMIT_REACHED":
                print(f"[ConnectionLimitCheck] Limit reached after {sent_invites} invite(s).")
                break
            else:
                time.sleep(random.uniform(8, 18))
    else:
        print(f"[InterBatch:Wait1] Connection limit still active -> completing remaining {post_test_s/60.0:.1f}m of <20m random gap...")
        if not _sleep_respecting_control(post_test_s):
            return

    # 2. Random Gap #2 -> Randomized Feed Post-Body Hunt
    print(f"[InterBatch:FeedGap] Random transition wait of {feed_gap_mins}m before Feed Scrape...")
    sync_control_with_cloud(stage_info=f"Batch #{batch_num} Random Gap ({feed_gap_mins}m) -> Feed Hunt ({feed_dur_mins}m)", force=True)
    if not _sleep_respecting_control(feed_gap_mins * 60.0):
        return
    if is_linkedin_logged_out(page):
        _handle_linkedin_logout(page)
        return

    send_discord_cycle_note(
        f"🔄 **Stage B — Randomized Feed Hunt & Post Engagement (~{feed_dur_mins} min)**\n"
        "• Selectively liking & commenting on Founder / HM / Recruiter posts + extracting recruiter emails strictly from post bodies."
    )
    run_feed_scan_stage(conn=conn, duration_minutes=feed_dur_mins)

    # 3. Random Gap #3 -> Randomized LinkedIn Jobs Section
    print(f"[InterBatch:JobsGap] Random transition wait of {jobs_gap_mins}m before LinkedIn Jobs section...")
    sync_control_with_cloud(stage_info=f"Batch #{batch_num} Random Gap ({jobs_gap_mins}m) -> Jobs Apply ({jobs_dur_mins}m)", force=True)
    if not _sleep_respecting_control(jobs_gap_mins * 60.0):
        return
    if is_linkedin_logged_out(page):
        _handle_linkedin_logout(page)
        return

    print(f"[InterBatch:Jobs] Stage C -> Applying in LinkedIn Jobs section for ~{jobs_dur_mins}m (max {jobs_max_apps} verified apps)...")
    send_discord_cycle_note(
        f"💼 **Stage C — Randomized LinkedIn Jobs Window (~{jobs_dur_mins} min)**\n"
        "• Applying sequentially to matching APM / Product / Business Analyst roles (verified confirmations only)."
    )
    try:
        jobs_py = (
            f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
            "from src.agents.linkedin_job_applicant import apply_linkedin_jobs_stage; "
            f"n = apply_linkedin_jobs_stage(duration_minutes={jobs_dur_mins}, max_apps={jobs_max_apps}); "
            "print(f'[InterBatch:Jobs] Stage C finished: {n} strictly confirmed LinkedIn Easy Apply submission(s).')"
        )
        subprocess.run([sys.executable, "-u", "-c", jobs_py], timeout=int(jobs_dur_mins * 60 + 240))
    except Exception as jae:
        print(f"[InterBatch:Jobs] Note: {jae}")

    # 4. Stage D -> Local Naukri Discovery + Auto-Apply (1 task at a time via Ridealong, synced to Cloud DB)
    if sync_control_with_cloud().get("linkedin_enabled", True) and is_in_active_window():
        print("[InterBatch:Naukri] Stage D -> Discovering & auto-applying to fresh Naukri openings (max 10 verified apps)...")
        sync_control_with_cloud(stage_info=f"Batch #{batch_num} Stage D -> Naukri Auto-Apply", force=True)
        send_discord_cycle_note(
            "💼 **Stage D — Naukri Fresh Discovery & Auto-Apply**\n"
            "• Scraping `<24h` APM / Product / Business Analyst listings on Naukri and submitting verified 1-click & chatbot applications."
        )
        try:
            nk_py = (
                f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
                "from scripts.run_naukri_batch import run_naukri_batch; "
                "run_naukri_batch(limit=10)"
            )
            subprocess.run([sys.executable, "-u", "-c", nk_py], timeout=420)
        except Exception as nke:
            print(f"[InterBatch:Naukri] Note: {nke}")

    # 4b. Stage E -> Local Indeed Discovery + SmartApply (via Ridealong, synced to Cloud DB)
    if sync_control_with_cloud().get("linkedin_enabled", True) and is_in_active_window():
        print("[InterBatch:Indeed] Stage E -> Discovering & auto-applying to fresh Indeed Easily-Apply openings...")
        sync_control_with_cloud(stage_info=f"Batch #{batch_num} Stage E -> Indeed SmartApply", force=True)
        send_discord_cycle_note(
            "💼 **Stage E — Indeed Fresh Discovery & SmartApply**\n"
            "• Scraping fresh APM / Product / Business / Data Analyst Easily-Apply listings on Indeed and submitting verified SmartApply applications."
        )
        try:
            in_py = (
                f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
                "from scripts.run_indeed_batch import run_indeed_batch; "
                "run_indeed_batch(limit=8)"
            )
            subprocess.run([sys.executable, "-u", "-c", in_py], timeout=420)
        except Exception as ine:
            print(f"[InterBatch:Indeed] Note: {ine}")

    # 5. Acceptance check + Pre-Batch Random Cool-Down
    try:
        new_accepts = check_and_message_accepted_connections(page, conn)
        if new_accepts > 0:
            print(f"[InterBatch:Accepts] 🎯 Pitched {new_accepts} newly accepted connection(s)!")
    except Exception:
        pass

    print(f"[InterBatch:CoolDown] Random pre-batch breather of {end_gap_mins}m before next message batch...")
    if not _sleep_respecting_control(end_gap_mins * 60.0):
        return

    print("="*70)
    print(f"✅ Cycle #{batch_num} complete. Looping back to Stage A (Next Randomized Message Batch, <2m random gaps)!")
    print("="*70 + "\n")
    send_discord_cycle_note(
        f"🔁 **Cycle #{batch_num} Complete** — Starting next randomized message batch (`<2 min` random gaps)."
    )

# -------------------------------------------------------------
# MASTER CONTINUOUS CYCLE (1 TASK AT A TIME SEQUENTIAL LOOP)
# -------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))
DAILY_MAX_1ST_DEGREE = 80       # Paced safely across the day

# LinkedIn Temporary Suspension Cooldown (12 Nov 10:37 AM PST = 13 Nov 00:07 AM IST)
LINKEDIN_SUSPEND_UNTIL = datetime(2026, 11, 13, 0, 7, 0, tzinfo=IST)

def is_in_active_window() -> bool:
    """Returns True during the user-mandated window: 10:00 AM to 11:00 PM IST, and strictly after any temporary suspension."""
    now_ist = datetime.now(IST)
    if now_ist < LINKEDIN_SUSPEND_UNTIL:
        return False
    return 10 <= now_ist.hour < 23

def _sync_linkedin_outreach_to_cloud(conn) -> None:
    """Pushes latest Mac linkedin_connections_outreach rows to Cloud VM DB so Live Dashboard stays accurate."""
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if sys.platform != "darwin" or not os.path.exists(ssh_key):
        return
    try:
        import base64, json
        rows = conn.execute(
            "SELECT id, name, headline, status, exclusion_reason, sent_at, followup_sent_at "
            "FROM linkedin_connections_outreach ORDER BY sent_at DESC LIMIT 30"
        ).fetchall()
        if not rows:
            return
        b64 = base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
        remote_py = (
            f"import base64,json,sqlite3; rows=json.loads(base64.b64decode('{b64}').decode()); "
            "c=sqlite3.connect('/home/opc/.linkedin_agent.db',timeout=15.0); "
            "c.executemany('INSERT OR REPLACE INTO linkedin_connections_outreach "
            "(id,name,headline,status,exclusion_reason,sent_at,followup_sent_at) VALUES (?,?,?,?,?,?,?)', rows); "
            "c.commit(); c.close()"
        )
        subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=4", "opc@130.210.108.55", "/home/opc/career-agent/venv/bin/python3 -"],
            input=remote_py, text=True, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def run_master_cycle(interval_minutes: int = 1, start_inter_batch: bool = False):
    init_master_db()
    conn = sqlite3.connect(DB_PATH)

    print("\n" + "="*80)
    print("🚀 LINKEDIN RANDOMIZED ANTI-PATTERN SEQUENTIAL MASTER CYCLE")
    print("Operating Window: 10:00 AM - 11:00 PM IST")
    print("Cycle: [Stage A] 8-11 Msgs (<2m random gap) -> [<20m Random Gap + Connect Test] -> [Random Gap + Feed Hunt] -> [Random Gap + Jobs] -> Repeat")
    print("="*80 + "\n")

    with sync_playwright() as p:
        iteration_count = 0
        messages_sent_session = 0
        init_cur = conn.cursor()
        init_cur.execute('''
            SELECT count(*) FROM linkedin_connections_outreach 
            WHERE date(sent_at, '+5 hours', '30 minutes') = date('now', '+5 hours', '30 minutes')
        ''')
        init_today = init_cur.fetchone()[0]
        batch_target = random.randint(8, 11)
        batch_msg_count = init_today % 10
        if batch_msg_count >= batch_target:
            batch_target = batch_msg_count + 1
        batch_num = (init_today // 10) + 1
        browser = None
        context = None
        page = None
        paused_logged = False
        last_naukri_standalone_ts = 0.0

        while True:
            try:
                now_ist = datetime.now(IST)

                # 0. Cloud-Synced Manual / Logout Stop Check (#prompt or CLI)
                ctl_state = sync_control_with_cloud()
                if not ctl_state.get("linkedin_enabled", True):
                    # Auto-resume immediately once Vishal completes LinkedIn login + 2FA in Ridealong
                    if ctl_state.get("reason") in ("linkedin_logged_out", "waiting_for_login"):
                        try:
                            if browser is None or not browser.is_connected():
                                browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
                                context = browser.contexts[0]
                            has_li_at = any(
                                c.get("name") == "li_at" and c.get("value")
                                for c in context.cookies()
                            )
                            if has_li_at:
                                probe_pg = context.new_page()
                                try:
                                    probe_pg.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=18000)
                                    probe_pg.wait_for_timeout(2000)
                                    feed_ok = not is_linkedin_logged_out(probe_pg)
                                finally:
                                    if len(context.pages) > 1:
                                        probe_pg.close()
                                if feed_ok:
                                    if page is not None and not page.is_closed():
                                        try:
                                            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=18000)
                                        except Exception:
                                            pass
                                    print("[Cycle:Control] 🟢 Verified live LinkedIn Feed session! Auto-starting LinkedIn cycle now!")
                                    set_linkedin_enabled(True, reason="active", updated_by="auto_resume_on_login")
                                    try:
                                        from src.services.system_alerter import clear_system_alert
                                        clear_system_alert("LinkedIn")
                                    except Exception:
                                        pass
                                    send_discord_cycle_note(
                                        "🟢 **LinkedIn Login Verified — Automation Started Automatically!**\n"
                                        "• Resuming Stage A (1st-Degree DMs) + Feed Recruiter Scan + LinkedIn Easy Apply + Naukri + Indeed."
                                    )
                                    paused_logged = False
                                    continue
                        except Exception:
                            pass
                    if not paused_logged:
                        print(f"[Cycle:Control] 🛑 LinkedIn automation STOPPED (reason: {ctl_state.get('reason')} | by: {ctl_state.get('updated_by')}). Naukri + Indeed continue independently during 10 AM - 11 PM IST!")
                        paused_logged = True
                    # Keep Naukri + Indeed running every ~30m during 10 AM - 11 PM IST even while LinkedIn is paused/logged out (unless manual emergency_stop)
                    if is_in_active_window() and ctl_state.get("reason") != "emergency_stop" and (time.time() - last_naukri_standalone_ts) >= 1800.0:
                        last_naukri_standalone_ts = time.time()
                        try:
                            nk_py = (
                                f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
                                "from scripts.run_naukri_batch import run_naukri_batch; "
                                "from scripts.run_indeed_batch import run_indeed_batch; "
                                "run_naukri_batch(limit=10); "
                                "run_indeed_batch(limit=8)"
                            )
                            subprocess.run([sys.executable, "-u", "-c", nk_py], timeout=600)
                        except Exception as snke:
                            print(f"[Cycle:NaukriIndeedStandalone] Note: {snke}")
                    time.sleep(10)
                    continue
                if paused_logged:
                    print("[Cycle:Control] 🟢 LinkedIn automation RESUMED via control state!")
                    paused_logged = False
                    if page is not None and not page.is_closed():
                        try:
                            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(1500)
                        except Exception:
                            pass

                # 1. Check Operating Window & Temporary Suspension
                if not is_in_active_window():
                    now_str = now_ist.strftime('%I:%M %p IST')
                    if now_ist < LINKEDIN_SUSPEND_UNTIL:
                        until_str = LINKEDIN_SUSPEND_UNTIL.strftime('%I:%M %p IST (%d %b)')
                        print(f"[Cycle:Suspension] LinkedIn temporarily suspended until {until_str}. Current: {now_str}. Sleeping safely...")
                    else:
                        print(f"[Cycle:Schedule] Current time {now_str} is outside 10:00 AM - 11:00 PM IST window. Resting safely...")
                    time.sleep(60)
                    continue

                # Reconnect or refresh context if needed
                if browser is None or not browser.is_connected():
                    subprocess.run(["ridealong", "wake", "default"], capture_output=True)
                    browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else context.new_page()
                elif page is None or page.is_closed():
                    page = context.pages[0] if context.pages else context.new_page()

                # Auth Check: If LinkedIn logs out, HARD-STOP all LinkedIn automation until user commands restart!
                if is_linkedin_logged_out(page):
                    _handle_linkedin_logout(page)
                    continue

                iteration_count += 1
                cur = conn.cursor()

                # Get Daily Sent Metrics (based on IST date)
                cur.execute('''
                    SELECT count(*) FROM linkedin_connections_outreach 
                    WHERE date(sent_at, '+5 hours', '30 minutes') = date('now', '+5 hours', '30 minutes')
                ''')
                daily_1st_sent = cur.fetchone()[0]

                cur.execute("SELECT count(*) FROM linkedin_connections_queue WHERE status='pending'")
                queue_pending = cur.fetchone()[0]

                now_fmt = now_ist.strftime('%I:%M %p IST')
                if start_inter_batch:
                    start_inter_batch = False
                    run_inter_batch_activities(page, context, conn, batch_num=max(1, batch_num - 1))
                    batch_target = random.randint(8, 11)
                    continue

                sync_control_with_cloud(stage_info=f"Batch #{batch_num} Stage A ({batch_msg_count}/{batch_target} msgs | {daily_1st_sent} today)")
                print(f"\n[Cycle:Heartbeat #{iteration_count} | {now_fmt}] Batch #{batch_num} ({batch_msg_count}/{batch_target}) | Today Sent: {daily_1st_sent}/{DAILY_MAX_1ST_DEGREE} | Queue: {queue_pending}")

                # STAGE 0: Queue Check & Auto-Refill if low (scroll up to 300 passes so older connections past top 760 are reached!)
                if queue_pending < 5:
                    refill_connections_queue(page, conn, min_needed=30, max_scrolls=300)

                # STAGE A: 1st-Degree Messaging (Randomized batch size & <2 min random gap, 1 task at a time)
                if daily_1st_sent < DAILY_MAX_1ST_DEGREE:
                    cur.execute('''
                        SELECT id, name, headline FROM linkedin_connections_queue 
                        WHERE status='pending' 
                        ORDER BY created_at DESC 
                        LIMIT 1
                    ''')
                    row = cur.fetchone()
                    if row:
                        username, name, headline = row
                        first_name = name.split()[0] if name else "there"
                        print(f"[Cycle:StageA] Batch #{batch_num} Msg {batch_msg_count + 1}/{batch_target} -> {name} ({headline[:40]}...)")
                        success = turbo_send_1st_degree(page, username, first_name)
                        if not sync_control_with_cloud().get("linkedin_enabled", True):
                            continue
                        if success:
                            cur.execute('''
                                INSERT OR REPLACE INTO linkedin_connections_outreach (id, name, headline, status, sent_at)
                                VALUES (?, ?, ?, 'sent', CURRENT_TIMESTAMP)
                            ''', (username, name, headline))
                            cur.execute("UPDATE linkedin_connections_queue SET status='sent' WHERE id=?", (username,))
                            conn.commit()
                            messages_sent_session += 1
                            batch_msg_count += 1
                            _sync_linkedin_outreach_to_cloud(conn)
                            try:
                                from src.services.system_alerter import clear_system_alert
                                clear_system_alert("LinkedIn")
                            except Exception:
                                pass

                            if messages_sent_session == 1 or batch_msg_count in (1, batch_target):
                                send_discord_cycle_note(
                                    f"📨 **Stage A — LinkedIn Message Sent ({batch_msg_count}/{batch_target} in Batch #{batch_num})**\n"
                                    f"• Recipient: **{name}** (`{username}`)\n"
                                    f"• Attachment: CV PDF + Drive Link | Verified composer cleared ✅"
                                )

                            if batch_msg_count >= batch_target:
                                print(f"[Cycle:StageA] Completed {batch_msg_count}/{batch_target} messages in Batch #{batch_num}! Entering randomized inter-batch cycle...")
                                run_inter_batch_activities(page, context, conn, batch_num=batch_num)
                                batch_msg_count = 0
                                batch_target = random.randint(8, 11)
                                batch_num += 1
                            else:
                                # Randomized wait strictly under 2 minutes (42s - 115s)
                                delay_s = random.uniform(42.0, 115.0)
                                print(f"[Cycle:StageA] Random <2m gap: resting {int(delay_s)}s before Message {batch_msg_count + 1}/{batch_target}...")
                                _sleep_respecting_control(delay_s)
                        else:
                            cur.execute("UPDATE linkedin_connections_queue SET status='skipped' WHERE id=?", (username,))
                            conn.commit()
                            time.sleep(random.uniform(2.0, 5.5))
                        continue
                    else:
                        print("[Cycle:StageA] Queue empty after refill attempt; running randomized inter-batch stages...")
                        run_inter_batch_activities(page, context, conn, batch_num=batch_num)
                        batch_target = random.randint(8, 11)
                        batch_num += 1
                        continue
                else:
                    print(f"[Cycle:StageA] Daily safe cap ({DAILY_MAX_1ST_DEGREE}) reached for 1st-degree messages. Continuing randomized Feed + Jobs loop...")
                    run_inter_batch_activities(page, context, conn, batch_num=batch_num)
                    batch_num += 1
                    continue

            except Exception as iter_e:
                print(f"[Cycle:Error] Iteration error caught: {iter_e}")
                try:
                    from src.services.system_alerter import send_system_alert
                    send_system_alert(
                        platform="LinkedIn",
                        issue_type="runtime_error",
                        summary="LinkedIn master cycle encountered a browser/CDP or runtime error.",
                        fix_steps="Check Ridealong (`http://127.0.0.1:8083`) and ensure Chrome/Ridealong is awake (`ridealong status`). Auto-retrying in 20s.",
                        details=str(iter_e),
                        cooldown_minutes=30,
                    )
                except Exception:
                    pass
                time.sleep(20)

if __name__ == "__main__":
    run_master_cycle(interval_minutes=1, start_inter_batch=("--inter-batch" in sys.argv))
