import time
import os
import sys
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.outreach_campaign import (
    send_outreach_batch,
    process_pending_followups,
    get_campaign_stats,
    is_followups_enabled,
)
from src.services.reply_monitor import check_gmail_replies

def run_continuous_email_worker(batch_size: int = 20, pause_between_batches: int = 15):
    print("="*70)
    print("🚀 DEDICATED COLD EMAIL + FOLLOW-UP WORKER RUNNING 24/7 (Dual-Sender SMTP)")
    print("Caps: NSUT 1,500/day + Gmail 500/day | Follow-ups gated by followup_control")
    print("="*70)

    pass_num = 0
    while True:
        try:
            pass_num += 1
            fu_active = is_followups_enabled()
            print(f"\n[EmailWorker:Pass #{pass_num}] Checking replies & fresh leads (Follow-ups: {'ENABLED' if fu_active else 'PAUSED until user command'})...")
            try:
                check_gmail_replies()
            except Exception as re_err:
                print(f"[EmailWorker:ReplyScanNote] {re_err}")

            res = send_outreach_batch(batch_size=batch_size, sender_mode="rotate")
            fu_res = process_pending_followups(max_followups=15) if fu_active else {"followup_1_sent": 0, "followup_2_sent": 0, "due_remaining": 0}
            stats = get_campaign_stats()
            print(
                f"[EmailWorker:Pass #{pass_num}] Complete: Fresh Sent {res['sent']}, "
                f"Follow-ups ({'ON' if fu_active else 'PAUSED'}): FU1={fu_res['followup_1_sent']}, FU2={fu_res['followup_2_sent']} | "
                f"Total Sent: {stats.get('sent_total', 0)}, Replied: {stats.get('replied', 0)}, Pending: {stats.get('pending', 0)}"
            )

            if res.get("daily_cap_reached"):
                print("[EmailWorker] Daily cap (500 Gmail + 1,500 NSUT) reached for today IST. Resting 10 minutes...")
                time.sleep(600)
            elif stats.get('pending', 0) == 0 and (not fu_active or fu_res.get("due_remaining", 0) == 0):
                print("[EmailWorker] Standing by (Pending=0, Follow-ups paused/clear). Resting 2 minutes...")
                time.sleep(120)
            else:
                batch_wait = round(random.uniform(5.0, 19.5), 1)
                time.sleep(batch_wait)
        except Exception as e:
            print(f"[EmailWorker:Error] {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_continuous_email_worker(batch_size=20, pause_between_batches=15)
