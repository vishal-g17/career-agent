#!/usr/bin/env python3
"""
Dedicated 24x7 Continuous Job Portal Worker (Naukri & Indeed)
Runs completely independently from LinkedIn.
Scrapes fresh APM, Product Analyst, and Business Analyst jobs from Naukri & Indeed,
auto-fills applications using candidate's updated profile (1 yr exp, 15-day notice, NSUT)
and uploads the new ATS LaTeX CV (Vishal_Gupta_Resume.pdf).
"""

import sys
import os
import time
import sqlite3
import random
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.job_hunter import run_job_hunt
from scripts.run_naukri_batch import run_naukri_batch
from scripts.process_external_redirects import process_redirects

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')

def get_pending_portal_counts():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM jobs WHERE url LIKE '%naukri.com%' AND status = 'new'")
    naukri_pending = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM jobs WHERE status = 'external_redirect'")
    redirects_pending = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM jobs WHERE status = 'applied'")
    total_applied = cur.fetchone()[0]
    conn.close()
    return naukri_pending, redirects_pending, total_applied

def run_portal_worker():
    print("\n" + "="*80)
    print("🚀 DEDICATED CONTINUOUS JOB PORTAL WORKER (Naukri & Indeed / ATS)")
    print("Running 100% in parallel — completely independent from LinkedIn")
    print("Using updated ATS CV: Vishal_Gupta_Resume.pdf (1 yr exp, 15-day notice, NSUT)")
    print("="*80 + "\n")

    iteration = 0
    while True:
        iteration += 1
        now_str = datetime.now().strftime("%I:%M %p")
        naukri_p, redir_p, applied_tot = get_pending_portal_counts()
        print(f"\n[PortalWorker:Sweep #{iteration} | {now_str}] Pending Naukri: {naukri_p} | Pending ATS/Indeed: {redir_p} | Total Applied: {applied_tot}")

        # 1. Scrape new jobs every 3 sweeps (~10-15 mins) or when pending pool is low
        if iteration == 1 or iteration % 3 == 0 or (naukri_p < 3 and redir_p < 3):
            print("[PortalWorker] Hunting fresh APM / Product / Business Analyst listings on Naukri & Indeed...")
            try:
                hunt_res = run_job_hunt()
                print(f"[PortalWorker] Hunt complete: Scraped {hunt_res.get('total_scraped', 0)} jobs, Saved {hunt_res.get('saved_to_db', 0)} fresh matches.")
            except Exception as he:
                print(f"[PortalWorker] Hunt notice: {he}")

        # 2. Process pending direct Naukri applications
        try:
            print("[PortalWorker] Processing Naukri applications batch...")
            run_naukri_batch(limit=5)
        except Exception as nbe:
            print(f"[PortalWorker] Naukri batch note: {nbe}")

        # 3. Process external ATS & Indeed redirects
        try:
            print("[PortalWorker] Processing ATS & Indeed redirects batch...")
            process_redirects(max_jobs=5)
        except Exception as pre:
            print(f"[PortalWorker] ATS batch note: {pre}")

        # Safe human pacing between portal sweeps
        sleep_s = random.uniform(120, 180) # 2-3 mins
        print(f"[PortalWorker] Sweep complete. Resting {int(sleep_s)}s before next portal sweep...")
        time.sleep(sleep_s)

if __name__ == "__main__":
    run_portal_worker()
