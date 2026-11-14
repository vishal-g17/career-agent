#!/usr/bin/env python3
"""
Lightweight 24/7 Career Autopilot Dashboard & Live Master Sheet Server (Cloud VM).
Serves:
  - / and /p/dashboard                  -> Live Career Autopilot Dashboard
  - /sheet (and /leads, /p/sheet)       -> Live Searchable Master Leads Sheet (0 bounces)
  - /download/MASTER_LEADS_SHEET.xlsx   -> Direct 1-click clean Excel download
  - /download/WORKING_LEADS_MASTER.xlsx -> Direct 1-click working Excel download
  - /download/ACTIVE_DELIVERED_LEADS.csv-> Direct 1-click clean CSV download
"""

import hashlib
import html
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.sync_leads_to_sheet import (
    DB_PATH,
    OUTPUT_ACTIVE_CSV,
    OUTPUT_WORKING_XLSX,
    OUTPUT_XLSX,
    export_split_leads,
)
from src.agents.dashboard_builder import build_dashboard_html

PORT = int(os.environ.get("FOLIO_PORT", "8082"))
ADDR = os.environ.get("FOLIO_ADDR", "127.0.0.1")

_CACHE = {"dash_html": b"", "dash_ts": 0.0, "sheet_html": b"", "sheet_ts": 0.0, "xlsx_ts": 0.0}
_EXPORT_LOCK = threading.Lock()
CACHE_TTL_SECONDS = 20.0


