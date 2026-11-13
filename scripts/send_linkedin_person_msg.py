#!/usr/bin/env python3
"""
Priority #0 On-Demand LinkedIn Person Search & Immediate Sender (Local Mac Chrome CDP).
Supports BOTH:
  1) Custom exact messages (`--custom-message "Hello pagal"`) — sent immediately with zero duplicate block!
  2) Standard job outreach pitch + attached CV PDF + Drive link.
  3) Direct fast lookup in local SQLite (`linkedin_connections_outreach` / `linkedin_connections_queue`)
     before falling back to live LinkedIn People Search.
"""

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.exclusion_filter import FORBIDDEN_COMPANIES
from src.services.linkedin_control import is_linkedin_logged_out

DB_PATH = os.path.expanduser("~/.linkedin_agent.db")
DEVTOOLS_PORT_FILE = Path.home() / ".ridealong" / "workspaces" / "default" / "profile" / "DevToolsActivePort"
RIDEALONG_CDP = "http://127.0.0.1:9230/default/adhoc-person-msg"
CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"
PDF_PATH = str(PROJECT_ROOT / "data" / "resume.pdf")


def get_direct_cdp_endpoint() -> str:
    """Wakes Ridealong workspace and returns the proven isolated agent CDP endpoint."""
    subprocess.run(["ridealong", "wake", "default"], capture_output=True)
    return RIDEALONG_CDP


def lookup_cached_connection(name_hint: str, company_hint: str = "") -> Optional[Dict[str, str]]:
    """Fast 1ms lookup in local SQLite if we already have this 1st-degree connection cached."""
    if not name_hint or not os.path.exists(DB_PATH):
        return None
    name_l = name_hint.strip().lower()
    comp_l = company_hint.strip().lower()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        cur = conn.cursor()
        matches = []
        for tbl in ("linkedin_connections_outreach", "linkedin_connections_queue"):
            try:
                rows = cur.execute(
                    f"SELECT id, name, COALESCE(headline, '') FROM {tbl} WHERE LOWER(name) LIKE ?",
                    (f"%{name_l}%",),
                ).fetchall()
                for uid, nm, hl in rows:
                    if uid and not uid.startswith("ACoAA"):
                        matches.append({"username": uid, "name": nm or name_hint, "headline": hl or ""})
            except Exception:
                pass
        conn.close()
        if not matches:
            return None
        # Deduplicate preserving order
        uniq = {}
        for m in matches:
            uniq.setdefault(m["username"], m)
        cands = list(uniq.values())
        if comp_l:
            comp_Filtered = [c for c in cands if comp_l in c["headline"].lower()]
            if len(comp_Filtered) == 1:
                return comp_Filtered[0]
            if comp_Filtered:
                return comp_Filtered[0]
        if len(cands) == 1 and cands[0]["name"].strip().lower() == name_l:
            return cands[0]
    except Exception:
        pass
    return None


def search_candidates_on_linkedin(page, query: str) -> List[Dict[str, Any]]:
    q_enc = urllib.parse.quote(query)
    url = f"https://www.linkedin.com/search/results/people/?keywords={q_enc}"
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(3200)
    if is_linkedin_logged_out(page):
        raise RuntimeError("LinkedIn session is logged out on Ridealong.")

    js = """
    () => {
        const items = [];
        const links = document.querySelectorAll('a[href*="/in/"]');
        const seen = new Set();
        for (const a of links) {
            const href = a.href || '';
            const idx = href.indexOf('/in/');
            if (idx === -1) continue;
            const uname = href.slice(idx + 4).split('/')[0].split('?')[0].trim();
            if (!uname || seen.has(uname)) continue;
            const card = a.closest('li, div[data-chameleon-result-urn], div[data-view-name*="search-entity"]') || a.parentElement?.parentElement?.parentElement;
            const rawText = ((card ? card.innerText : a.innerText) || '').trim();
            if (rawText.length < 6) continue;
            seen.add(uname);
            const lines = rawText.split('\\n').map(s => s.trim()).filter(Boolean);
            items.push({
                username: uname,
                lines: lines.slice(0, 8),
                full_text: lines.join(' | ').slice(0, 380)
            });
        }
        return items.slice(0, 12);
    }
    """
    return page.evaluate(js)


