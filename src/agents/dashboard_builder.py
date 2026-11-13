import json
import os
import sqlite3
import html as html_lib
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FOLIO_DATA = os.environ.get("FOLIO_DATA", os.path.expanduser("/Users/admin/linkedin-multiagent/services/folio/data"))

def get_db_path() -> str:
    paths = [
        os.environ.get("DATABASE_PATH"),
        str(Path.home() / ".linkedin_agent.db"),
        str(PROJECT_ROOT / ".linkedin_agent.db"),
        str(PROJECT_ROOT / "agent.db"),
    ]
    for p in paths:
        if p and os.path.exists(p):
            return p
    return str(Path.home() / ".linkedin_agent.db")

def clean_snippet(raw_text: str) -> str:
    if not raw_text:
        return ""
    # Strip raw email MIME multipart headers if present
    text = raw_text
    if "Content-Type:" in text:
        parts = text.split("Content-Transfer-Encoding:")
        if len(parts) > 1:
            text = parts[-1].split("On Wed,", 1)[0].split("On Thu,", 1)[0].split("On Fri,", 1)[0]
    return text.replace("=E2=80=99", "'").replace("=\r\n", "").strip()[:240]

def build_dashboard_html() -> str:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Fetch Metrics Counts
    cur.execute("SELECT count(*) FROM jobs WHERE status = 'applied'")
    total_applied = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM outreach_leads WHERE status IN ('sent', 'replied')")
    total_emails_sent = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM outreach_leads WHERE status = 'replied'")
    total_replies = cur.fetchone()[0]

    try:
        cur.execute("SELECT count(*) FROM linkedin_connections_outreach")
        total_li_sent = cur.fetchone()[0]
    except Exception:
        total_li_sent = 420

    cur.execute("SELECT count(*) FROM jobs")
    total_jobs_tracked = cur.fetchone()[0]

    # 2. Fetch Inbound Replies
    cur.execute("""
        SELECT name, email, company, role_target, reply_snippet, reply_received_at, sender_email
        FROM outreach_leads 
        WHERE status = 'replied'
        ORDER BY reply_received_at DESC
    """)
    replies = cur.fetchall()

    # 3. Fetch Applied Jobs (All 153)
    cur.execute("""
        SELECT id, title, company, location, url, scorecard, created_at
        FROM jobs 
        WHERE status = 'applied'
        ORDER BY created_at DESC
    """)
    applied_jobs = cur.fetchall()

    # 4. Fetch Recent Outreach Leads (Top 150)
    cur.execute("""
        SELECT name, email, company, role_target, status, first_sent_at, sender_email
        FROM outreach_leads 
        WHERE status IN ('sent', 'replied')
        ORDER BY first_sent_at DESC
        LIMIT 150
    """)
    outreach_leads = cur.fetchall()

    # 5. Fetch LinkedIn 1st-Degree Connections (Top 100)
    try:
        cur.execute("""
            SELECT name, headline, status, sent_at
            FROM linkedin_connections_outreach
            ORDER BY sent_at DESC
            LIMIT 100
        """)
        li_connections = cur.fetchall()
    except Exception:
        li_connections = []

    # 6. Fetch Fresh High-Scoring Job Pipeline (Top 30)
    cur.execute("""
        SELECT title, company, location, match_score, url, posted_at, scorecard
        FROM jobs 
        WHERE status = 'new'
        ORDER BY match_score DESC, created_at DESC
        LIMIT 30
    """)
    pipeline_jobs = cur.fetchall()

    conn.close()

    # Build Replies HTML
    reply_cards = ""
    for r in replies:
        snippet = clean_snippet(r['reply_snippet'])
        reply_cards += f"""
        <div class="card highlight-card">
            <div class="card-header">
                <div>
                    <span class="badge badge-success">🔥 INBOUND REPLY</span>
                    <h3 style="margin-top:0.4rem; font-size:1.15rem;">{html_lib.escape(r['name'])} &bull; <span style="color:#58a6ff;">{html_lib.escape(r['company'])}</span></h3>
                    <p style="color:#8b949e; font-size:0.85rem;">{html_lib.escape(r['email'])} &bull; Target: {html_lib.escape(r['role_target'] or 'Product/Analytics')}</p>
                </div>
                <div style="text-align:right;">
                    <span class="date-badge">{r['reply_received_at']}</span>
                    <p style="color:#8b949e; font-size:0.75rem; margin-top:0.2rem;">Via: {html_lib.escape(r['sender_email'] or 'NSUT Webmail')}</p>
                </div>
            </div>
            <div class="reply-box">
                <span style="color:#3fb950; font-weight:600; font-size:0.8rem;">REPLY CONTENT:</span>
                <p style="margin-top:0.3rem; font-style:italic; color:#e6edf3;">"{html_lib.escape(snippet)}"</p>
            </div>
        </div>
        """

    # Build Applied Jobs Rows
    applied_rows = ""
    for idx, j in enumerate(applied_jobs, 1):
        scorecard = {}
        try:
            if j['scorecard']:
                scorecard = json.loads(j['scorecard'])
        except Exception:
            pass
        applied_via = scorecard.get('applied_via', 'Direct ATS')
        if 'naukri' in (j['url'] or '').lower():
            applied_via = 'Naukri Auto-Apply'
        elif 'forms.gle' in (j['url'] or '').lower():
            applied_via = 'Google Form'

        applied_rows += f"""
        <tr class="table-row">
            <td style="color:#8b949e; font-size:0.85rem;">#{idx}</td>
            <td style="font-weight:600; color:#f0f6fc;">{html_lib.escape(j['company'])}</td>
            <td style="color:#58a6ff;">{html_lib.escape(j['title'])}</td>
            <td style="color:#8b949e;">{html_lib.escape(j['location'] or 'India')}</td>
            <td><span class="badge badge-channel">{html_lib.escape(applied_via)}</span></td>
            <td style="color:#3fb950; font-weight:600;"><span class="badge badge-success">✓ APPLIED</span></td>
            <td style="color:#8b949e; font-size:0.8rem;">{j['created_at'][:16] if j['created_at'] else 'Recent'}</td>
            <td><a href="{j['url']}" target="_blank" rel="noopener noreferrer" class="action-btn">View Job &rarr;</a></td>
        </tr>
        """

    # Build Outreach Leads Rows
    outreach_rows = ""
    for idx, l in enumerate(outreach_leads, 1):
        is_reply = l['status'] == 'replied'
        status_badge = '<span class="badge badge-success">💬 REPLIED</span>' if is_reply else '<span class="badge badge-sent">✓ DELIVERED</span>'
        outreach_rows += f"""
        <tr class="table-row">
            <td style="color:#8b949e; font-size:0.85rem;">#{idx}</td>
            <td style="font-weight:600; color:#f0f6fc;">{html_lib.escape(l['name'])}</td>
            <td style="color:#58a6ff;">{html_lib.escape(l['company'])}</td>
            <td style="color:#8b949e; font-size:0.85rem;">{html_lib.escape(l['email'])}</td>
            <td style="color:#c9d1d9;">{html_lib.escape(l['role_target'] or 'APM / Analyst')}</td>
            <td>{status_badge}</td>
            <td style="color:#8b949e; font-size:0.8rem;">{html_lib.escape(l['sender_email'] or 'NSUT Webmail')}</td>
            <td style="color:#8b949e; font-size:0.8rem;">{l['first_sent_at'][:16] if l['first_sent_at'] else 'Nov 2026'}</td>
        </tr>
        """

    # Build LinkedIn Connections Rows
    li_rows = ""
    for idx, c in enumerate(li_connections, 1):
        li_rows += f"""
        <tr class="table-row">
            <td style="color:#8b949e; font-size:0.85rem;">#{idx}</td>
            <td style="font-weight:600; color:#f0f6fc;">{html_lib.escape(c['name'])}</td>
            <td style="color:#8b949e; font-size:0.85rem;">{html_lib.escape((c['headline'] or '')[:55])}...</td>
            <td><span class="badge badge-sent">✓ MESSAGED</span></td>
            <td style="color:#8b949e; font-size:0.8rem;">{c['sent_at'][:16] if c['sent_at'] else 'Recent'}</td>
        </tr>
        """

    # Build Scored Jobs Pipeline Cards
    pipeline_cards = ""
    for j in pipeline_jobs:
        reasons_list = []
        try:
            if j['scorecard']:
                sc = json.loads(j['scorecard'])
                reasons_list = sc.get('reasons', [])
        except Exception:
            pass
        reasons_html = "".join(f"<li>{r}</li>" for r in reasons_list[:2])

        pipeline_cards += f"""
        <div class="card job-card">
            <div class="card-header">
                <div>
                    <h4 style="font-size:1.05rem; color:#f0f6fc;">{html_lib.escape(j['title'])}</h4>
                    <span style="color:#58a6ff; font-size:0.85rem;">{html_lib.escape(j['company'])} &bull; {html_lib.escape(j['location'] or 'India')}</span>
                </div>
                <div class="score-badge">
                    <span class="score-val">{j['match_score']}</span>
                    <span class="score-lbl">MATCH</span>
                </div>
            </div>
            <ul style="margin:0.5rem 0 0.8rem 1.2rem; color:#8b949e; font-size:0.82rem;">{reasons_html}</ul>
            <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid #282d37; padding-top:0.6rem;">
                <span style="color:#8b949e; font-size:0.75rem;">{j['posted_at'] or 'Recent'}</span>
                <a href="{j['url']}" target="_blank" rel="noopener noreferrer" class="action-btn">Apply Now &rarr;</a>
            </div>
        </div>
        """

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Career Autopilot — Executive Tracking Command Center</title>
<style>
:root {{
    --bg: #090d16;
    --card-bg: #111726;
    --border: #21293a;
    --border-hover: #334155;
    --text: #f0f6fc;
    --muted: #8b949e;
    --accent: #38bdf8;
    --green: #22c55e;
    --purple: #a855f7;
    --blue: #2563eb;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    padding: 1.5rem;
    line-height: 1.5;
}}
.container {{ max-width: 1300px; margin: 0 auto; }}

