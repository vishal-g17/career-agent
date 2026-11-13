import imaplib
import os
import sys
import time

sys.path.insert(0, '/Users/admin/linkedin-multiagent')
from src import config

def purge_bounces_from_inbox(account_name: str, email_addr: str, app_pwd: str, trash_folder: str):
    print(f"\n{'='*70}")
    print(f"🧹 PURGING BOUNCE NOTIFICATIONS FROM {account_name} ({email_addr})")
    print(f"{'='*70}")

    if not email_addr or not app_pwd:
        print(f"[-] Missing credentials for {account_name}")
        return 0

    clean_pwd = app_pwd.replace(" ", "").strip()
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        mail.login(email_addr, clean_pwd)
        mail.select("INBOX", readonly=False)

        criteria_list = [
            'FROM "mailer-daemon"',
            'FROM "postmaster"',
            'SUBJECT "Delivery Status Notification"',
            'SUBJECT "Undelivered Mail"',
            'SUBJECT "Mail delivery failed"',
            'SUBJECT "failure notice"'
        ]

        all_ids = set()
        for crit in criteria_list:
            res, nums = mail.search(None, f'({crit})')
            if res == "OK" and nums[0]:
                ids = nums[0].split()
                all_ids.update(ids)

        total_found = len(all_ids)
        print(f"[*] Found {total_found} bounce notification emails in INBOX.")

        if total_found == 0:
            print("[+] INBOX is already 100% clean of bounces!")
            mail.close()
            mail.logout()
            return 0

        # Process in batches of 200
        id_list = list(all_ids)
        batch_size = 200
        total_moved = 0

        for i in range(0, len(id_list), batch_size):
            batch = id_list[i:i + batch_size]
            id_str = b",".join(batch).decode("utf-8")

            # Copy to Trash / Bin
            c_res, _ = mail.copy(id_str, trash_folder)
            if c_res == "OK":
                # Mark deleted in INBOX
                mail.store(id_str, "+FLAGS", "\\Deleted")
                total_moved += len(batch)
                print(f"  -> Moved {total_moved}/{total_found} bounce emails to {trash_folder}...")

        mail.expunge()
        mail.close()
        mail.logout()
        print(f"[+] Successfully purged all {total_moved} bounce notifications from {account_name} INBOX!")
        return total_moved

    except Exception as e:
        print(f"[-] Error purging {account_name}: {e}")
        return 0

if __name__ == "__main__":
    t1 = purge_bounces_from_inbox("Gmail", config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD, "[Gmail]/Bin")
    t2 = purge_bounces_from_inbox("NSUT Workspace", config.COLLEGE_EMAIL_ADDRESS, config.COLLEGE_EMAIL_APP_PASSWORD, "[Gmail]/Trash")
    print(f"\n🎉 Total bounce notifications removed from both inboxes: {t1 + t2}")
