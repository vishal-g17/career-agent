#!/usr/bin/env python3
"""
On-demand LinkedIn Person Search & Verified Outreach Sender (Local Mac Ridealong).
Finds a requested person (e.g., "Shivani Gupta CARS24 Associate") on LinkedIn:
  - If 1st-degree connection (or open composer): sends verified direct pitch + attached CV PDF + Drive link.
  - If 2nd/3rd-degree (direct message locked): sends connection invite + queues auto-pitch on acceptance.
"""

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.exclusion_filter import FORBIDDEN_COMPANIES
from src.services.linkedin_control import is_linkedin_logged_out

DB_PATH = os.path.expanduser("~/.linkedin_agent.db")
RIDEALONG_CDP = "http://127.0.0.1:9230/default/adhoc-person-msg"
CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"
PDF_PATH = str(PROJECT_ROOT / "data" / "resume.pdf")


def search_candidates_on_linkedin(page, query: str) -> List[Dict[str, Any]]:
    """Searches LinkedIn People results for `query` and returns candidate profiles."""
    q_enc = urllib.parse.quote(query)
    url = f"https://www.linkedin.com/search/results/people/?keywords={q_enc}"
    page.goto(url, wait_until="domcontentloaded", timeout=28000)
    page.wait_for_timeout(3800)
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
            if (!uname || uname.startsWith('ACoAA') && items.length > 8 || seen.has(uname)) continue;
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


def pick_best_candidate(candidates: List[Dict[str, Any]], name_hint: str, company_hint: str = "", role_hint: str = "") -> Dict[str, Any]:
    """Scores candidate cards against name, company, and role hints."""
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


def send_person_outreach(query: str, name_hint: str = "", company_hint: str = "", role_hint: str = "", dry_run: bool = False) -> Dict[str, Any]:
    subprocess.run(["ridealong", "wake", "default"], capture_output=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
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
                    "message": f"Could not find a matching LinkedIn profile for `{query}`.",
                    "candidates_seen": [c["full_text"] for c in candidates[:5]],
                }

            uname = chosen["username"]
            # Visit profile first so we resolve canonical /in/<slug>/, real display name, headline, and connection degree
            page.goto(f"https://www.linkedin.com/in/{uname}/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            try:
                page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')
            except Exception:
                pass

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
                        headline: lines.slice(1, 4).join(' | '),
                        top_text: topText.slice(0, 600)
                    };
                }
            """)
            display_name = prof_info.get("name") or name_hint or uname
            headline = prof_info.get("headline") or chosen["full_text"][:160]
            first_name = display_name.split()[0] if display_name else "there"

            if any(fc in f"{display_name} {headline}".lower() for fc in FORBIDDEN_COMPANIES):
                return {"ok": False, "action": "excluded", "message": "Target matches restricted company exclusion list."}

            # Attempt 1: Direct 1st-degree compose URL
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
                    "message": f"✅ Direct LinkedIn message + attached CV PDF sent to **{display_name}** (`{uname}` — {headline[:100]}).",
                }

            # Check if prior pitch was already sent to this 1st-degree connection
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
                    "message": f"ℹ️ Already sent LinkedIn message + CV to **{display_name}** (`{uname}`) on `{already[0]}` — skipped duplicate.",
                }

            # Attempt 2: Not a 1st-degree connection (2nd/3rd-degree) -> Send Connection Request with short note + queue auto-pitch on accept!
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
                        f"• Sent a **Connection Request** to **{display_name}** ({headline[:100]}) and queued automatic full CV message the moment she accepts!"
                    ),
                }
            elif invited == "LIMIT_REACHED":
                return {
                    "ok": False,
                    "action": "weekly_invite_limit",
                    "username": uname,
                    "name": display_name,
                    "headline": headline,
                    "profile_url": f"https://www.linkedin.com/in/{uname}/",
                    "message": (
                        f"⚠️ Found **{display_name}** (`https://www.linkedin.com/in/{uname}/` — {headline[:100]}), "
                        "but she is a **2nd/3rd-degree** profile (not yet a 1st-degree connection, so free direct DM is locked) and LinkedIn's weekly connection invite cap is currently active."
                    ),
                }
            else:
                return {
                    "ok": False,
                    "action": "dm_locked_no_connect",
                    "username": uname,
                    "name": display_name,
                    "headline": headline,
                    "profile_url": f"https://www.linkedin.com/in/{uname}/",
                    "message": (
                        f"⚠️ Found **{display_name}** (`https://www.linkedin.com/in/{uname}/` — {headline[:100]}), "
                        "but direct messaging is locked (not a 1st-degree connection / connection pending or Follow-only profile)."
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    res = send_person_outreach(
        query=args.query,
        name_hint=args.name,
        company_hint=args.company,
        role_hint=args.role,
        dry_run=args.dry_run,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
