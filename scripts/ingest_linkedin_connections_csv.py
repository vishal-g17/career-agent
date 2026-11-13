import os
import sys
import csv
import glob
import zipfile
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.exclusion_filter import is_excluded
from src.services.email_verifier import verify_email
from scripts.sync_leads_to_sheet import export_all_leads_to_sheet

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')
DOWNLOADS_DIR = os.path.expanduser('~/Downloads')

def find_connections_file():
    """Finds Connections.csv or extracts it from a LinkedIn export zip in Downloads."""
    # Check directly for Connections.csv
    csv_candidates = glob.glob(os.path.join(DOWNLOADS_DIR, "*Connections*.csv"))
    if csv_candidates:
        return sorted(csv_candidates, key=os.path.getmtime, reverse=True)[0]

    # Check for zip archives
    zip_candidates = glob.glob(os.path.join(DOWNLOADS_DIR, "*Basic_Member_Data*.zip")) + \
                     glob.glob(os.path.join(DOWNLOADS_DIR, "*Complete_LinkedInDataExport*.zip")) + \
                     glob.glob(os.path.join(DOWNLOADS_DIR, "*LinkedIn*.zip"))
    
    for zf_path in sorted(zip_candidates, key=os.path.getmtime, reverse=True):
        try:
            with zipfile.ZipFile(zf_path, 'r') as z:
                for filename in z.namelist():
                    if 'connections.csv' in filename.lower():
                        extract_path = os.path.join(DOWNLOADS_DIR, "Connections.csv")
                        with z.open(filename) as source, open(extract_path, "wb") as target:
                            target.write(source.read())
                        print(f"[ArchiveIngest] Extracted Connections.csv from {os.path.basename(zf_path)}")
                        return extract_path
        except Exception as e:
            print(f"[ArchiveIngest] Error reading zip {zf_path}: {e}")

    return None

def ingest_archive_connections(csv_path: str = None):
    if not csv_path:
        csv_path = find_connections_file()

    if not csv_path or not os.path.exists(csv_path):
        print("[ArchiveIngest] No Connections.csv found in ~/Downloads. Please download it from:")
        print("👉 https://www.linkedin.com/mypreferences/d/download-my-data")
        return 0, 0

    print(f"\n[ArchiveIngest] Processing official LinkedIn archive: {csv_path}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS linkedin_connections_queue (
            id TEXT PRIMARY KEY,
            name TEXT,
            headline TEXT,
            exclusion_reason TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Find the header row (LinkedIn often includes 3 disclaimer lines)
    with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    header_idx = -1
    for idx, line in enumerate(lines[:10]):
        if 'First Name' in line and 'Last Name' in line:
            header_idx = idx
            break

    if header_idx == -1:
        print("[ArchiveIngest] Could not find valid header row in CSV.")
        return 0, 0

    reader = csv.DictReader(lines[header_idx:])
    
    total_rows = 0
    exact_emails_added = 0
    connections_queued = 0
    excluded_count = 0

    for row in reader:
        total_rows += 1
        first_name = (row.get("First Name") or "").strip()
        last_name = (row.get("Last Name") or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        email_addr = (row.get("Email Address") or "").strip().lower()
        company = (row.get("Company") or "").strip()
        position = (row.get("Position") or "").strip()
        profile_url = (row.get("URL") or "").strip()

        # Extract LinkedIn username from URL
        username = ""
        if "/in/" in profile_url:
            username = profile_url.split("/in/")[1].split("/")[0].split("?")[0].strip()
        elif full_name:
            username = full_name.lower().replace(" ", "-")

        headline = f"{position} at {company}" if position and company else (position or company)

        # Check strict user exclusions (E1, E2, E3)
        exclusion = is_excluded(name=full_name, headline=headline)
        if exclusion:
            excluded_count += 1
            continue

        # Case 1: Exact Email present in LinkedIn export (Zero Guessing!)
        if email_addr and "@" in email_addr and "." in email_addr:
            is_valid, reason, _ = verify_email(email_addr)
            if is_valid:
                cur.execute("SELECT id FROM outreach_leads WHERE lower(email) = ?", (email_addr,))
                if not cur.fetchone():
                    cur.execute('''
                        INSERT INTO outreach_leads (name, email, company, role_target, status, created_at)
                        VALUES (?, ?, ?, 'APM / Product Analyst', 'pending', CURRENT_TIMESTAMP)
                    ''', (full_name, email_addr, company or "Tech"))
                    exact_emails_added += 1

        # Case 2: Store connection for safe, throttled future LinkedIn messaging
        if username:
            cur.execute("SELECT id FROM linkedin_connections_queue WHERE id = ?", (username,))
            if not cur.fetchone():
                cur.execute('''
                    INSERT OR IGNORE INTO linkedin_connections_queue (id, name, headline, exclusion_reason, status)
                    VALUES (?, ?, ?, '', 'pending')
                ''', (username, full_name, headline))
                connections_queued += 1

    conn.commit()
    conn.close()

    print(f"\n[ArchiveIngest] === Ingestion Results ===")
    print(f"• Total Connections in Archive: {total_rows}")
    print(f"• Exact Registered Emails Added: {exact_emails_added} (100% authentic, zero guessing)")
    print(f"• Connections Stored in Queue: {connections_queued}")
    print(f"• Excluded (Juniors/Forbidden Companies): {excluded_count}")

    if exact_emails_added > 0:
        export_all_leads_to_sheet()

    return exact_emails_added, connections_queued

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else None
    ingest_archive_connections(csv_file)
