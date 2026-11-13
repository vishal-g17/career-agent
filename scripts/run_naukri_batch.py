import sys
import os
import time
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.naukri_filler import apply_to_naukri_job
from src.engine.discord_client import DiscordClient

DB_PATH = os.environ.get("DATABASE_PATH") or os.path.expanduser('~/.linkedin_agent.db')
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / ".linkedin_agent.db")
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / "agent.db")

DISCORD_CHANNEL = "1569246471251959838"

def run_naukri_batch(limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute('''
        SELECT id, title, company, url 
        FROM jobs 
        WHERE url LIKE "%naukri.com%" AND status = "new"
        ORDER BY created_at DESC
        LIMIT ?
    ''', (limit,))
    
    jobs = cur.fetchall()
    print(f"[NaukriBatch] Fetched {len(jobs)} pending Naukri jobs to process...")

    applied_count = 0
    redirect_count = 0
    failed_count = 0

    discord = None
    try:
        discord = DiscordClient()
    except Exception as de:
        print(f"[NaukriBatch] Discord client init error: {de}")

    for idx, (jid, title, company, url) in enumerate(jobs, 1):
        print(f"\n=======================================================")
        print(f"[{idx}/{len(jobs)}] Processing: {title} @ {company}")
        print(f"URL: {url}")
        print(f"=======================================================")

        try:
            res = apply_to_naukri_job(url, f"{title} @ {company}")
            status = res.get("status")

            if res.get("applied") or status in ["applied", "already_applied", "external_applied"]:
                applied_count += 1
                cur.execute('''
                    UPDATE jobs 
                    SET status = "applied", 
                        scorecard = json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'naukri_auto_apply', '$.applied_at', CURRENT_TIMESTAMP)
                    WHERE id = ?
                ''', (jid,))
                conn.commit()
                print(f"[NaukriBatch] -> SUCCESS: Applied to {title} @ {company}!")

                if discord:
                    try:
                        from src.agents.discord_notifier import DiscordNotifier
                        DiscordNotifier().send_application_alert({
                            "company": company,
                            "role": title,
                            "platform": "Naukri (Auto-Apply)",
                            "url": url,
                            "status": "SUBMITTED & CONFIRMED",
                            "details": f"Type: {'External ATS Auto-Apply' if status == 'external_applied' else ('1-Click Direct Apply' if not res.get('questions_answered') else 'Chatbot Screening Completed')}",
                            "resume": "Vishal_Gupta_Resume.pdf"
                        })
                    except:
                        pass

            elif status == "external_redirect":
                redirect_count += 1
                cur.execute('UPDATE jobs SET status = "external_redirect" WHERE id = ?', (jid,))
                conn.commit()
                print(f"[NaukriBatch] -> External company site redirect (marked as external_redirect).")

            elif status == "expired":
                failed_count += 1
                cur.execute('UPDATE jobs SET status = "expired" WHERE id = ?', (jid,))
                conn.commit()
                print(f"[NaukriBatch] -> Expired job listing marked as expired.")

            else:
                failed_count += 1
                cur.execute('UPDATE jobs SET status = "failed" WHERE id = ?', (jid,))
                conn.commit()
                print(f"[NaukriBatch] -> Failed or no apply button: {res.get('error') or status} (marked as failed).")

        except Exception as e:
            failed_count += 1
            cur.execute('UPDATE jobs SET status = "failed" WHERE id = ?', (jid,))
            conn.commit()
            print(f"[NaukriBatch] -> Error processing job: {e} (marked as failed).")

        # Humanized delay between applications
        time.sleep(5)

    print(f"\n[NaukriBatch] Completed! Applied: {applied_count}, External Redirects: {redirect_count}, Failed/Skipped: {failed_count}")

if __name__ == "__main__":
    limit = 50
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    run_naukri_batch(limit)
