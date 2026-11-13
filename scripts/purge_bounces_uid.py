import imaplib
import sys
import time

sys.path.insert(0, '/Users/admin/linkedin-multiagent')
from src import config

def purge_all_bounces_uid(account_name: str, email_addr: str, app_pwd: str, trash_folder: str):
    print(f"\n{'='*70}")
    print(f"🧹 UID PURGE: ZERO-TOLERANCE BOUNCE CLEANUP FOR {account_name} ({email_addr})")
    print(f"{'='*70}")

    if not email_addr or not app_pwd:
        print("[-] Missing credentials")
        return 0

    clean_pwd = app_pwd.replace(" ", "").strip()
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    mail.login(email_addr, clean_pwd)
    mail.select("INBOX", readonly=False)

    criteria = [
        'FROM "mailer-daemon"',
        'FROM "postmaster"',
        'SUBJECT "Delivery Status Notification"',
        'SUBJECT "Undelivered Mail"',
        'SUBJECT "Mail delivery failed"'
    ]

    total_purged = 0
    while True:
        # Collect all matching UIDs
        all_uids = set()
        for crit in criteria:
            res, data = mail.uid("search", None, f"({crit})")
            if res == "OK" and data[0]:
                all_uids.update(data[0].split())

        if not all_uids:
            print(f"[+] All bounce notifications completely wiped from {account_name} INBOX (0 remaining)!")
            break

        print(f"[*] Found {len(all_uids)} bounce emails in this sweep...")
        uid_list = list(all_uids)
        batch_size = 250

        for i in range(0, len(uid_list), batch_size):
            batch = uid_list[i:i + batch_size]
            uid_str = b",".join(batch).decode("utf-8")

            # Copy to Bin/Trash
            mail.uid("copy", uid_str, trash_folder)
            # Mark Deleted
            mail.uid("store", uid_str, "+FLAGS", "(\\Deleted)")
            total_purged += len(batch)
            print(f"  -> Trashed {total_purged} bounce emails...")

        mail.expunge()
        time.sleep(1)

    mail.close()
    mail.logout()
    return total_purged

if __name__ == "__main__":
    t1 = purge_all_bounces_uid("Gmail", config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD, "[Gmail]/Bin")
    t2 = purge_all_bounces_uid("NSUT Workspace", config.COLLEGE_EMAIL_ADDRESS, config.COLLEGE_EMAIL_APP_PASSWORD, "[Gmail]/Trash")
    print(f"\n🎉 DONE: Total bounce emails wiped: {t1 + t2}")