def pick_best_candidate(candidates: List[Dict[str, Any]], name_hint: str, company_hint: str = "", role_hint: str = "") -> Optional[Dict[str, Any]]:
    name_tokens = [t.lower() for t in re.split(r"\s+", name_hint.strip()) if len(t) >= 2]
    comp_tokens = [t.lower() for t in re.split(r"\s+", company_hint.strip()) if len(t) >= 2]
    role_tokens = [t.lower() for t in re.split(r"\s+", role_hint.strip()) if len(t) >= 2]

    best = None
    best_score = -1
    for c in candidates:
        txt_l = c["full_text"].lower()
        if any(fc in txt_l for fc in FORBIDDEN_COMPANIES):
            continue
        score = 0
        matched_name = sum(1 for t in name_tokens if t in txt_l)
        if name_tokens and matched_name == 0:
            continue
        score += matched_name * 25
        if name_hint.strip().lower() in txt_l:
            score += 30
        for ct in comp_tokens:
            if ct in txt_l:
                score += 35
        for rt in role_tokens:
            if rt in txt_l:
                score += 15
        if "1st" in c["full_text"]:
            score += 20
        if score > best_score:
            best_score = score
            best = c
    return best


def send_custom_direct_message(page, username: str, custom_text: str, attach_cv: bool = False) -> bool:
    """Sends an exact user-provided custom message immediately on Priority #0 (no duplicate skip)."""
    compose_url = f"https://www.linkedin.com/messaging/compose/?recipient={username}"
    page.goto(compose_url, wait_until="domcontentloaded", timeout=22000)
    composer = page.locator('div[role="textbox"], .msg-form__contenteditable').first
    try:
        composer.wait_for(state="visible", timeout=10000)
    except Exception:
        return False

    if attach_cv and os.path.exists(PDF_PATH):
        file_input = page.locator('input.msg-form__attachment-upload-input[accept*=".pdf"], .msg-form input[type="file"]').last
        try:
            if file_input.count() > 0:
                file_input.set_input_files(PDF_PATH)
                page.wait_for_timeout(1200)
        except Exception:
            pass

    composer.click()
    page.wait_for_timeout(350)
    page.keyboard.insert_text(custom_text)
    page.wait_for_timeout(500)

    send_btn = page.locator('button.msg-form__send-button, button[type="submit"]:has-text("Send"), button:has-text("Send")').first
    if send_btn.is_visible() and send_btn.is_enabled():
        send_btn.click()
    else:
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        if composer.inner_text().strip():
            page.keyboard.press("Meta+Enter")

    page.wait_for_timeout(1800)
    rem = ""
    try:
        rem = composer.inner_text().strip()
    except Exception:
        pass
    return len(rem) == 0


