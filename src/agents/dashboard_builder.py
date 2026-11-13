import json
import os
import shutil
import sqlite3
from datetime import datetime
from ..db.database import get_connection

FOLIO_DATA = os.environ.get("FOLIO_DATA", os.path.expanduser("/Users/admin/linkedin-multiagent/services/folio/data"))

def build_dashboard_html() -> str:
    conn = get_connection()
    posts = conn.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT 10").fetchall()
    jobs = conn.execute("SELECT * FROM jobs ORDER BY match_score DESC, created_at DESC LIMIT 15").fetchall()

    post_rows = ""
    for p in posts:
        post_rows += f"""
        <div class="card">
            <div class="card-header">
                <span class="badge badge-post">{p['hook_id']}</span>
                <span class="score">Human Score: <strong>{p['human_score']}/100</strong></span>
                <span class="date">{p['created_at']}</span>
            </div>
            <pre class="content">{p['content']}</pre>
        </div>
        """

    job_rows = ""
    for j in jobs:
        reasons = json.loads(j['scorecard']).get('reasons', [])
        reasons_html = "".join(f"<li>{r}</li>" for r in reasons)
        job_rows += f"""
        <div class="card job-card">
            <div class="card-header">
                <div class="job-title-row">
                    <h3>{j['title']}</h3>
                    <span class="company">{j['company']} &bull; {j['location']}</span>
                </div>
                <div class="score-badge">
                    <span class="score-val">{j['match_score']}</span>
                    <span class="score-lbl">MATCH</span>
                </div>
            </div>
            <ul class="reasons">{reasons_html}</ul>
            <div class="card-footer">
                <span class="posted-tag">{j['posted_at']}</span>
                <a href="{j['url']}" target="_blank" class="apply-btn">Apply on LinkedIn &rarr;</a>
            </div>
        </div>
        """

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LinkedIn Agent 24/7 Command Dashboard</title>
<style>
:root {{
    --bg: #0f1115; --card-bg: #181b22; --border: #282d37;
    --text: #e6edf3; --muted: #8b949e; --accent: #2f81f7;
    --green: #238636; --blue: #1f6feb;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 2rem 1.5rem; line-height: 1.5;
}}
.container {{ max-width: 1100px; margin: 0 auto; }}
header {{
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid var(--border); padding-bottom: 1.5rem; margin-bottom: 2rem;
}}
h1 {{ font-size: 1.6rem; letter-spacing: -0.02em; }}
.status-pill {{
    background: rgba(35, 134, 54, 0.15); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3);
    padding: 0.3rem 0.8rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
}}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }}
@media (max-width: 800px) {{ .grid {{ grid-template-columns: 1fr; }} }}
h2 {{ font-size: 1.2rem; margin-bottom: 1rem; color: #58a6ff; display: flex; align-items: center; gap: 0.5rem; }}
.card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 1.2rem; margin-bottom: 1rem;
}}
.card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.8rem; }}
.badge {{
    font-size: 0.75rem; padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 600; text-transform: uppercase;
}}
.badge-post {{ background: rgba(88, 166, 255, 0.15); color: #58a6ff; }}
.content {{
    white-space: pre-wrap; font-family: inherit; font-size: 0.9rem; color: #c9d1d9;
    background: #0d1117; padding: 1rem; border-radius: 6px; border: 1px solid #21262d;
}}
.company {{ display: block; font-size: 0.85rem; color: var(--muted); margin-top: 0.2rem; }}
.score-badge {{
    background: rgba(35, 134, 54, 0.15); border: 1px solid rgba(63, 185, 80, 0.3); border-radius: 6px;
    padding: 0.4rem 0.6rem; text-align: center;
}}
.score-val {{ font-size: 1.3rem; font-weight: 700; color: #3fb950; line-height: 1; display: block; }}
.score-lbl {{ font-size: 0.65rem; color: #8b949e; letter-spacing: 0.05em; }}
.reasons {{ margin: 0.8rem 0; padding-left: 1.2rem; font-size: 0.85rem; color: #c9d1d9; }}
.card-footer {{ display: flex; justify-content: space-between; align-items: center; margin-top: 1rem; padding-top: 0.8rem; border-top: 1px solid var(--border); }}
.posted-tag {{ font-size: 0.8rem; color: var(--muted); }}
.apply-btn {{
    background: #238636; color: white; text-decoration: none; padding: 0.4rem 0.8rem;
    border-radius: 6px; font-size: 0.85rem; font-weight: 600;
}}
.apply-btn:hover {{ background: #2ea043; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <div>
            <h1>🚀 LinkedIn 24/7 Multi-Agent Operating System</h1>
            <p style="color: var(--muted); font-size: 0.9rem; margin-top: 0.2rem;">Live updates streamed via Cloudflare Tunnel</p>
        </div>
        <div class="status-pill">&bull; 24/7 ONLINE</div>
    </header>
    <div class="grid">
        <div>
            <h2>📝 Recent Post Drafts & Approvals</h2>
            {post_rows}
        </div>
        <div>
            <h2>🔥 Fresh Job Matches (<24h)</h2>
            {job_rows}
        </div>
    </div>
</div>
</body>
</html>
    """
    return html

def publish_dashboard_to_folio(doc_id: str = "dashboard") -> str:
    """
    Writes the rendered dashboard into Folio's data directory.
    Folio serves this at /p/<id> (public) or /a/<id> (private).
    """
    doc_dir = os.path.join(FOLIO_DATA, doc_id)
    os.makedirs(doc_dir, exist_ok=True)

    html_content = build_dashboard_html()
    with open(os.path.join(doc_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_content)

    meta = {
        "id": doc_id,
        "title": "LinkedIn Agent 24/7 Command Dashboard",
        "visibility": "public",
        "folder": "analytics",
        "tags": ["dashboard", "live", "jobs", "posts"],
        "created": datetime.utcnow().isoformat() + "Z",
        "updated": datetime.utcnow().isoformat() + "Z"
    }

    with open(os.path.join(doc_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return f"http://127.0.0.1:8082/p/{doc_id}"

if __name__ == "__main__":
    url = publish_dashboard_to_folio()
    print("Dashboard published to Folio at:", url)
