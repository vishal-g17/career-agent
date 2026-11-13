import csv
import json
import os
import sqlite3
from pathlib import Path
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

DB_PATH = Path(os.environ.get("DATABASE_PATH", str(Path.home() / ".linkedin_agent.db")))
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_XLSX = OUTPUT_DIR / "MASTER_LEADS_SHEET.xlsx"
OUTPUT_WORKING_XLSX = OUTPUT_DIR / "WORKING_LEADS_MASTER.xlsx"
OUTPUT_WORKING_CSV = OUTPUT_DIR / "WORKING_LEADS_MASTER.csv"
OUTPUT_ACTIVE_CSV = OUTPUT_DIR / "ACTIVE_DELIVERED_LEADS.csv"
OUTPUT_CLEAN_JSON = OUTPUT_DIR / "cleaned_recruiter_leads.json"
LEGACY_DISCARDED_CSV = OUTPUT_DIR / "DISCARDED_BOUNCED_LEADS.csv"


def export_split_leads():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Ensure internal blocklist exists and purge ANY bounced/excluded rows from active outreach_leads
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bounced_blocklist (
            email TEXT PRIMARY KEY,
            reason TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    bad_rows = cur.execute(
        "SELECT email, status FROM outreach_leads WHERE status IN ('bounced', 'excluded') AND email IS NOT NULL"
    ).fetchall()
    for br in bad_rows:
        cur.execute(
            "INSERT OR REPLACE INTO bounced_blocklist (email, reason, detected_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (br["email"].strip().lower(), f"Purged {br['status']}"),
        )
    cur.execute("DELETE FROM outreach_leads WHERE status IN ('bounced', 'excluded')")
    cur.execute(
        "DELETE FROM outreach_leads WHERE LOWER(TRIM(email)) IN (SELECT LOWER(TRIM(email)) FROM bounced_blocklist)"
    )
    conn.commit()

    # Fetch 100% clean Active & Delivered Leads ONLY
    active_rows = [
        dict(r)
        for r in cur.execute(
            """
            SELECT *
            FROM outreach_leads
            WHERE status IN ('sent', 'replied', 'pending', 'unresponsive')
            ORDER BY CASE status WHEN 'replied' THEN 1 WHEN 'sent' THEN 2 WHEN 'pending' THEN 3 ELSE 4 END,
                     COALESCE(last_sent_at, first_sent_at, '') DESC, id DESC
            """
        ).fetchall()
    ]
    blocklist_count = cur.execute("SELECT COUNT(*) FROM bounced_blocklist").fetchone()[0]
    conn.close()

    # Remove legacy discarded CSV so no bounced leads file remains in data/
    if LEGACY_DISCARDED_CSV.exists():
        try:
            LEGACY_DISCARDED_CSV.unlink()
        except OSError:
            pass

    print(f"[SheetSync] Exporting {len(active_rows)} clean Active Leads (0 bounced in data/sheets; {blocklist_count} permanently blocked internally)...")

    # 1. MASTER_LEADS_SHEET.xlsx (Styled, 100% clean active leads only)
    wb = openpyxl.Workbook()
    ws_active = wb.active
    ws_active.title = "Active & Delivered Leads"

    border_side = Side(style="thin", color="DDDDDD")
    border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)
    data_font = Font(name="Calibri", size=10)
    header_fill_active = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font_active = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    headers_active = [
        "ID",
        "Full Name",
        "Corporate Email",
        "Company",
        "Target Role",
        "Status",
        "Sent Timestamp",
        "Sender Account",
        "Verification",
    ]
    ws_active.append(headers_active)

    for col_idx in range(1, len(headers_active) + 1):
        cell = ws_active.cell(row=1, column=col_idx)
        cell.fill = header_fill_active
        cell.font = header_font_active
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r_idx, r in enumerate(active_rows, start=2):
        status = (r.get("status") or "pending").lower()
        sent_at = r.get("last_sent_at") or r.get("first_sent_at")
        row_data = [
            r.get("id"),
            r.get("name") or "Hiring Team",
            r.get("email") or "",
            r.get("company") or "Tech Company",
            r.get("role_target") or "Associate Product Manager / Product & Business Analyst",
            status.upper(),
            str(sent_at)[:19] if sent_at else "Queued",
            r.get("sender_email") or "Dual SMTP Queue",
            "Delivered (Verified MX)" if status in ("sent", "replied", "unresponsive") else "Pending Dispatch",
        ]
        ws_active.append(row_data)

        for c_idx in range(1, len(row_data) + 1):
            c = ws_active.cell(row=r_idx, column=c_idx)
            c.font = data_font
            c.border = border
            if c_idx == 6:
                if status == "replied":
                    c.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                    c.font = Font(name="Calibri", size=10, color="006100", bold=True)
                elif status in ("sent", "unresponsive"):
                    c.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
                    c.font = Font(name="Calibri", size=10, color="276A3C", bold=True)
                elif status == "pending":
                    c.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
                    c.font = Font(name="Calibri", size=10, color="8A6D3B", bold=True)

    col_widths_active = [8, 26, 34, 24, 34, 16, 22, 28, 24]
    for idx, width in enumerate(col_widths_active, start=1):
        ws_active.column_dimensions[get_column_letter(idx)].width = width

    pid = os.getpid()
    tmp_xlsx = OUTPUT_DIR / f".MASTER_LEADS_SHEET.{pid}.tmp.xlsx"
    wb.save(tmp_xlsx)
    os.replace(tmp_xlsx, OUTPUT_XLSX)

    # 2. ACTIVE_DELIVERED_LEADS.csv
    tmp_active_csv = OUTPUT_DIR / f".ACTIVE_DELIVERED_LEADS.{pid}.tmp.csv"
    with open(tmp_active_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers_active)
        for r in active_rows:
            st = (r.get("status") or "pending").lower()
            sent_at = r.get("last_sent_at") or r.get("first_sent_at") or ""
            w.writerow([
                r.get("id"),
                r.get("name") or "Hiring Team",
                r.get("email") or "",
                r.get("company") or "",
                r.get("role_target") or "",
                st.upper(),
                sent_at,
                r.get("sender_email") or "",
                "Delivered" if st in ("sent", "replied", "unresponsive") else "Pending",
            ])
    os.replace(tmp_active_csv, OUTPUT_ACTIVE_CSV)

    # 3. WORKING_LEADS_MASTER.csv & WORKING_LEADS_MASTER.xlsx
    working_headers = [
        "Name",
        "Email",
        "Company",
        "Role Target",
        "Status",
        "Sent At",
        "Sender Email",
        "Reply Snippet",
        "Reply Date",
    ]
    tmp_w_csv = OUTPUT_DIR / f".WORKING_LEADS_MASTER.{pid}.tmp.csv"
    with open(tmp_w_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(working_headers)
        for r in active_rows:
            w.writerow([
                r.get("name") or "",
                r.get("email") or "",
                r.get("company") or "",
                r.get("role_target") or "",
                r.get("status") or "",
                r.get("last_sent_at") or r.get("first_sent_at") or "",
                r.get("sender_email") or "",
                r.get("reply_snippet") or "",
                r.get("reply_received_at") or "",
            ])
    os.replace(tmp_w_csv, OUTPUT_WORKING_CSV)

    wb_work = openpyxl.Workbook()
    ws_work = wb_work.active
    ws_work.title = "Working Leads Master"
    ws_work.append(working_headers)
    for r in active_rows:
        ws_work.append([
            r.get("name") or "",
            r.get("email") or "",
            r.get("company") or "",
            r.get("role_target") or "",
            r.get("status") or "",
            r.get("last_sent_at") or r.get("first_sent_at") or "",
            r.get("sender_email") or "",
            r.get("reply_snippet") or "",
            r.get("reply_received_at") or "",
        ])
    tmp_w_xlsx = OUTPUT_DIR / f".WORKING_LEADS_MASTER.{pid}.tmp.xlsx"
    wb_work.save(tmp_w_xlsx)
    os.replace(tmp_w_xlsx, OUTPUT_WORKING_XLSX)

    # 4. cleaned_recruiter_leads.json
    json_leads = [
        {
            "name": r.get("name") or "Hiring Team",
            "email": r.get("email") or "",
            "company": r.get("company") or "",
            "role_target": r.get("role_target") or "Associate Product Manager / Product & Business Analyst",
            "status": r.get("status") or "pending",
        }
        for r in active_rows
    ]
    tmp_json = OUTPUT_DIR / f".cleaned_recruiter_leads.{pid}.tmp.json"
    with open(tmp_json, "w", encoding="utf-8") as jf:
        json.dump(json_leads, jf, indent=2, ensure_ascii=False)
    os.replace(tmp_json, OUTPUT_CLEAN_JSON)

    print(f"[SheetSync] Saved clean MASTER_LEADS_SHEET.xlsx, WORKING_LEADS_MASTER.xlsx/.csv, ACTIVE_DELIVERED_LEADS.csv, and cleaned_recruiter_leads.json.")
    return {
        "active_count": len(active_rows),
        "blocked_count": blocklist_count,
        "xlsx_path": str(OUTPUT_XLSX),
        "active_csv_path": str(OUTPUT_ACTIVE_CSV),
    }


if __name__ == "__main__":
    export_split_leads()
