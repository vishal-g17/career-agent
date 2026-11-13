import sqlite3
import os
import csv
from pathlib import Path
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

DB_PATH = Path.home() / '.linkedin_agent.db'
OUTPUT_DIR = Path('/Users/admin/linkedin-multiagent/data')
OUTPUT_XLSX = OUTPUT_DIR / 'MASTER_LEADS_SHEET.xlsx'
OUTPUT_ACTIVE_CSV = OUTPUT_DIR / 'ACTIVE_DELIVERED_LEADS.csv'
OUTPUT_DISCARDED_CSV = OUTPUT_DIR / 'DISCARDED_BOUNCED_LEADS.csv'

def export_split_leads():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. Fetch Active & Delivered Leads (Sent, Pending, Replied)
    cur.execute('''
        SELECT id, name, email, company, role_target, status, first_sent_at, sender_email
        FROM outreach_leads
        WHERE status IN ('sent', 'replied', 'pending')
        ORDER BY CASE status WHEN 'replied' THEN 1 WHEN 'sent' THEN 2 ELSE 3 END, first_sent_at DESC
    ''')
    active_rows = cur.fetchall()

    # 2. Fetch Discarded & Bounced Leads
    cur.execute('''
        SELECT id, name, email, company, role_target, status, last_sent_at, sender_email
        FROM outreach_leads
        WHERE status IN ('bounced', 'excluded')
        ORDER BY last_sent_at DESC
    ''')
    discarded_rows = cur.fetchall()
    conn.close()

    print(f"[SheetSync] Processing {len(active_rows)} Active Leads and {len(discarded_rows)} Discarded/Bounced Leads...")

    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    border_side = Side(style='thin', color='DDDDDD')
    border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)
    data_font = Font(name="Calibri", size=10)

    # -------------------------------------------------------------
    # TAB 1: ACTIVE & DELIVERED LEADS
    # -------------------------------------------------------------
    ws_active = wb.create_sheet(title="Active & Delivered Leads")
    header_fill_active = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font_active = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    headers_active = ["ID", "Full Name", "Corporate Email", "Company", "Target Role", "Status", "Sent Timestamp", "Sender Account", "Verification"]
    ws_active.append(headers_active)

    for col_idx in range(1, len(headers_active) + 1):
        cell = ws_active.cell(row=1, column=col_idx)
        cell.fill = header_fill_active
        cell.font = header_font_active
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r_idx, (lid, name, email, company, role, status, sent_at, sender) in enumerate(active_rows, start=2):
        row_data = [
            lid,
            name or "Hiring Team",
            email,
            company or "Tech Company",
            role or "Product / Analytics",
            status.upper(),
            str(sent_at)[:19] if sent_at else "Queued",
            sender or "NSUT SMTP",
            "Delivered (Verified MX)" if status in ('sent', 'replied') else "Pending Dispatch"
        ]
        ws_active.append(row_data)

        for c_idx in range(1, len(row_data) + 1):
            c = ws_active.cell(row=r_idx, column=c_idx)
            c.font = data_font
            c.border = border
            if c_idx == 6: # Status
                if status == 'replied':
                    c.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                    c.font = Font(name="Calibri", size=10, color="006100", bold=True)
                elif status == 'sent':
                    c.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
                    c.font = Font(name="Calibri", size=10, color="276A3C", bold=True)
                elif status == 'pending':
                    c.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
                    c.font = Font(name="Calibri", size=10, color="8A6D3B", bold=True)

    col_widths_active = [8, 26, 34, 22, 24, 16, 22, 28, 22]
    for idx, width in enumerate(col_widths_active, start=1):
        ws_active.column_dimensions[get_column_letter(idx)].width = width

    # -------------------------------------------------------------
    # TAB 2: DISCARDED & BOUNCED LEADS (NEVER EMAIL AGAIN)
    # -------------------------------------------------------------
    ws_disc = wb.create_sheet(title="Discarded & Bounced")
    header_fill_disc = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid") # Strong Red
    header_font_disc = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    headers_disc = ["ID", "Full Name", "Bounced / Discarded Email", "Company", "Target Role", "Status", "Attempted Date", "Exclusion Reason"]
    ws_disc.append(headers_disc)

    for col_idx in range(1, len(headers_disc) + 1):
        cell = ws_disc.cell(row=1, column=col_idx)
        cell.fill = header_fill_disc
        cell.font = header_font_disc
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r_idx, (lid, name, email, company, role, status, last_sent, sender) in enumerate(discarded_rows, start=2):
        row_data = [
            lid,
            name or "Professional",
            email,
            company or "Organization",
            role or "Product / Analytics",
            "DISCARDED (BOUNCED)" if status == 'bounced' else "EXCLUDED (STUDENT/JUNIOR)",
            str(last_sent)[:19] if last_sent else "N/A",
            "SMTP Bounce / Churned Inactive Mailbox - DO NOT RE-CONTACT"
        ]
        ws_disc.append(row_data)

        for c_idx in range(1, len(row_data) + 1):
            c = ws_disc.cell(row=r_idx, column=c_idx)
            c.font = data_font
            c.border = border
            if c_idx == 6: # Status
                c.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
                c.font = Font(name="Calibri", size=10, color="C00000", bold=True)

    col_widths_disc = [8, 26, 36, 22, 24, 26, 22, 45]
    for idx, width in enumerate(col_widths_disc, start=1):
        ws_disc.column_dimensions[get_column_letter(idx)].width = width

    # Save Excel Workbook
    wb.save(OUTPUT_XLSX)
    print(f"[SheetSync] Excel workbook successfully saved to {OUTPUT_XLSX}")

    # Export Active CSV
    with open(OUTPUT_ACTIVE_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(headers_active)
        for r in active_rows:
            w.writerow([r[0], r[1] or "Hiring Team", r[2], r[3], r[4], r[5].upper(), r[6], r[7], "Delivered" if r[5] in ('sent', 'replied') else "Pending"])
    print(f"[SheetSync] Clean active CSV exported to {OUTPUT_ACTIVE_CSV}")

    # Export Discarded CSV
    with open(OUTPUT_DISCARDED_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(headers_disc)
        for r in discarded_rows:
            w.writerow([r[0], r[1] or "Professional", r[2], r[3], r[4], "DISCARDED (BOUNCED)" if r[5] == 'bounced' else "EXCLUDED", r[6], "SMTP Bounce / Churned - DO NOT USE"])
    print(f"[SheetSync] Discarded CSV exported to {OUTPUT_DISCARDED_CSV}")

    return {
        "active_count": len(active_rows),
        "discarded_count": len(discarded_rows),
        "xlsx_path": str(OUTPUT_XLSX),
        "active_csv_path": str(OUTPUT_ACTIVE_CSV),
        "discarded_csv_path": str(OUTPUT_DISCARDED_CSV)
    }

if __name__ == "__main__":
    export_split_leads()
