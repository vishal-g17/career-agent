import sqlite3
import re
import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.exclusion_filter import is_excluded

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')

def populate_queue(scrolls: int = 15):
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
    conn.commit()

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/queue-builder")
        page = b.contexts[0].new_page()
        print("Navigating to connections page...")
        page.goto("https://www.linkedin.com/mynetwork/invite-connect/connections/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        # Scroll to load connections using mouse wheel
        for s in range(scrolls):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1000)

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
        extracted = page.evaluate(js_extract)
        print(f"Extracted {len(extracted)} connections from live DOM.")

        inserted = 0
        excluded_count = 0
        for item in extracted:
            uname = item["username"]
            full_text = item["text"]
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            name = lines[0] if lines else uname
            headline = lines[1] if len(lines) > 1 else ""

            exclusion = is_excluded(f"{name} {headline}")
            cur.execute("SELECT id FROM linkedin_connections_outreach WHERE id=?", (uname,))
            if cur.fetchone():
                continue # already messaged

            status = 'excluded' if exclusion else 'pending'
            cur.execute('''
                INSERT OR REPLACE INTO linkedin_connections_queue (id, name, headline, exclusion_reason, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (uname, name, headline, exclusion, status))
            if exclusion:
                excluded_count += 1
            else:
                inserted += 1

        conn.commit()
        print(f"Queued {inserted} valid, eligible connections for messaging. (Excluded {excluded_count} via E1/E2/E3)")
        
        cur.execute("SELECT count(*) FROM linkedin_connections_queue WHERE status='pending'")
        print("Total pending in queue:", cur.fetchone()[0])
        conn.close()
        page.close()

if __name__ == "__main__":
    populate_queue(scrolls=35)
