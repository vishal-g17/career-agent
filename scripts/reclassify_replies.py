import os
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.reply_monitor import _extract_clean_reply_body, classify_inbound_reply
from src.db.database import get_connection

def reclassify_all_replies(db_path: Path = None) -> dict:
    conn = sqlite3.connect(str(db_path)) if db_path else get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    rows = cursor.execute(
        "SELECT id, name, company, email, status, reply_snippet, last_subject, sender_email FROM outreach_leads WHERE status = 'replied' OR reply_snippet IS NOT NULL"
    ).fetchall()

    counts = {"auto_replied": 0, "closed_no_opening": 0, "exited_redirected": 0, "replied": 0, "unchanged": 0}
    reclassified = []

    for r in rows:
        lead_id = r["id"]
        raw_snippet = r["reply_snippet"] or ""
        subj = r["last_subject"] or ""
        email_addr = (r["email"] or "").lower()

        clean_text = _extract_clean_reply_body(raw_snippet)
        new_kind = classify_inbound_reply(subj, clean_text, auto_submitted_hdr="", from_addr=email_addr)

        if new_kind == "auto_reply":
            new_status = "auto_replied"
            clean_snippet = f"[AUTO] {clean_text[:380]}"
        elif new_kind == "closed_no_opening":
            new_status = "closed_no_opening"
            clean_snippet = clean_text[:400]
        elif new_kind == "exited_redirected":
            new_status = "exited_redirected"
            clean_snippet = clean_text[:400]
        else:
            new_status = "replied"
            clean_snippet = clean_text[:400]

        cur_status = r["status"]
        if new_status != cur_status or (raw_snippet.startswith("--") and clean_snippet != raw_snippet):
            cursor.execute(
                "UPDATE outreach_leads SET status = ?, reply_snippet = ? WHERE id = ?",
                (new_status, clean_snippet, lead_id),
            )
            counts[new_status] = counts.get(new_status, 0) + 1
            reclassified.append({
                "id": lead_id,
                "name": r["name"],
                "company": r["company"],
                "email": r["email"],
                "old_status": cur_status,
                "new_status": new_status,
                "snippet": clean_snippet[:80],
            })
        else:
            counts["unchanged"] += 1

    conn.commit()
    conn.close()

    return {"total_evaluated": len(rows), "counts": counts, "reclassified": reclassified}

if __name__ == "__main__":
    db_file = Path(os.environ.get("DATABASE_PATH", str(Path.home() / ".linkedin_agent.db")))
    print(f"[Reclassify] Scanning database at: {db_file}")
    res = reclassify_all_replies(db_file)
    print(f"[Reclassify] Evaluated: {res['total_evaluated']}")
    print(f"[Reclassify] Status counts: {res['counts']}")
    for item in res["reclassified"]:
        print(f"  • ID {item['id']} {item['name']} ({item['company']}): {item['old_status']} -> {item['new_status']} | {item['snippet']}")