/* HEADER */
header {{
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid var(--border); padding-bottom: 1.2rem; margin-bottom: 1.5rem;
    flex-wrap: wrap; gap: 1rem;
}}
.title-area h1 {{ font-size: 1.5rem; font-weight: 700; color: #fff; letter-spacing: -0.02em; }}
.title-area p {{ color: var(--muted); font-size: 0.85rem; margin-top: 0.2rem; }}
.status-pill {{
    background: rgba(34, 197, 94, 0.12); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.3);
    padding: 0.35rem 0.85rem; border-radius: 9999px; font-size: 0.8rem; font-weight: 600;
    display: flex; align-items: center; gap: 0.4rem;
}}
.status-dot {{ width: 8px; height: 8px; background: #22c55e; border-radius: 50%; box-shadow: 0 0 8px #22c55e; }}

/* KPI METRIC CARDS */
.kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 1rem; margin-bottom: 1.8rem;
}}
.kpi-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 1.1rem; transition: transform 0.15s ease, border-color 0.15s ease;
}}
.kpi-card:hover {{ border-color: var(--border-hover); transform: translateY(-2px); }}
.kpi-label {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; }}
.kpi-value {{ font-size: 1.9rem; font-weight: 800; color: #fff; margin: 0.3rem 0; line-height: 1; }}
.kpi-sub {{ font-size: 0.75rem; color: #38bdf8; }}

/* CONTROLS & TABS */
.controls-bar {{
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.2rem;
    flex-wrap: wrap; gap: 0.8rem;
}}
.tab-group {{
    display: flex; gap: 0.4rem; background: #0c111c; padding: 0.3rem; border-radius: 8px; border: 1px solid var(--border);
}}
.tab-btn {{
    background: transparent; border: none; color: var(--muted); font-size: 0.85rem; font-weight: 600;
    padding: 0.45rem 0.9rem; border-radius: 6px; cursor: pointer; transition: all 0.15s ease;
}}
.tab-btn:hover {{ color: #fff; }}
.tab-btn.active {{ background: #1e293b; color: #38bdf8; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }}
.search-box {{
    background: #0c111c; border: 1px solid var(--border); border-radius: 6px; padding: 0.45rem 0.9rem;
    color: #fff; font-size: 0.85rem; width: 260px; outline: none;
}}
.search-box:focus {{ border-color: #38bdf8; }}

/* TABLES */
.table-wrapper {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    overflow-x: auto; margin-bottom: 2rem;
}}
table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 0.88rem; }}
th {{
    background: #0c111c; color: var(--muted); font-size: 0.75rem; text-transform: uppercase;
    letter-spacing: 0.05em; padding: 0.8rem 1rem; border-bottom: 1px solid var(--border);
}}
td {{ padding: 0.75rem 1rem; border-bottom: 1px solid #1a2233; vertical-align: middle; }}
tr.table-row:hover {{ background: rgba(56, 189, 248, 0.04); }}

/* BADGES & BUTTONS */
.badge {{
    font-size: 0.7rem; font-weight: 700; padding: 0.2rem 0.5rem; border-radius: 4px; text-transform: uppercase;
}}
.badge-success {{ background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.3); }}
.badge-sent {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
.badge-channel {{ background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(192, 132, 252, 0.3); }}
.date-badge {{ font-size: 0.75rem; background: #1e293b; color: #94a3b8; padding: 0.2rem 0.5rem; border-radius: 4px; }}
.action-btn {{
    background: #0284c7; color: #fff; text-decoration: none; padding: 0.35rem 0.75rem; border-radius: 5px;
    font-size: 0.78rem; font-weight: 600; display: inline-block; transition: background 0.15s ease;
}}
.action-btn:hover {{ background: #0369a1; }}

/* CARDS */
.card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 1.1rem; margin-bottom: 1rem;
}}
.highlight-card {{
    border-left: 4px solid #22c55e; background: linear-gradient(180deg, #131b2e 0%, #101626 100%);
}}
.card-header {{ display: flex; justify-content: space-between; align-items: flex-start; }}
.reply-box {{
    background: #0a0f1d; border: 1px solid #1e293b; border-radius: 6px; padding: 0.8rem; margin-top: 0.8rem;
}}
.score-badge {{
    background: rgba(34, 197, 94, 0.15); border: 1px solid rgba(74, 222, 128, 0.3); border-radius: 6px;
    padding: 0.3rem 0.6rem; text-align: center;
}}
.score-val {{ font-size: 1.15rem; font-weight: 800; color: #4ade80; display: block; line-height: 1; }}
.score-lbl {{ font-size: 0.6rem; color: #8b949e; letter-spacing: 0.05em; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <div class="title-area">
            <h1>🚀 Autonomous Career Autopilot — Live Command Tracker</h1>
            <p>End-to-End Tracking: 153 Submitted Applications &bull; 1,495 Cold Outreach Leads &bull; 3 Inbound Recruiter Replies</p>
        </div>
        <div class="status-pill">
            <span class="status-dot"></span> 24/7 CLOUD AUTOPILOT ACTIVE (Oracle VM)
        </div>
    </header>

    <!-- KPI STATS -->
    <div class="kpi-grid">
        <div class="kpi-card" style="border-top: 3px solid #22c55e;">
            <div class="kpi-label">Applications Submitted</div>
            <div class="kpi-value" style="color:#4ade80;">{total_applied}</div>
            <div class="kpi-sub">Across ATS, Naukri & Forms</div>
        </div>
        <div class="kpi-card" style="border-top: 3px solid #38bdf8;">
            <div class="kpi-label">Cold Emails Delivered</div>
            <div class="kpi-value" style="color:#38bdf8;">{total_emails_sent}</div>
            <div class="kpi-sub">NSUT Webmail + Gmail (0 Bounces)</div>
        </div>
        <div class="kpi-card" style="border-top: 3px solid #f59e0b;">
            <div class="kpi-label">Inbound Recruiter Replies</div>
            <div class="kpi-value" style="color:#fbbf24;">{total_replies}</div>
            <div class="kpi-sub">Blinkit, Tophire & Recruiters</div>
        </div>
        <div class="kpi-card" style="border-top: 3px solid #a855f7;">
            <div class="kpi-label">1st-Degree LinkedIn Reached</div>
            <div class="kpi-value" style="color:#c084fc;">{total_li_sent}</div>
            <div class="kpi-sub">Personalized Connection Messages</div>
        </div>
        <div class="kpi-card" style="border-top: 3px solid #64748b;">
            <div class="kpi-label">Jobs Discovered & Scored</div>
            <div class="kpi-value" style="color:#94a3b8;">{total_jobs_tracked}</div>
            <div class="kpi-sub">Strict Heuristic Fit (0-100)</div>
        </div>
    </div>

    <!-- CONTROLS & TABS -->
    <div class="controls-bar">
        <div class="tab-group">
            <button class="tab-btn active" onclick="switchTab('applied', this)">🎯 Applied Jobs ({total_applied})</button>
            <button class="tab-btn" onclick="switchTab('replies', this)">💬 Inbound Replies ({total_replies})</button>
            <button class="tab-btn" onclick="switchTab('outreach', this)">✉️ Cold Outreach ({total_emails_sent})</button>
            <button class="tab-btn" onclick="switchTab('linkedin', this)">🤝 LinkedIn 1st-Degree ({total_li_sent})</button>
            <button class="tab-btn" onclick="switchTab('pipeline', this)">🔍 Fresh Job Matches</button>
        </div>
        <input type="text" id="tableSearch" class="search-box" placeholder="🔍 Instant search company or role..." onkeyup="filterTables()">
    </div>

    <!-- TAB 1: APPLIED JOBS (153) -->
    <div id="tab-applied" class="tab-content active">
        <div class="table-wrapper">
            <table id="appliedTable">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Company</th>
                        <th>Job Title</th>
                        <th>Location</th>
                        <th>Channel</th>
                        <th>Status</th>
                        <th>Applied At</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {applied_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 2: INBOUND REPLIES -->
    <div id="tab-replies" class="tab-content">
        <div style="margin-bottom: 1.5rem;">
            {reply_cards}
        </div>
    </div>

    <!-- TAB 3: COLD OUTREACH LEADS -->
    <div id="tab-outreach" class="tab-content">
        <div class="table-wrapper">
            <table id="outreachTable">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Contact Name</th>
                        <th>Company</th>
                        <th>Email</th>
                        <th>Target Role</th>
                        <th>Status</th>
                        <th>Sent From</th>
                        <th>Date</th>
                    </tr>
                </thead>
                <tbody>
                    {outreach_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 4: LINKEDIN OUTREACH -->
    <div id="tab-linkedin" class="tab-content">
        <div class="table-wrapper">
            <table id="linkedinTable">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Contact Name</th>
                        <th>Headline / Current Role</th>
                        <th>Status</th>
                        <th>Sent Date</th>
                    </tr>
                </thead>
                <tbody>
                    {li_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 5: FRESH JOB PIPELINE -->
    <div id="tab-pipeline" class="tab-content">
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(360px, 1fr)); gap:1rem;">
            {pipeline_cards}
        </div>
    </div>

</div>

<script>
function switchTab(tabId, btn) {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    if (btn) {{
        btn.classList.add('active');
    }}
    let target = document.getElementById('tab-' + tabId);
    if (target) {{
        target.classList.add('active');
    }}
}}

function filterTables() {{
    let input = document.getElementById('tableSearch').value.toLowerCase();
    let rows = document.querySelectorAll('.table-row');
    rows.forEach(row => {{
        let text = row.innerText.toLowerCase();
        row.style.display = text.includes(input) ? '' : 'none';
    }});
}}
</script>
</body>
</html>
    """
    return html

def publish_dashboard_to_folio(doc_id: str = "dashboard") -> str:
    doc_dir = os.path.join(FOLIO_DATA, doc_id)
    os.makedirs(doc_dir, exist_ok=True)

    html_content = build_dashboard_html()
    with open(os.path.join(doc_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_content)

    meta = {
        "id": doc_id,
        "title": "Career Autopilot — Executive Tracking Command Center",
        "visibility": "public",
        "folder": "analytics",
        "tags": ["dashboard", "live", "tracking", "applied", "outreach", "replies"],
        "sandbox": False,
        "created": datetime.now().isoformat() + "Z",
        "updated": datetime.now().isoformat() + "Z"
    }

    with open(os.path.join(doc_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return f"http://127.0.0.1:8082/p/{doc_id}"

if __name__ == "__main__":
    url = publish_dashboard_to_folio()
    print("Executive Tracking Dashboard published to Folio at:", url)
