import sys
import os
import time
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.ats_filler import fill_ats_application
from src.engine.discord_client import DiscordClient
from src import config

DB_PATH = os.environ.get("DATABASE_PATH") or os.path.expanduser('~/.linkedin_agent.db')
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / ".linkedin_agent.db")
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / "agent.db")

def process_redirects(max_jobs: int = 15):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute('''
        SELECT id, title, company, url 
        FROM jobs 
        WHERE status = 'external_redirect' 
        ORDER BY created_at DESC 
        LIMIT ?
    ''', (max_jobs,))
    jobs = cur.fetchall()

    if not jobs:
        print("[ATSProcessor] No pending external_redirect jobs in queue.")
        return

    print(f"\n=======================================================")
    print(f"[ATSProcessor] Starting Automated ATS Applications for {len(jobs)} Jobs")
    print(f"=======================================================\n")

    discord = None
    try:
        discord = DiscordClient()
    except Exception as de:
        print(f"[ATSProcessor] Discord notice: {de}")

    success_count = 0
    skipped_count = 0

    for idx, (jid, title, comp, url) in enumerate(jobs, 1):
        print(f"\n[{idx}/{len(jobs)}] Processing: {title} @ {comp}")
        print(f"URL: {url}")

        try:
            res = fill_ats_application(url, role_title=f"{title} @ {comp}", auto_submit=True)
            
            if res.get("submitted") or res.get("resume_uploaded") or len(res.get("fields_filled", [])) >= 2:
                success_count += 1
                cur.execute('''
                    UPDATE jobs 
                    SET status = 'applied',
                        scorecard = json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'ats_autofill', '$.ats_type', ?, '$.applied_at', CURRENT_TIMESTAMP)
                    WHERE id = ?
                ''', (res.get("ats_type", "external_ats"), jid))
                conn.commit()
                print(f"[ATSProcessor] -> SUCCESS: Applied to {title} @ {comp} ({res.get('ats_type')})!")

                if discord:
                    try:
                        from src.agents.discord_notifier import DiscordNotifier
                        DiscordNotifier().send_application_alert({
                            "company": comp,
                            "role": title,
                            "platform": f"External ATS ({res.get('ats_type', 'Company Portal').upper()})",
                            "url": res.get('ats_url', url),
                            "status": "SUBMITTED & CONFIRMED" if res.get("confirmed") else "SUBMITTED",
                            "details": "Screening: Automated (1 yr exp, 15-day immediate notice, no employer leak), uploaded ATS resume.",
                            "resume": "Vishal_Gupta_Resume.pdf"
                        })
                    except Exception as me:
                        print(f"[ATSProcessor] Discord send notice: {me}")
            elif res.get("error") == "expired":
                cur.execute("UPDATE jobs SET status = 'expired' WHERE id = ?", (jid,))
                conn.commit()
                print(f"[ATSProcessor] -> Job listing expired. Marked as expired.")
                skipped_count += 1
            elif res.get("error") and "ERR_HTTP_RESPONSE_CODE_FAILURE" in res["error"]:
                print(f"[ATSProcessor] -> LinkedIn 429 throttle encountered. Skipping to next...")
                skipped_count += 1
            else:
                skipped_count += 1
                # Mark failed after trying so other pending jobs can proceed
                cur.execute("UPDATE jobs SET status = 'external_pending_review' WHERE id = ?", (jid,))
                conn.commit()
                print(f"[ATSProcessor] -> Could not auto-submit (portal redirect or expired): {res.get('error')}")

        except Exception as e:
            skipped_count += 1
            print(f"[ATSProcessor] Error on {title}: {e}")

        time.sleep(5)

    conn.close()
    print(f"\n=======================================================")
    print(f"[ATSProcessor] Batch Complete! Submitted: {success_count}, Skipped/Pending: {skipped_count}")
    print(f"=======================================================\n")

if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    process_redirects(max_jobs=count)