def send_person_outreach(
    query: str,
    name_hint: str = "",
    company_hint: str = "",
    role_hint: str = "",
    custom_message: str = "",
    attach_cv: Optional[bool] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    cdp_ep = get_direct_cdp_endpoint()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_ep, timeout=12000)
        except Exception:
            subprocess.run(["ridealong", "wake", "default"], capture_output=True)
            time.sleep(3)
            browser = p.chromium.connect_over_cdp(cdp_ep, timeout=15000)

        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            cached = None if dry_run else lookup_cached_connection(name_hint or query, company_hint)
            if cached:
                uname = cached["username"]
                display_name = cached["name"]
                headline = cached["headline"]
            else:
                candidates = search_candidates_on_linkedin(page, query)
                if not candidates and company_hint:
                    candidates = search_candidates_on_linkedin(page, f"{name_hint} {company_hint}".strip())
                if not candidates and name_hint:
                    candidates = search_candidates_on_linkedin(page, name_hint.strip())

                if dry_run:
                    return {"ok": True, "dry_run": True, "candidates": candidates}

                chosen = pick_best_candidate(candidates, name_hint or query, company_hint, role_hint)
                if not chosen:
                    return {
                        "ok": False,
                        "action": "not_found",
                        "message": f"❌ Could not find a matching LinkedIn profile for `{query}`.",
                        "candidates_seen": [c["full_text"] for c in candidates[:5]],
                    }

                uname = chosen["username"]
                page.goto(f"https://www.linkedin.com/in/{uname}/", wait_until="domcontentloaded", timeout=22000)
                page.wait_for_timeout(2200)
                canonical_url = page.url.split("?")[0].rstrip("/")
                if "/in/" in canonical_url:
                    uname = canonical_url.split("/in/")[1].split("/")[0]

                prof_info = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('main h1');
                        const topSec = document.querySelector('main section:first-of-type');
                        const topText = (topSec ? topSec.innerText : document.querySelector('main')?.innerText || '').trim();
                        const lines = topText.split('\\n').map(l => l.trim()).filter(Boolean);
                        return {
                            name: (h1 ? h1.innerText.trim() : lines[0] || ''),
                            headline: lines.slice(1, 4).join(' | ')
                        };
                    }
                """)
                display_name = prof_info.get("name") or name_hint or uname
                headline = prof_info.get("headline") or chosen["full_text"][:160]

            first_name = display_name.split()[0] if display_name else "there"

            if not custom_message and any(fc in f"{display_name} {headline}".lower() for fc in FORBIDDEN_COMPANIES):
                return {"ok": False, "action": "excluded", "message": "Target matches restricted company exclusion list."}

            # CASE 1: Custom exact message requested by user (e.g. "Hello pagal") -> send immediately!
            if custom_message.strip():
                ok_custom = send_custom_direct_message(
                    page, uname, custom_message.strip(), attach_cv=bool(attach_cv)
                )
                if ok_custom:
                    return {
                        "ok": True,
                        "action": "custom_message_sent",
                        "username": uname,
                        "name": display_name,
                        "headline": headline,
                        "profile_url": f"https://www.linkedin.com/in/{uname}/",
                        "message": (
                            f"✅ **Priority Message Sent Immediately!**\n"
                            f"• **To:** **{display_name}** (`{uname}`)\n"
                            f"• **Message:** `{custom_message.strip()}`\n"
                            f"• **Profile:** https://www.linkedin.com/in/{uname}/"
                        ),
                    }
                return {
                    "ok": False,
                    "action": "custom_message_failed",
                    "username": uname,
                    "name": display_name,
                    "message": (
                        f"⚠️ Found **{display_name}** (`https://www.linkedin.com/in/{uname}/`), "
                        "but LinkedIn direct message composer could not open (not a 1st-degree connection or DM locked)."
                    ),
                }

            # CASE 2: Standard Job Pitch + CV PDF
            from scripts.master_autonomous_cycle import turbo_send_1st_degree, send_poc_request, init_master_db
            init_master_db()

            sent_direct = turbo_send_1st_degree(page, uname, first_name)
            if sent_direct:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR REPLACE INTO linkedin_connections_outreach (id, name, headline, status, sent_at)
                    VALUES (?, ?, ?, 'sent', CURRENT_TIMESTAMP)
                    """,
                    (uname, display_name, headline),
                )
                cur.execute("UPDATE linkedin_connections_queue SET status='sent' WHERE id=?", (uname,))
                conn.commit()
                conn.close()
                return {
                    "ok": True,
                    "action": "direct_message_sent",
                    "username": uname,
                    "name": display_name,
                    "headline": headline,
                    "profile_url": f"https://www.linkedin.com/in/{uname}/",
                    "message": f"✅ **Priority LinkedIn Pitch + CV Sent Immediately!**\n• **To:** **{display_name}** (`{uname}` — {headline[:100]})\n• **Profile:** https://www.linkedin.com/in/{uname}/",
                }

            conn = sqlite3.connect(DB_PATH)
            already = conn.execute(
                "SELECT sent_at FROM linkedin_connections_outreach WHERE id=? AND status='sent'", (uname,)
            ).fetchone()
            conn.close()
            if already:
                return {
                    "ok": True,
                    "action": "already_messaged",
                    "username": uname,
                    "name": display_name,
                    "headline": headline,
                    "profile_url": f"https://www.linkedin.com/in/{uname}/",
                    "message": f"ℹ️ Prior pitch + CV was already sent to **{display_name}** (`{uname}`) on `{already[0]}`. (Pass a custom message if you want to send a follow-up text!)",
                }

            short_note = (
                f"Hi {first_name}, reaching out from NSUT Delhi (1 yr Product/Analytics exp). "
                f"Exploring APM / Product & Business Analyst roles at {company_hint or 'your team'}—would love to connect & share my CV!"
            )[:295]
            invited = send_poc_request(page, uname, display_name, custom_note=short_note)
            if invited is True:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO poc_connection_requests (id, name, headline, status, note, requested_at)
                    VALUES (?, ?, ?, 'requested', ?, CURRENT_TIMESTAMP)
                    """,
                    (uname, display_name, headline, short_note),
                )
                conn.commit()
                conn.close()
                return {
                    "ok": True,
                    "action": "connection_request_sent",
                    "username": uname,
                    "name": display_name,
                    "headline": headline,
                    "profile_url": f"https://www.linkedin.com/in/{uname}/",
                    "message": (
                        f"🤝 **{display_name}** (`{uname}`) is a **2nd/3rd-degree** profile (direct DM locked until connected).\n"
                        f"• Sent a **Connection Request** immediately and queued auto-CV message on acceptance!"
                    ),
                }
            return {
                "ok": False,
                "action": "dm_locked",
                "username": uname,
                "name": display_name,
                "message": (
                    f"⚠️ Found **{display_name}** (`https://www.linkedin.com/in/{uname}/` — {headline[:100]}), "
                    "but direct messaging is locked (not yet a 1st-degree connection)."
                ),
            }
        finally:
            try:
                page.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--company", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--custom-message", default="")
    parser.add_argument("--attach-cv", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    res = send_person_outreach(
        query=args.query,
        name_hint=args.name,
        company_hint=args.company,
        role_hint=args.role,
        custom_message=args.custom_message,
        attach_cv=args.attach_cv,
        dry_run=args.dry_run,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
