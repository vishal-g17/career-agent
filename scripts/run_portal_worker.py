#!/usr/bin/env python3
"""
Turbo 24x7 Continuous Job Portal Worker (Direct ATS: Greenhouse, Lever, Ashby + Naukri)
Reuses shared Playwright Chromium per batch and polls fast with zero dead sleep.
"""

import os
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.process_external_redirects import process_redirects
from scripts.run_naukri_batch import run_naukri_batch
from src.agents.job_hunter import run_job_hunt
from src.services.instahyre_service import run_instahyre_auto_apply

DB_PATH = os.environ.get("DATABASE_PATH") or os.path.expanduser("~/.linkedin_agent.db")
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / ".linkedin_agent.db")
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / "agent.db")


def get_pending_portal_counts():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM jobs WHERE url LIKE '%naukri.com%' AND status = 'new'")
    naukri_pending = cur.fetchone()[0]
    cur.execute(
        """
        SELECT count(*) FROM jobs
        WHERE (
            status = 'external_redirect'
            OR (status = 'new' AND url NOT LIKE '%naukri.com%' AND url NOT LIKE '%linkedin.com%' AND url NOT LIKE '%instahyre.com%' AND COALESCE(match_score, 80) >= 70)
        )
        AND url NOT LIKE '%indeed.com%'
        AND url NOT LIKE '%instahyre.com%'
        """
    )
    redirects_pending = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM jobs WHERE status = 'applied'")
    total_applied = cur.fetchone()[0]
    conn.close()
    return naukri_pending, redirects_pending, total_applied


def run_portal_worker():
    print("\n" + "=" * 80)
    print("⚡ TURBO 24/7 JOB PORTAL WORKER (Instahyre + Greenhouse + Lever + Ashby + Naukri)")
    print("Zero-RAM Instahyre REST Auto-Apply | Shared-Chromium ATS Batching | Fast Pacing")
    print("=" * 80 + "\n")

    iteration = 0
    # Start after a 20m initial breather for Instahyre so any sliding 429 window clears completely, then poll every 25m
    next_instahyre_ts = time.time() + 1200.0
    while True:
        iteration += 1
        now_str = datetime.now().strftime("%I:%M %p")
        naukri_p, redir_p, applied_tot = get_pending_portal_counts()
        print(
            f"\n[PortalWorker:Sweep #{iteration} | {now_str}] "
            f"Pending Naukri: {naukri_p} | Pending Direct ATS: {redir_p} | Total Applied: {applied_tot}"
        )

        # 0. Run 24/7 Instahyre Auto-Discovery + Verified Auto-Apply every 25m (prevents HTTP 429 rate-limiting!)
        if time.time() >= next_instahyre_ts:
            try:
                ih_res = run_instahyre_auto_apply(max_apps=10)
                if ih_res.get("applied", 0) > 0:
                    print(f"[PortalWorker] Instahyre confirmed {ih_res['applied']} new application(s) this sweep!")
                next_instahyre_ts = time.time() + 1500.0
            except Exception as ihe:
                print(f"[PortalWorker] Instahyre sweep note: {ihe}")
                try:
                    from src.services.system_alerter import send_system_alert
                    send_system_alert(
                        platform="Instahyre",
                        issue_type="runtime_error",
                        summary="Unhandled exception during 24/7 Cloud Instahyre sweep.",
                        fix_steps="Check `journalctl -u linkedin-portal-worker` on Cloud VM (`130.210.108.55`).",
                        details=str(ihe),
                    )
                except Exception:
                    pass
                next_instahyre_ts = time.time() + 1800.0

        # 1. Discover fresh openings on startup, when queue is low, or every 4 sweeps
        if iteration == 1 or iteration % 4 == 0 or (naukri_p == 0 and redir_p < 5):
            print("[PortalWorker] Running fast parallel API job discovery (160+ Greenhouse/Lever/Ashby boards)...")
            try:
                hunt_res = run_job_hunt()
                print(
                    f"[PortalWorker] Discovery complete: Scanned {hunt_res.get('total_scraped', 0)} jobs, "
                    f"Saved {hunt_res.get('saved_to_db', 0)} fresh matches."
                )
            except Exception as he:
                print(f"[PortalWorker] Discovery notice: {he}")

        # 2. Process pending Naukri applications periodically (stops immediately if datacenter IP is gated)
        naukri_p, redir_p, _ = get_pending_portal_counts()
        if naukri_p > 0 and (iteration == 1 or iteration % 30 == 0):
            try:
                run_naukri_batch(limit=15)
            except Exception as nbe:
                print(f"[PortalWorker] Naukri batch note: {nbe}")

        # 3. Process direct external ATS forms (shared Chromium, batch of 20)
        if redir_p > 0:
            try:
                process_redirects(max_jobs=20)
            except Exception as pre:
                print(f"[PortalWorker] ATS batch note: {pre}")

        _, rem_redir, _ = get_pending_portal_counts()
        sleep_s = random.uniform(10, 18) if rem_redir > 0 else 60.0
        print(f"[PortalWorker] Sweep complete. Next turbo pass in {int(sleep_s)}s...")
        time.sleep(sleep_s)


if __name__ == "__main__":
    run_portal_worker()
