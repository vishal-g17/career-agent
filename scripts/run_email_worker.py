import time
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.outreach_campaign import send_outreach_batch, get_campaign_stats

def run_continuous_email_worker(batch_size: int = 20, pause_between_batches: int = 15):
    print("="*70)
    print("🚀 DEDICATED COLD EMAIL WORKER RUNNING IN PARALLEL (Dual-Sender SMTP)")
    print(f"Batch Size: {batch_size} | Pacing: 10–14s | Alternating NSUT & Gmail")
    print("="*70)

    pass_num = 0
    while True:
        try:
            pass_num += 1
            print(f"\n[EmailWorker:Pass #{pass_num}] Initiating batch of {batch_size} verified emails...")
            res = send_outreach_batch(batch_size=batch_size, sender_mode="rotate")
            stats = get_campaign_stats()
            print(f"[EmailWorker:Pass #{pass_num}] Complete: Sent {res['sent']}, Bounced {res['bounced']}. Total Sent: {stats.get('sent_total', 0)}, Pending: {stats.get('pending', 0)}")

            if stats.get('pending', 0) == 0:
                print("[EmailWorker] All pending corporate leads delivered! Resting 5 minutes for new connections...")
                time.sleep(300)
            else:
                time.sleep(pause_between_batches)
        except Exception as e:
            print(f"[EmailWorker:Error] {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_continuous_email_worker(batch_size=20, pause_between_batches=15)
