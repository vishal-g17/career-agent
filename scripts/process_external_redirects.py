import fcntl
import os
import sqlite3
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.discord_client import DiscordClient
from src.services.ats_filler import fill_ats_application

DB_PATH = os.environ.get("DATABASE_PATH") or os.path.expanduser("~/.linkedin_agent.db")
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / ".linkedin_agent.db")
if not os.path.exists(DB_PATH):
    DB_PATH = str(PROJECT_ROOT / "agent.db")


def process_redirects(max_jobs: int = 20):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()

    # Mark datacenter-login-walled raw indeed.com viewjob links as pending review immediately
    # so the headless worker never wastes 80s/job on Indeed's login redirect
    cur.execute(
        """
        UPDATE jobs
        SET status = 'external_pending_review'
        WHERE status IN ('new', 'external_redirect')
          AND url LIKE '%indeed.com%'
        """
    )
    conn.commit()

    cur.execute(
        """
        SELECT id, title, company, url
        FROM jobs
        WHERE (
            status = 'external_redirect'
            OR (
                status = 'new'
                AND url NOT LIKE '%naukri.com%'
                AND url NOT LIKE '%linkedin.com%'
                AND url NOT LIKE '%indeed.com%'
                AND url NOT LIKE '%instahyre.com%'
                AND COALESCE(match_score, 80) >= 70
            )
        )
        AND url NOT LIKE '%indeed.com%'
        AND url NOT LIKE '%instahyre.com%'
        ORDER BY
            CASE
                WHEN url LIKE '%greenhouse.io%' THEN 0
                WHEN url LIKE '%lever.co%' OR url LIKE '%ashbyhq.com%' THEN 1
                ELSE 2
            END ASC,
            created_at DESC
        LIMIT ?
        """,
        (max_jobs,),
    )
    jobs = cur.fetchall()

    if not jobs:
        conn.close()
        print("[ATSProcessor] No pending direct external ATS jobs in queue.")
        return 0

    print("\n=======================================================")
    print(f"[ATSProcessor] Turbo Shared-Browser Batch: Processing {len(jobs)} Direct ATS Jobs")
    print("=======================================================\n")

    discord = None
    try:
        discord = DiscordClient()
    except Exception as de:
        print(f"[ATSProcessor] Discord notice: {de}")

    success_count = 0
    skipped_count = 0

    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]
    if sys.platform != "darwin":
        launch_args.extend(
            [
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--disable-background-networking",
                "--renderer-process-limit=1",
                "--js-flags=--max-old-space-size=192",
                "--blink-settings=imagesEnabled=false",
            ]
        )

    lock_fh = None
    if sys.platform != "darwin":
        try:
            lock_fh = open("/tmp/cloud_chromium.lock", "w")
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
        except Exception:
            pass

    with sync_playwright() as p_play:
        try:
            browser = p_play.chromium.launch(headless=True, args=launch_args)
        except Exception:
            browser = p_play.chromium.launch(channel="chrome", headless=True, args=launch_args)

        try:
            for idx, (jid, title, comp, url) in enumerate(jobs, 1):
                print(f"\n[{idx}/{len(jobs)}] Processing: {title} @ {comp}")
                print(f"URL: {url}")

                try:
                    res = fill_ats_application(
                        url,
                        role_title=f"{title} @ {comp}",
                        auto_submit=True,
                        shared_browser=browser,
                    )

                    if res.get("submitted") and res.get("confirmed"):
                        success_count += 1
                        cur.execute(
                            """
                            UPDATE jobs
                            SET status = 'applied',
                                scorecard = json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'ats_autofill', '$.ats_type', ?, '$.applied_at', CURRENT_TIMESTAMP)
                            WHERE id = ?
                            """,
                            (res.get("ats_type", "external_ats"), jid),
                        )
                        conn.commit()
                        print(f"[ATSProcessor] -> SUCCESS: Applied to {title} @ {comp} ({res.get('ats_type')})!")

                        if discord:
                            try:
                                from src.agents.discord_notifier import DiscordNotifier
                                DiscordNotifier().send_application_alert(
                                    {
                                        "company": comp,
                                        "role": title,
                                        "platform": f"External ATS ({res.get('ats_type', 'Company Portal').upper()})",
                                        "url": res.get("ats_url", url),
                                        "status": "SUBMITTED & CONFIRMED",
                                        "details": "Screening: Automated (1 yr exp, 15-day immediate notice, clean profile), uploaded ATS resume.",
                                        "resume": "Vishal_Gupta_Resume.pdf",
                                    }
                                )
                            except Exception as me:
                                print(f"[ATSProcessor] Discord send notice: {me}")
                    elif res.get("error") == "expired":
                        cur.execute("UPDATE jobs SET status = 'expired' WHERE id = ?", (jid,))
                        conn.commit()
                        skipped_count += 1
                    else:
                        skipped_count += 1
                        err_val = str(res.get("error") or "unconfirmed")[:140]
                        cur.execute(
                            """
                            UPDATE jobs
                            SET status = 'external_pending_review',
                                scorecard = json_set(COALESCE(scorecard, '{}'), '$.last_error', ?)
                            WHERE id = ?
                            """,
                            (err_val, jid),
                        )
                        conn.commit()
                        print(f"[ATSProcessor] -> Skipped ({err_val})")
                except Exception as e:
                    skipped_count += 1
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status = 'external_pending_review',
                            scorecard = json_set(COALESCE(scorecard, '{}'), '$.last_error', ?)
                        WHERE id = ?
                        """,
                        (str(e)[:140], jid),
                    )
                    conn.commit()
                    print(f"[ATSProcessor] Error on {title}: {e}")

                time.sleep(0.8)
        finally:
            try:
                browser.close()
            except Exception:
                pass
            if lock_fh is not None:
                try:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)
                    lock_fh.close()
                except Exception:
                    pass

    conn.close()
    print("\n=======================================================")
    print(f"[ATSProcessor] Turbo Batch Complete! Submitted: {success_count}, Skipped/Review: {skipped_count}")
    print("=======================================================\n")
    return success_count


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    process_redirects(max_jobs=count)
