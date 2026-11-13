import sys
from pathlib import Path
import sqlite3
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.easy_apply import apply_to_easy_apply_job
from src.services.ats_filler import fill_ats_application
from src.engine.discord_client import DiscordClient
from src.db import database
from src import config

def main():
    client = DiscordClient()
    target_channel = config.DISCORD_JOBS_CHANNEL_ID
    
    conn = database.get_connection()
    c = conn.cursor()
    # Find top hot matches (score >= 75) on LinkedIn
    c.execute(
        """
        SELECT id, title, company, url, match_score 
        FROM jobs 
        WHERE status = 'new' AND match_score >= 75 AND url LIKE '%linkedin.com%'
        ORDER BY match_score DESC 
        LIMIT 5
        """
    )
    jobs = c.fetchall()
    
    if not jobs:
        print("[AutoApply] No pending hot-match LinkedIn jobs found to apply.")
        client.send_message(target_channel, "ℹ️ No pending hot-match LinkedIn jobs waiting in queue right now.")
        return

    client.send_message(
        target_channel,
        f"🤖 **Starting Autonomous Easy Apply Batch ({len(jobs)} Roles)**\n"
        f"• Auto-filling profile & attaching CV (`Vishal_s_CV.pdf`)\n"
        f"• Answering screening questions via Deterministic & LLM Engine\n"
        f"• Pacing with 30–45s human delays..."
    )

    applied_count = 0
    external_count = 0

    for i, j in enumerate(jobs):
        job_id, title, comp, url, score = j["id"], j["title"], j["company"], j["url"], j["match_score"]
        print(f"\n[AutoApply] Processing ({i+1}/{len(jobs)}): {title} @ {comp} ({url})")
        
        res = apply_to_easy_apply_job(url, role_title=title, auto_submit=True)
        
        with database.get_connection() as conn_update:
            if res.get("is_easy_apply") and res.get("submitted") and res.get("confirmed"):
                conn_update.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (job_id,))
                applied_count += 1
                status_note = "✅ Submitted & Confirmed via LinkedIn Easy Apply"
            elif res.get("is_easy_apply"):
                conn_update.execute("UPDATE jobs SET status = 'reviewed' WHERE id = ?", (job_id,))
                status_note = f"⚠️ Reviewed / Unconfirmed ({res.get('error') or 'requires custom manual review'})"
            else:
                # External redirect: Automatically fill application on company career site!
                print(f"[AutoApply] External role detected for {title} @ {comp}. Launching ATS autofill...")
                ats_res = fill_ats_application(url, role_title=title, auto_submit=True)
                if ats_res.get("submitted") and ats_res.get("confirmed"):
                    conn_update.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (job_id,))
                    applied_count += 1
                    status_note = f"✅ Submitted & Confirmed via External ATS ({ats_res.get('ats_type', 'Company Portal').upper()})"
                else:
                    conn_update.execute("UPDATE jobs SET status = 'external_redirect' WHERE id = ?", (job_id,))
                    external_count += 1
                    status_note = f"🔗 External Career Site ({ats_res.get('ats_type', 'ATS')}) - Stored in queue (not auto-confirmed)"

        # Notify Discord of individual application
        client.send_message(
            target_channel,
            f"📄 **Job Application Progress ({i+1}/{len(jobs)}):**\n"
            f"• Role: **{title}** @ **{comp}** ({score}/100 Match)\n"
            f"• Status: {status_note}\n"
            f"• URL: {url}"
        )

        if i < len(jobs) - 1:
            time.sleep(30)

    summary = (
        f"🎯 **Auto-Apply Batch Finished!**\n"
        f"• Total Processed: **{len(jobs)}**\n"
        f"• Easy Apply Submitted: **{applied_count}**\n"
        f"• External Redirects (Saved for ATS filler): **{external_count}**"
    )
    client.send_message(target_channel, summary)
    print(f"[AutoApply] Finished batch. Applied: {applied_count}, External: {external_count}")

if __name__ == "__main__":
    main()
