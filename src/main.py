import time
import threading
from datetime import datetime
from .agents.discord_listener import DiscordListener
from .agents.content_writer import draft_post
from .agents.discord_notifier import DiscordNotifier
from .agents.job_hunter import run_job_hunt
from .agents.feed_scanner import scan_personal_feed_for_jobs
from .services.reply_monitor import check_gmail_replies
from .services.outreach_campaign import process_pending_followups
from .db.database import init_db, log_event
from . import config

def run_scheduled_post():
    print(f"[{datetime.now()}] Triggering scheduled daily post generation...")
    try:
        post = draft_post("High-impact operational product metrics and unsexy automation")
        notifier = DiscordNotifier()
        notifier.send_post_review(post)
        log_event("SCHEDULER", "INFO", "Daily post draft sent to Discord", {"post_id": post["post_id"]})
    except Exception as e:
        print(f"[Scheduler] Post generation error: {e}")
        log_event("SCHEDULER", "ERROR", f"Post draft failed: {e}")

def run_scheduled_jobs():
    print(f"[{datetime.now()}] Triggering scheduled job hunt across LinkedIn Jobs & Personal Feed...")
    notifier = DiscordNotifier()
    
    # 1. Scrape formal LinkedIn Jobs (<24h)
    try:
        results = run_job_hunt()
        hot = results.get("hot_matches", [])
        if hot:
            for j in hot[:3]:
                notifier.send_job_alert(j)
                time.sleep(1)
        log_event("SCHEDULER", "INFO", "Formal job hunt completed", {"hot_matches": len(hot)})
    except Exception as e:
        print(f"[Scheduler] Formal job hunt error: {e}")
        log_event("SCHEDULER", "ERROR", f"Formal job hunt failed: {e}")

    # 2. Scan personal LinkedIn feed for informal hiring posts
    try:
        leads = scan_personal_feed_for_jobs()
        for lead in leads:
            notifier.send_feed_lead_alert(lead)
            time.sleep(1)
        log_event("SCHEDULER", "INFO", "Feed scan completed", {"leads_found": len(leads)})
    except Exception as e:
        print(f"[Scheduler] Feed scan error: {e}")
        log_event("SCHEDULER", "ERROR", f"Feed scan failed: {e}")

def scheduler_loop():
    """
    Lightweight, dependency-free 24x7 scheduler loop.
    Checks wall-clock time every 30 seconds.
    """
    last_post_date = ""
    last_job_hour = -1
    last_fu_date = ""
    last_reply_check_min = -1

    while True:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 7:45 AM: Morning post generation
        if now.hour == 7 and now.minute >= 45 and last_post_date != today_str:
            last_post_date = today_str
            run_scheduled_post()

        # 9:00 AM & 6:00 PM (18:00): Job hunt
        if now.hour in (9, 18) and now.hour != last_job_hour and now.minute <= 5:
            last_job_hour = now.hour
            run_scheduled_jobs()

        # 11:00 AM: Process scheduled cold email follow-ups (Follow-up 1 & 2)
        if now.hour == 11 and now.minute <= 5 and last_fu_date != today_str:
            last_fu_date = today_str
            print(f"[{datetime.now()}] Checking & sending pending cold outreach follow-ups...")
            try:
                fu_res = process_pending_followups()
                log_event("OUTREACH", "INFO", "Follow-ups processed", fu_res)
            except Exception as e:
                print(f"[Scheduler] Follow-up check error: {e}")

        # Every 15 minutes: Check Gmail IMAP for recruiter replies
        if now.minute % 15 == 0 and now.minute != last_reply_check_min:
            last_reply_check_min = now.minute
            try:
                check_gmail_replies()
            except Exception as e:
                print(f"[Scheduler] Reply check error: {e}")

        time.sleep(30)

def main():
    print("=" * 60)
    print("🚀 LinkedIn 24/7 Autonomous Multi-Agent System Starting...")
    print("=" * 60)
    init_db()

    # 1. Start Discord Interactive Command Listener in background thread
    listener = DiscordListener()
    listener_thread = threading.Thread(target=listener.start_polling, daemon=True)
    listener_thread.start()
    print("✅ Discord interactive command listener started.")

    # 2. Run scheduler in main thread
    print("✅ Scheduler loop started (Morning Post: 7:45 AM, Job Scans: 9:00 AM & 6:00 PM).")
    try:
        scheduler_loop()
    except KeyboardInterrupt:
        print("\n[Main] Agent system shutting down gracefully.")

if __name__ == "__main__":
    main()
