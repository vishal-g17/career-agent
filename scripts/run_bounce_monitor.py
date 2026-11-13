import sqlite3
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.sync_leads_to_sheet import DB_PATH, export_split_leads
from src.services.reply_monitor import check_gmail_replies, sweep_all_inboxes_for_bounces

_sheet_lock = threading.Lock()


def _trigger_sheet_refresh_async():
    if _sheet_lock.locked():
        return

    def _worker():
        with _sheet_lock:
            try:
                export_split_leads()
            except Exception as e:
                print(f"[BounceMonitor:SheetSyncWarn] {e}")

    threading.Thread(target=_worker, daemon=True).start()


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


def run_continuous_bounce_monitor(poll_seconds: int = 6):
    print("=" * 72)
    print("⚡ INSTANT 24/7 BOUNCE DELETER ACTIVE (NSUT + Gmail Parallel IMAP)")
    print(f"Polling every {poll_seconds}s | Pipelined 16KB fetch + instant INBOX & Trash wipe + DB/Sheet delete")
    print("=" * 72)

    init_res = sweep_all_inboxes_for_bounces()
    export_split_leads()
    last_blk, last_tot, last_sent = _get_blocklist_and_lead_counts()
    print(
        f"[BounceMonitor:Init] Wiped {init_res['inbox_deleted']} INBOX bounce(s) + {init_res.get('trash_deleted', 0)} Trash bounce(s), "
        f"active leads={last_tot} (sent={last_sent}), total purged bounces={last_blk}."
    )

    cycle = 0
    while True:
        try:
            time.sleep(poll_seconds)
            cycle += 1

            res = sweep_all_inboxes_for_bounces()
            cur_blk, cur_tot, cur_sent = _get_blocklist_and_lead_counts()

            bounced_changed = (res["db_deleted"] > 0) or (cur_blk != last_blk) or (cur_tot < last_tot)
            periodic_sent_refresh = (cycle % 20 == 0) and (cur_sent != last_sent)

            if bounced_changed or periodic_sent_refresh:
                last_blk, last_tot, last_sent = cur_blk, cur_tot, cur_sent
                _trigger_sheet_refresh_async()

            # Check for genuine recruiter replies every ~2 minutes (every 20th 6s pass)
            if cycle % 20 == 0:
                threading.Thread(target=check_gmail_replies, daemon=True).start()
        except Exception as e:
            print(f"[BounceMonitor:Error] {e}")
            time.sleep(6)


if __name__ == "__main__":
    run_continuous_bounce_monitor(poll_seconds=6)
