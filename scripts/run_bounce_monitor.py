import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.sync_leads_to_sheet import DB_PATH, export_split_leads
from src.services.reply_monitor import check_gmail_replies, sweep_all_inboxes_for_bounces


def _get_blocklist_and_lead_counts():
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bounced_blocklist (
                email TEXT PRIMARY KEY,
                reason TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        blk = conn.execute("SELECT COUNT(*) FROM bounced_blocklist").fetchone()[0]
        tot = conn.execute("SELECT COUNT(*) FROM outreach_leads").fetchone()[0]
        sent = conn.execute("SELECT COUNT(*) FROM outreach_leads WHERE status = 'sent'").fetchone()[0]
        conn.close()
        return blk, tot, sent
    except Exception:
        return 0, 0, 0


def run_continuous_bounce_monitor(poll_seconds: int = 25):
    print("=" * 72)
    print("🧹 24/7 BOUNCE & REPLY MONITOR ACTIVE (NSUT + Gmail IMAP + DB/Sheet Purge)")
    print(f"Polling every {poll_seconds}s | Auto-deletes bounce emails + purges DB & sheets")
    print("=" * 72)

    # Initial cleanup & sheet sync on boot
    init_res = sweep_all_inboxes_for_bounces()
    export_split_leads()
    last_blk, last_tot, last_sent = _get_blocklist_and_lead_counts()
    print(
        f"[BounceMonitor:Init] Wiped {init_res['inbox_deleted']} inbox bounce(s), "
        f"active leads={last_tot} (sent={last_sent}), blocked={last_blk}."
    )

    cycle = 0
    while True:
        try:
            time.sleep(poll_seconds)
            cycle += 1

            res = sweep_all_inboxes_for_bounces()
            cur_blk, cur_tot, cur_sent = _get_blocklist_and_lead_counts()

            # Regenerate sheets immediately if any bounce was deleted OR every 8th pass (~3m) if sent count moved
            bounced_changed = (res["db_deleted"] > 0) or (cur_blk != last_blk) or (cur_tot < last_tot)
            periodic_sent_refresh = (cycle % 8 == 0) and (cur_sent != last_sent)

            if bounced_changed or periodic_sent_refresh:
                export_split_leads()
                last_blk, last_tot, last_sent = _get_blocklist_and_lead_counts()

            # Also check for genuine recruiter replies every 4th pass (~100s)
            if cycle % 4 == 0:
                replies = check_gmail_replies()
                if replies:
                    export_split_leads()
                    last_blk, last_tot, last_sent = _get_blocklist_and_lead_counts()
        except Exception as e:
            print(f"[BounceMonitor:Error] {e}")
            time.sleep(15)


if __name__ == "__main__":
    run_continuous_bounce_monitor(poll_seconds=25)