def _get_bridge_key() -> str:
    tok = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not tok and (PROJECT_ROOT / ".env").exists():
        for ln in (PROJECT_ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("DISCORD_BOT_TOKEN="):
                tok = ln.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return hashlib.sha256((tok or "career-agent-priority").encode("utf-8")).hexdigest()[:24]


def _ensure_priority_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS priority_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            channel_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            result_msg TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """
    )
    conn.commit()


def get_cached_dashboard_html() -> bytes:
    now = time.time()
    if not _CACHE["dash_html"] or (now - _CACHE["dash_ts"]) > CACHE_TTL_SECONDS:
        html_str = build_dashboard_html()
        _CACHE["dash_html"] = html_str.encode("utf-8")
        _CACHE["dash_ts"] = now
    return _CACHE["dash_html"]


def ensure_fresh_exports(max_age: float = 120.0) -> None:
    """Serves existing Cloud VM sheet files immediately (0ms wait) and refreshes asynchronously in background if stale."""
    if not OUTPUT_XLSX.exists() or not OUTPUT_ACTIVE_CSV.exists():
        with _EXPORT_LOCK:
            if not OUTPUT_XLSX.exists() or not OUTPUT_ACTIVE_CSV.exists():
                export_split_leads()
                _CACHE["xlsx_ts"] = time.time()
        return

    age = time.time() - OUTPUT_XLSX.stat().st_mtime
    if age > max_age and not _EXPORT_LOCK.locked():
        def _bg():
            with _EXPORT_LOCK:
                try:
                    export_split_leads()
                    _CACHE["xlsx_ts"] = time.time()
                except Exception:
                    pass
        threading.Thread(target=_bg, daemon=True).start()


def build_live_sheet_html() -> str:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bounced_blocklist (
            email TEXT PRIMARY KEY,
            reason TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    rows = conn.execute(
        """
        SELECT id, name, email, company, role_target, status,
               COALESCE(last_sent_at, first_sent_at, '') AS sent_ts,
               COALESCE(sender_email, '') AS sender_email,
               COALESCE(reply_snippet, '') AS reply_snippet
        FROM outreach_leads
        WHERE status IN ('replied', 'sent', 'pending', 'unresponsive')
        ORDER BY CASE status WHEN 'replied' THEN 1 WHEN 'sent' THEN 2 WHEN 'pending' THEN 3 ELSE 4 END,
                 sent_ts DESC, id DESC
        """
    ).fetchall()
    blocked_cnt = conn.execute("SELECT COUNT(*) FROM bounced_blocklist").fetchone()[0]
    conn.close()

    total_clean = len(rows)
    sent_cnt = sum(1 for r in rows if r["status"] in ("sent", "unresponsive"))
    pend_cnt = sum(1 for r in rows if r["status"] == "pending")
    rep_cnt = sum(1 for r in rows if r["status"] == "replied")

    tr_parts = []
    for idx, r in enumerate(rows, 1):
        st = (r["status"] or "pending").lower()
        badge_cls = "st-replied" if st == "replied" else ("st-sent" if st in ("sent", "unresponsive") else "st-pending")
        name_e = html.escape(r["name"] or "Hiring Team")
        email_e = html.escape(r["email"] or "")
        comp_e = html.escape(r["company"] or "")
        role_e = html.escape(r["role_target"] or "APM / Product & Business Analyst")
        sent_e = html.escape((r["sent_ts"] or "Queued")[:19])
        sender_e = html.escape(r["sender_email"] or "Dual Queue")
        tr_parts.append(
            f"<tr data-status='{st}'>"
            f"<td>{idx}</td>"
            f"<td class='fw'>{name_e}</td>"
            f"<td class='mono'>{email_e}</td>"
            f"<td>{comp_e}</td>"
            f"<td>{role_e}</td>"
            f"<td><span class='badge {badge_cls}'>{st.upper()}</span></td>"
            f"<td class='muted'>{sent_e}</td>"
            f"<td class='muted'>{sender_e}</td>"
            f"</tr>"
        )

    rows_html = "\n".join(tr_parts)
    now_label = time.strftime("%d %b %Y, %H:%M:%S UTC", time.gmtime())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Master Outreach Leads Sheet — Live Cloud VM</title>
<style>
  :root {{
    --bg: #0b1020; --panel: #131b33; --border: #243056; --text: #eef2ff; --muted: #94a3b8;
    --accent: #3b82f6; --green-bg: rgba(16,185,129,0.16); --green-fg: #34d399;
    --amber-bg: rgba(245,158,11,0.16); --amber-fg: #fbbf24; --teal-bg: rgba(6,182,212,0.2); --teal-fg: #22d3ee;
  }}
  * {{ box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); padding: 20px; }}
  .wrap {{ max-width: 1440px; margin: 0 auto; }}
  .top {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 14px; margin-bottom: 18px; }}
  h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .btns {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .btn {{ text-decoration: none; padding: 9px 14px; border-radius: 8px; font-size: 13px; font-weight: 600; border: 1px solid var(--border); color: var(--text); background: var(--panel); }}
  .btn.primary {{ background: #2563eb; border-color: #3b82f6; color: #fff; }}
  .btn.green {{ background: #059669; border-color: #10b981; color: #fff; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .kpi {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }}
  .kpi .lbl {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
  .kpi .val {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .controls {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: center; }}
  input[type="search"] {{ flex: 1; min-width: 240px; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel); color: var(--text); font-size: 14px; }}
  .tab {{ cursor: pointer; padding: 8px 13px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel); color: var(--muted); font-size: 13px; font-weight: 600; }}
  .tab.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
  .table-wrap {{ overflow-x: auto; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; max-height: 76vh; overflow-y: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ position: sticky; top: 0; background: #192447; color: #cbd5e1; text-align: left; padding: 11px 12px; border-bottom: 1px solid var(--border); z-index: 2; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid rgba(255,255,255,0.05); white-space: nowrap; }}
  tr:hover td {{ background: rgba(59,130,246,0.08); }}
  .fw {{ font-weight: 600; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; color: #93c5fd; }}
  .muted {{ color: var(--muted); font-size: 12px; }}
  .badge {{ padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
  .st-sent {{ background: var(--green-bg); color: var(--green-fg); }}
  .st-pending {{ background: var(--amber-bg); color: var(--amber-fg); }}
  .st-replied {{ background: var(--teal-bg); color: var(--teal-fg); }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>Master Recruiter & HR Leads Sheet (24/7 Cloud VM Live)</h1>
      <div class="sub">100% Bounce-Purged Active Data • Auto-synced every 6s • Snapshot: {now_label}</div>
    </div>
    <div class="btns">
      <a class="btn green" href="/download/MASTER_LEADS_SHEET.xlsx">📥 Download Excel (.xlsx)</a>
      <a class="btn primary" href="/download/ACTIVE_DELIVERED_LEADS.csv">📄 Download CSV (.csv)</a>
      <a class="btn" href="/p/dashboard">📊 Open Dashboard</a>
    </div>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="lbl">Clean Active Leads</div><div class="val">{total_clean:,}</div></div>
    <div class="kpi"><div class="lbl">Delivered (Sent)</div><div class="val" style="color:#34d399">{sent_cnt:,}</div></div>
    <div class="kpi"><div class="lbl">Pending Queue</div><div class="val" style="color:#fbbf24">{pend_cnt:,}</div></div>
    <div class="kpi"><div class="lbl">Recruiter Replies</div><div class="val" style="color:#22d3ee">{rep_cnt:,}</div></div>
    <div class="kpi"><div class="lbl">Bounces Auto-Deleted</div><div class="val" style="color:#f87171">{blocked_cnt:,}</div></div>
  </div>

  <div class="controls">
    <input id="q" type="search" placeholder="Search name, email, company, or sender inbox..." oninput="filterRows()"/>
    <button class="tab active" onclick="setTab('all', this)">All ({total_clean:,})</button>
    <button class="tab" onclick="setTab('sent', this)">Sent ({sent_cnt:,})</button>
    <button class="tab" onclick="setTab('pending', this)">Pending ({pend_cnt:,})</button>
    <button class="tab" onclick="setTab('replied', this)">Replied ({rep_cnt:,})</button>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Name</th><th>Corporate Email</th><th>Company</th><th>Role Target</th><th>Status</th><th>Sent Timestamp</th><th>Sender Account</th>
        </tr>
      </thead>
      <tbody id="tb">
        {rows_html}
      </tbody>
    </table>
  </div>
</div>
<script>
let curTab = 'all';
function setTab(st, btn) {{
  curTab = st;
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterRows();
}}
function filterRows() {{
  const q = document.getElementById('q').value.toLowerCase().trim();
  const trs = document.getElementById('tb').rows;
  for (let i = 0; i < trs.length; i++) {{
    const tr = trs[i];
    const st = tr.getAttribute('data-status');
    const matchTab = (curTab === 'all') || (curTab === 'sent' && (st === 'sent' || st === 'unresponsive')) || (st === curTab);
    const matchQ = !q || tr.innerText.toLowerCase().includes(q);
    tr.style.display = (matchTab && matchQ) ? '' : 'none';
  }}
}}
</script>
</body>
</html>"""


def get_cached_sheet_html() -> bytes:
    now = time.time()
    if not _CACHE["sheet_html"] or (now - _CACHE["sheet_ts"]) > 10.0:
        _CACHE["sheet_html"] = build_live_sheet_html().encode("utf-8")
        _CACHE["sheet_ts"] = now
    return _CACHE["sheet_html"]


class DashboardHandler(BaseHTTPRequestHandler):
    def _send_bytes(self, body: bytes, content_type: str, download_name: str = "", head_only: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_HEAD(self):
        self._handle_req(head_only=True)

    def do_GET(self):
        self._handle_req(head_only=False)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/api/priority-task/complete":
            key = (qs.get("key") or [""])[0]
            if key != _get_bridge_key():
                self.send_response(403)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
            _ensure_priority_table(conn)
            conn.execute(
                "UPDATE priority_tasks SET status = ?, result_msg = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (data.get("status", "done"), data.get("result_msg", ""), int(data["id"])),
            )
            conn.commit()
            conn.close()
            self._send_bytes(b'{"ok":true}', "application/json; charset=utf-8")
            return
        self.send_response(404)
        self.end_headers()

    def _handle_req(self, head_only: bool = False):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/healthz":
            self._send_bytes(b"ok", "text/plain; charset=utf-8", head_only=head_only)
            return

        if path == "/api/priority-task/claim" and not head_only:
            key = (qs.get("key") or [""])[0]
            if key != _get_bridge_key():
                self.send_response(403)
                self.end_headers()
                return
            try:
                Path("/tmp/mac_bridge_heartbeat").write_text(str(time.time()), encoding="utf-8")
            except Exception:
                pass
            conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
            _ensure_priority_table(conn)
            cur = conn.cursor()
            row = cur.execute(
                "SELECT id, task_type, payload_json, channel_id FROM priority_tasks WHERE status = ? ORDER BY id ASC LIMIT 1",
                ("pending",),
            ).fetchone()
            if row:
                cur.execute("UPDATE priority_tasks SET status = ? WHERE id = ?", ("running", row[0]))
                conn.commit()
                resp = {"task": {"id": row[0], "task_type": row[1], "payload": json.loads(row[2]), "channel_id": row[3]}}
            else:
                resp = {"task": None}
            conn.close()
            self._send_bytes(json.dumps(resp).encode("utf-8"), "application/json; charset=utf-8")
            return

        if path in ("/", "/p/dashboard", "/dashboard"):
            try:
                self._send_bytes(get_cached_dashboard_html(), "text/html; charset=utf-8", head_only=head_only)
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                if not head_only:
                    self.wfile.write(f"Dashboard error: {exc}".encode("utf-8"))
            return

        if path in ("/sheet", "/leads", "/p/sheet"):
            try:
                self._send_bytes(get_cached_sheet_html(), "text/html; charset=utf-8", head_only=head_only)
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                if not head_only:
                    self.wfile.write(f"Sheet error: {exc}".encode("utf-8"))
            return

        if path in ("/download/MASTER_LEADS_SHEET.xlsx", "/sheet.xlsx"):
            try:
                ensure_fresh_exports()
                self._send_bytes(
                    OUTPUT_XLSX.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "MASTER_LEADS_SHEET.xlsx",
                    head_only=head_only,
                )
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                if not head_only:
                    self.wfile.write(f"Excel export error: {exc}".encode("utf-8"))
            return

        if path in ("/download/WORKING_LEADS_MASTER.xlsx",):
            try:
                ensure_fresh_exports()
                self._send_bytes(
                    OUTPUT_WORKING_XLSX.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "WORKING_LEADS_MASTER.xlsx",
                    head_only=head_only,
                )
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                if not head_only:
                    self.wfile.write(f"Excel export error: {exc}".encode("utf-8"))
            return

        if path in ("/download/ACTIVE_DELIVERED_LEADS.csv", "/sheet.csv"):
            try:
                ensure_fresh_exports()
                self._send_bytes(
                    OUTPUT_ACTIVE_CSV.read_bytes(),
                    "text/csv; charset=utf-8",
                    "ACTIVE_DELIVERED_LEADS.csv",
                    head_only=head_only,
                )
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                if not head_only:
                    self.wfile.write(f"CSV export error: {exc}".encode("utf-8"))
            return

        self.send_response(302)
        self.send_header("Location", "/p/dashboard")
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def main():
    print(f"[DashboardServer] Serving Dashboard & Live Sheet on http://{ADDR}:{PORT} (/p/dashboard, /sheet, /download/...)")
    server = ThreadingHTTPServer((ADDR, PORT), DashboardHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
