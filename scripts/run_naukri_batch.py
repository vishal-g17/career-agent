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

def _discover_fresh_naukri_into_db(conn) -> int:
    """Scrapes fresh (<24h) Naukri jobs across Vishal's target roles and saves high-fit matches to DB."""
    from src.agents.job_hunter import scrape_fresh_naukri_jobs, score_job_heuristic
    from src.services.exclusion_filter import FORBIDDEN_COMPANIES
    import json

    keywords = [
        "associate product manager",
        "product analyst",
        "business analyst",
        "data analyst",
        "category manager",
        "operations analyst",
        "founders office",
    ]
    scraped = scrape_fresh_naukri_jobs(keywords=keywords)
    saved = 0
    cur = conn.cursor()
    for j in scraped:
        comp_l = (j.get("company") or "").lower()
        title_l = (j.get("title") or "").lower()
        if any(fc in f"{comp_l} {title_l}" for fc in FORBIDDEN_COMPANIES):
            continue
        score, reasons = score_job_heuristic(j["title"], j["company"])
        if score < 70:
            continue
        exists = cur.execute(
            "SELECT 1 FROM jobs WHERE id = ? OR url = ?",
            (j["dedup_id"], j["url"]),
        ).fetchone()
        if exists:
            continue
        sc_json = json.dumps({"score": score, "reasons": reasons, "platform": "Naukri"})
        cur.execute(
            """
            INSERT INTO jobs (id, job_id, title, company, location, posted_at, url, match_score, scorecard, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                j["dedup_id"],
                j["job_id"],
                j["title"],
                j["company"],
                j["location"],
                j["posted_time"],
                j["url"],
                score,
                sc_json,
            ),
        )
        saved += 1
    conn.commit()
    print(f"[NaukriBatch] Fresh Naukri discovery scanned {len(scraped)} listings -> saved {saved} high-fit new roles.")
    return saved


def _sync_naukri_updates_to_cloud(conn) -> None:
    """Syncs confirmed Naukri 'applied' jobs and extracted external ATS redirects from Local Mac DB to Cloud VM DB."""
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if sys.platform != "darwin" or not os.path.exists(ssh_key):
        return
    try:
        import base64, json, subprocess
        rows = conn.execute(
            """
            SELECT id, job_id, title, company, COALESCE(location, 'India'), posted_at, url,
                   COALESCE(match_score, 85), COALESCE(scorecard, '{}'), status, created_at
            FROM jobs
            WHERE (status = 'applied' AND (url LIKE '%naukri%' OR COALESCE(scorecard, '') LIKE '%naukri%'))
               OR (status = 'external_redirect' AND url NOT LIKE '%naukri.com%')
            ORDER BY created_at DESC
            LIMIT 80
            """
        ).fetchall()
        if not rows:
            return
        b64 = base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
        remote_py = (
            f"import base64,json,sqlite3; rows=json.loads(base64.b64decode('{b64}').decode()); "
            "c=sqlite3.connect('/home/opc/.linkedin_agent.db',timeout=20.0); "
            "c.executemany('''INSERT INTO jobs (id,job_id,title,company,location,posted_at,url,match_score,scorecard,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, url=excluded.url, scorecard=excluded.scorecard''', rows); "
            "c.commit(); c.close()"
        )
        subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "opc@130.210.108.55", "/home/opc/career-agent/venv/bin/python3 -"],
            input=remote_py, text=True, capture_output=True, timeout=15,
        )
    except Exception as se:
        print(f"[NaukriBatch] Cloud DB sync notice: {se}")


def run_naukri_batch(limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    pending_now = cur.execute('SELECT count(*) FROM jobs WHERE url LIKE "%naukri.com%" AND status = "new"').fetchone()[0]
    if sys.platform == "darwin" and pending_now < 8:
        try:
            _discover_fresh_naukri_into_db(conn)
        except Exception as de:
            print(f"[NaukriBatch] Discovery notice: {de}")

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

            if status in ["applied", "external_applied"]:
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
                            "platform": "Naukri (External ATS Verified)" if status == "external_applied" else "Naukri (Direct Apply)",
                            "url": res.get("external_url", url),
                            "status": "SUBMITTED & CONFIRMED",
                            "details": f"Type: {'External ATS Auto-Apply (Confirmed)' if status == 'external_applied' else ('1-Click Direct Apply' if not res.get('questions_answered') else 'Chatbot Screening Completed')}",
                            "resume": "Vishal_Gupta_Resume.pdf"
                        })
                    except:
                        pass

            elif status == "already_applied":
                cur.execute('UPDATE jobs SET status = "applied" WHERE id = ?', (jid,))
                conn.commit()
                print(f"[NaukriBatch] -> Already applied previously on Naukri (synced DB without duplicate alert).")

            elif status in ("needs_login", "datacenter_blocked"):
                print(f"[NaukriBatch] -> Naukri returned '{status}'; leaving jobs untouched and stopping batch cleanly.")
                if status == "needs_login" or sys.platform == "darwin":
                    from src.services.system_alerter import send_system_alert
                    send_system_alert(
                        platform="Naukri",
                        issue_type="logged_out" if status == "needs_login" else "access_denied",
                        summary=(
                            "Naukri session is logged out ('Login to apply' shown instead of Apply)."
                            if status == "needs_login"
                            else "Naukri returned Access Denied on Mac browser."
                        ),
                        fix_steps="Open Ridealong (`http://127.0.0.1:8083`) -> sign back in at `https://www.naukri.com/nlogin/login`.",
                        details=url,
                    )
                break

            elif status == "external_redirect":
                redirect_count += 1
                ext_url = res.get("external_url")
                if ext_url and ext_url.startswith("http") and "naukri.com" not in ext_url:
                    cur.execute(
                        'UPDATE jobs SET status = "external_redirect", url = ? WHERE id = ?',
                        (ext_url, jid),
                    )
                else:
                    cur.execute('UPDATE jobs SET status = "external_redirect" WHERE id = ?', (jid,))
                conn.commit()
                print(f"[NaukriBatch] -> External company site redirect ({ext_url or url}).")

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
            if sys.platform == "darwin":
                try:
                    from src.services.system_alerter import send_system_alert
                    send_system_alert(
                        platform="Naukri",
                        issue_type="runtime_error",
                        summary=f"Naukri auto-apply encountered a browser/runtime error on `{title} @ {company}`.",
                        fix_steps="Check Ridealong (`http://127.0.0.1:8083`) and Naukri tab health.",
                        details=str(e),
                    )
                except Exception:
                    pass

        # Humanized delay between applications
        time.sleep(5)

    if applied_count > 0:
        try:
            from src.services.system_alerter import clear_system_alert
            clear_system_alert("Naukri")
        except Exception:
            pass
    _sync_naukri_updates_to_cloud(conn)
    conn.close()
    print(f"\n[NaukriBatch] Completed! Applied: {applied_count}, External Redirects: {redirect_count}, Failed/Skipped: {failed_count}")
    return applied_count

if __name__ == "__main__":
    limit = 50
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    run_naukri_batch(limit)
