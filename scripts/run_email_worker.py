import time
import os
import sys
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.outreach_campaign import send_outreach_batch, get_campaign_stats

def run_continuous_email_worker(batch_size: int = 20, pause_between_batches: int = 15):
    print("="*70)
    print("🚀 DEDICATED COLD EMAIL WORKER RUNNING IN PARALLEL (Dual-Sender SMTP)")
    print(f"Caps: NSUT 1,500/day + Gmail 500/day | Random Timer: <20s (4.5s–19.5s)")
    print("="*70)

    pass_num = 0
    while True:
        try:
            pass_num += 1
            print(f"\n[EmailWorker:Pass #{pass_num}] Initiating batch of {batch_size} verified emails...")
            res = send_outreach_batch(batch_size=batch_size, sender_mode="rotate")
            stats = get_campaign_stats()
            print(f"[EmailWorker:Pass #{pass_num}] Complete: Sent {res['sent']}, Bounced {res['bounced']}. Total Sent: {stats.get('sent_total', 0)}, Pending: {stats.get('pending', 0)}")

            if res.get("daily_cap_reached"):
                print("[EmailWorker] Daily cap (500 Gmail + 1,500 NSUT) reached for today IST. Resting 10 minutes...")
                time.sleep(600)
            elif stats.get('pending', 0) == 0:
                print("[EmailWorker] All pending corporate leads delivered! Resting 5 minutes for new connections...")
                time.sleep(300)
            else:
                batch_wait = round(random.uniform(5.0, 19.5), 1)
                time.sleep(batch_wait)
        except Exception as e:
            print(f"[EmailWorker:Error] {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_continuous_email_worker(batch_size=20, pause_between_batches=15)
