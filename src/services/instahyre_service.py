"""
24x7 Zero-RAM Instahyre Auto-Discovery, Auto-Apply & Cookie Sync Service.

Runs directly over Instahyre's authenticated REST API using Python's built-in `urllib`
(<5 MB memory footprint — runs 24/7 on the 498 MB Oracle Cloud VM as well as Local Mac).
"""

import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.discord_notifier import DiscordNotifier
from src.agents.job_hunter import score_job_heuristic
from src.services.exclusion_filter import FORBIDDEN_COMPANIES

COOKIES_FILE = PROJECT_ROOT / "data" / "instahyre_cookies.json"
CLOUD_SSH_TARGET = os.environ.get("CLOUD_SSH_TARGET", "opc@130.210.108.55")
CLOUD_COOKIES_DEST = "/home/opc/career-agent/data/instahyre_cookies.json"

# Target Instahyre job_function IDs:
#   39  = Data Analysis / Business Intelligence
#   11  = Product Management
#   100 = Other Product / Project Management
#   28  = Operations Management
#   34  = General Management / Strategy
TARGET_JOB_FUNCTIONS: List[Tuple[int, str]] = [
    (39, "Data Analysis / BI"),
    (11, "Product Management"),
    (100, "Other Product Management"),
    (28, "Operations Management"),
    (34, "Strategy / Founder's Office"),
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

_SWEEP_COUNTER = 0


def _get_db_path() -> str:
    env_db = os.environ.get("DATABASE_PATH")
    if env_db and os.path.exists(env_db):
        return env_db
    home_db = os.path.expanduser("~/.linkedin_agent.db")
    if os.path.exists(home_db):
        return home_db
    proj_db = str(PROJECT_ROOT / ".linkedin_agent.db")
    if os.path.exists(proj_db):
        return proj_db
    return home_db


def _ensure_jobs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            job_id TEXT UNIQUE,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            posted_at DATETIME,
            url TEXT NOT NULL,
            match_score INTEGER NOT NULL,
            scorecard JSON NOT NULL,
            discord_thread_id TEXT,
            status TEXT DEFAULT 'new',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _load_instahyre_headers(referer: str = "https://www.instahyre.com/candidate/opportunities/?matching=true") -> Optional[Dict[str, str]]:
    if not COOKIES_FILE.exists():
        return None
    try:
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    ih_cookies = [c for c in cookies if "instahyre" in str(c.get("domain", "")).lower()]
    if not ih_cookies:
        return None
    cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in ih_cookies if c.get("name") and c.get("value") is not None)
    csrf = next((str(c["value"]) for c in ih_cookies if c.get("name") == "csrftoken"), "")
    if not cookie_hdr or "sessionid=" not in cookie_hdr:
        return None
    return {
        "Cookie": cookie_hdr,
        "X-CSRFToken": csrf,
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
    }


def _api_get(path: str, headers: Dict[str, str], timeout: float = 15.0) -> Dict[str, Any]:
    url = path if path.startswith("http") else f"https://www.instahyre.com{path}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_post(path: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float = 18.0) -> Tuple[int, Dict[str, Any]]:
    url = path if path.startswith("http") else f"https://www.instahyre.com{path}"
    data = json.dumps(payload).encode("utf-8")
    post_headers = {**headers, "Content-Type": "application/json;charset=UTF-8"}
    req = urllib.request.Request(url, data=data, headers=post_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, (json.loads(raw) if raw.strip() else {})


def _is_forbidden_company_or_text(company: str, title: str = "", extra: str = "") -> bool:
    blob = f"{company} {title} {extra}".lower()
    blob_nopunc = re.sub(r"[^a-z0-9]", "", blob)
    for fc in FORBIDDEN_COMPANIES:
        fc_clean = fc.lower().strip()
        fc_nopunc = re.sub(r"[^a-z0-9]", "", fc_clean)
        if fc_clean and (fc_clean in blob or (len(fc_nopunc) >= 4 and fc_nopunc in blob_nopunc)):
            return True
    return False


def _score_instahyre_role(title: str, company: str) -> int:
    """Scores an Instahyre listing for Vishal's target 0-3 yr Product / Analytics / Founder's Office / FDE profile."""
    if _is_forbidden_company_or_text(company, title):
        return 0
    tl = (title or "").lower().strip()
    # Hard reject pure SWE / DevOps / Design / HR / Legal / Sales leadership roles
    hard_skip = [
        "data engineer", "software engineer", "backend", "frontend", "full stack", "fullstack",
        "android", "ios developer", "devops", "sre", "qa engineer", "sdet", "designer", "ui/ux",
        "ux ", "copywriter", "human resource", "hr ", "talent acquisition", "recruiter",
        "accountant", "chartered accountant", "legal", "counsel", "sales director", "enterprise sales",
        "account executive", "bdm", "business development manager", "nurse", "doctor", "architect",
        "principal", "director", "vp ", "vice president", "head of", "senior ", "sr.", "sr ",
        "lead ", "staff ", "manager ii", "manager iii", "data scientist ii", "data scientist iii",
    ]
    # Exception: Forward Deployed Software Engineer is explicitly desired!
    if "forward deployed" not in tl and any(h in tl for h in hard_skip):
        return 0

    base_score, _ = score_job_heuristic(title, company)
    # Additional boost for strong Instahyre titles that score_job_heuristic doesn't explicitly list
    if any(k in tl for k in ["business associate", "founder office", "founder's office", "program analyst", "strategy analyst", "growth manager", "product specialist", "data specialist"]):
        base_score = max(base_score, 85)
    return base_score


def sync_instahyre_cookies_from_ridealong(push_to_cloud: bool = True) -> bool:
    """
    Exports fresh Instahyre cookies from Mac's persistent Ridealong Chrome session
    and securely syncs them to the 24/7 Cloud VM so the session never expires.
    """
    if sys.platform != "darwin":
        return COOKIES_FILE.exists()
    try:
        from playwright.sync_api import sync_playwright
        from scripts.send_linkedin_person_msg import get_direct_cdp_endpoint

        cdp_ep = get_direct_cdp_endpoint()
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(cdp_ep, timeout=10000)
            if not b.contexts:
                return False
            ih_cookies = [c for c in b.contexts[0].cookies() if "instahyre" in str(c.get("domain", "")).lower()]
            if not ih_cookies or not any(c.get("name") == "sessionid" for c in ih_cookies):
                return False
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_text(json.dumps(ih_cookies, indent=2), encoding="utf-8")
            os.chmod(COOKIES_FILE, 0o600)

        if push_to_cloud:
            ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
            scp_cmd = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8"]
            if os.path.exists(ssh_key):
                scp_cmd.extend(["-i", ssh_key])
            scp_cmd.extend([str(COOKIES_FILE), f"{CLOUD_SSH_TARGET}:{CLOUD_COOKIES_DEST}"])
            subprocess.run(
                scp_cmd,
                capture_output=True,
                timeout=15,
            )
        return True
    except Exception as e:
        print(f"[Instahyre:CookieSync] Note: {e}")
        return False


def _record_instahyre_job(
    conn: sqlite3.Connection,
    job_id: int,
    title: str,
    company: str,
    location: str,
    public_url: str,
    status: str,
    score: int = 88,
    details: str = "",
) -> None:
    _ensure_jobs_table(conn)
    ih_key = f"instahyre_{job_id}"
    clean_url = public_url or f"https://www.instahyre.com/job-{job_id}/"
    scorecard_json = json.dumps(
        {
            "platform": "Instahyre",
            "score": score,
            "details": details[:400],
            "instahyre_job_id": job_id,
        }
    )
    cur = conn.cursor()
    cur.execute("SELECT id, status FROM jobs WHERE id = ? OR url = ? LIMIT 1", (ih_key, clean_url))
    existing = cur.fetchone()
    if existing:
        if existing[1] == "applied" and status != "applied":
            return
        cur.execute(
            """
            UPDATE jobs
            SET status = ?,
                title = COALESCE(NULLIF(?, ''), title),
                company = COALESCE(NULLIF(?, ''), company),
                scorecard = ?
            WHERE id = ?
            """,
            (status, title, company, scorecard_json, existing[0]),
        )
    else:
        cur.execute(
            """
            INSERT INTO jobs (id, job_id, title, company, location, url, match_score, scorecard, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ih_key, str(job_id), title or "Product / Business Analyst", company or "Instahyre Employer", location or "India", clean_url, score, scorecard_json, status),
        )
    conn.commit()


def apply_instahyre_job_by_id(
    job_id: int,
    title: str = "",
    company: str = "",
    public_url: str = "",
    locations: str = "",
    max_workex_min: int = 3,
    enforce_workex: bool = True,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Verifies job details & experience requirements on Instahyre, submits 1-click REST application,
    updates SQLite `jobs` ONLY on verified `"success": true`, and dispatches Discord alert.
    """
    headers = _load_instahyre_headers()
    if not headers:
        return {"submitted": False, "confirmed": False, "error": "missing_instahyre_cookies"}

    own_conn = False
    if conn is None:
        conn = sqlite3.connect(_get_db_path(), timeout=25.0)
        own_conn = True

    try:
        recruiter_info = ""
        workex_range = ""
        try:
            pub = _api_get(f"/api/v1/employer_public_jobs/{job_id}", headers, timeout=12.0)
            title = title or pub.get("title") or pub.get("candidate_title") or "Product / Business Analyst"
            company = company or pub.get("hiring_company_name") or "Instahyre Employer"
            opp_path = pub.get("opportunity_url") or ""
            if opp_path and not public_url:
                public_url = f"https://www.instahyre.com{opp_path}" if opp_path.startswith("/") else opp_path
            locs = pub.get("locations")
            if isinstance(locs, list) and locs:
                locations = ", ".join(str(x) for x in locs)
            if pub.get("is_active") is False:
                _record_instahyre_job(conn, job_id, title, company, locations, public_url, "expired", 0, "inactive")
                return {"submitted": False, "confirmed": False, "error": "job_expired_or_inactive", "title": title, "company": company}

            w_min = pub.get("workex_min")
            w_max = pub.get("workex_max")
            if w_min is not None or w_max is not None:
                workex_range = f"{w_min if w_min is not None else 0}-{w_max if w_max is not None else '+'} yrs"
            if enforce_workex and isinstance(w_min, (int, float)) and w_min > max_workex_min:
                _record_instahyre_job(conn, job_id, title, company, locations, public_url, "skipped_senior", 40, f"workex_min={w_min}")
                return {"submitted": False, "confirmed": False, "error": f"workex_min_too_high_{w_min}yrs", "title": title, "company": company}

            if pub.get("is_internship") and "associate" not in title.lower():
                _record_instahyre_job(conn, job_id, title, company, locations, public_url, "skipped_internship", 35, "internship")
                return {"submitted": False, "confirmed": False, "error": "internship_skipped", "title": title, "company": company}

            rec_name = (pub.get("recruiter_name") or "").strip()
            rec_desig = (pub.get("recruiter_designation") or "").strip()
            if rec_name:
                recruiter_info = f"{rec_name} ({rec_desig})" if rec_desig else rec_name
        except urllib.error.HTTPError as he:
            if he.code == 404:
                return {"submitted": False, "confirmed": False, "error": "job_not_found_404"}
        except Exception:
            pass

        if _is_forbidden_company_or_text(company, title):
            return {"submitted": False, "confirmed": False, "error": "excluded_confidential_employer"}

        status_code, resp = _api_post(
            "/api/v1/candidate_opportunities/candidate_matching/apply",
            {"job_id": int(job_id), "is_interested": True, "is_activity_page_job": False},
            headers,
        )
        confirmed = status_code in (200, 201) and (resp.get("success") is True or bool(resp.get("opp_id")))
        final_url = public_url or f"https://www.instahyre.com/job-{job_id}/"

        if confirmed:
            opp_id = resp.get("opp_id")
            instascreen = resp.get("instascreen")
            detail_parts = [f"Instahyre Opp ID: {opp_id}"]
            if workex_range:
                detail_parts.append(f"Exp: {workex_range}")
            if recruiter_info:
                detail_parts.append(f"Recruiter: {recruiter_info}")
            if instascreen:
                detail_parts.append("Note: InstaScreen assessment link available on dashboard")
            detail_str = " | ".join(detail_parts)

            _record_instahyre_job(conn, job_id, title, company, locations or "India", final_url, "applied", 90, detail_str)
            try:
                DiscordNotifier().send_application_alert(
                    {
                        "company": company,
                        "role": f"{title} ({locations})" if locations else title,
                        "platform": "Instahyre 24/7 Auto-Apply",
                        "url": final_url,
                        "status": "SUBMITTED & CONFIRMED",
                        "details": detail_str,
                        "resume": "Vishal_Gupta.pdf (Instahyre Profile CV)",
                    }
                )
            except Exception:
                pass
            return {
                "submitted": True,
                "confirmed": True,
                "job_id": job_id,
                "opp_id": opp_id,
                "title": title,
                "company": company,
                "locations": locations,
                "url": final_url,
                "details": detail_str,
            }

        return {
            "submitted": False,
            "confirmed": False,
            "job_id": job_id,
            "title": title,
            "company": company,
            "url": final_url,
            "error": str(resp.get("message") or f"http_{status_code}"),
        }
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            pass
        return {"submitted": False, "confirmed": False, "error": f"http_{e.code}:{err_body}"}
    except Exception as e:
        return {"submitted": False, "confirmed": False, "error": str(e)}
    finally:
        if own_conn:
            conn.close()


def apply_instahyre_url(url: str) -> Dict[str, Any]:
    """Applies immediately to an Instahyre job URL (used by #prompt Priority #0 `apply`)."""
    m = re.search(r"job-(\d+)", url) or re.search(r"[?&]job_id=(\d+)", url) or re.search(r"/(\d{5,8})(?:[/?#]|$)", url)
    if not m:
        return {"submitted": False, "confirmed": False, "error": "could_not_extract_instahyre_job_id"}
    jid = int(m.group(1))
    # When Vishal explicitly pastes a link in #prompt, do not block on workex_min!
    return apply_instahyre_job_by_id(jid, public_url=url, enforce_workex=False)


def run_instahyre_auto_apply(max_apps: int = 10) -> Dict[str, Any]:
    """
    24/7 Instahyre sweep:
      1. Pulls personalized `candidate_matching` opportunities (`interest_facet=0`)
      2. Scans `job_search` across all 5 target functions (`39, 11, 100, 28, 34`) with rotating pagination
      3. Filters strictly for 0-3 yr Product / Analytics / Founder's Office / FDE roles & blocks confidential employers
      4. Submits verified REST applications (`0 MB` browser overhead) and logs receipts to SQLite + Discord `#agent`.
    """
    global _SWEEP_COUNTER
    _SWEEP_COUNTER += 1

    headers = _load_instahyre_headers()
    if not headers:
        print("[Instahyre] No valid data/instahyre_cookies.json found yet — skipping sweep.")
        return {"scanned": 0, "applied": 0, "status": "no_cookies"}

    conn = sqlite3.connect(_get_db_path(), timeout=30.0)
    _ensure_jobs_table(conn)
    cur = conn.cursor()
    seen_db_ids = {
        r[0]
        for r in cur.execute("SELECT id FROM jobs WHERE id LIKE 'instahyre_%'").fetchall()
    }
    seen_db_urls = {
        r[0]
        for r in cur.execute("SELECT url FROM jobs WHERE url LIKE '%instahyre.com%' AND status IN ('applied', 'expired', 'skipped_senior', 'skipped_internship')").fetchall()
    }

    candidates: List[Dict[str, Any]] = []
    seen_batch_jids = set()

    # 1. Personalized Matching Opportunities (highest priority!)
    try:
        cm = _api_get("/api/v1/candidate_opportunities/candidate_matching?interest_facet=0&limit=30&offset=0", headers)
        for obj in cm.get("objects", []):
            jid = obj.get("job_id") or (obj.get("job") or {}).get("id") or obj.get("id")
            if not jid or jid in seen_batch_jids:
                continue
            emp = obj.get("employer") or {}
            comp = emp.get("company_name") or obj.get("company_name") or ""
            title = obj.get("title") or (obj.get("job") or {}).get("title") or obj.get("candidate_title") or ""
            pub_url = obj.get("public_url") or ""
            locs = obj.get("locations") or ""
            if isinstance(locs, list):
                locs = ", ".join(str(x) for x in locs)
            if f"instahyre_{jid}" in seen_db_ids or (pub_url and pub_url in seen_db_urls):
                continue
            sc = _score_instahyre_role(title, comp)
            if sc >= 75:
                seen_batch_jids.add(jid)
                candidates.append(
                    {
                        "job_id": int(jid),
                        "title": title,
                        "company": comp,
                        "public_url": pub_url,
                        "locations": str(locs),
                        "score": sc + 15,  # Boost direct Instahyre matches
                    }
                )
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            conn.close()
            print(f"[Instahyre] Session expired or unauthorized (HTTP {e.code}). Waiting for next Mac cookie sync.")
            return {"scanned": 0, "applied": 0, "status": f"auth_expired_{e.code}"}
    except Exception as e:
        print(f"[Instahyre] candidate_matching note: {e}")

    # 2. Portal-Wide Multi-Function Search (rotates offsets 0, 35, 70, 105 across sweeps!)
    page_offset = ((_SWEEP_COUNTER - 1) % 5) * 35
    total_scanned = len(seen_batch_jids)
    for fn_id, _fn_label in TARGET_JOB_FUNCTIONS:
        try:
            js = _api_get(
                f"/api/v1/job_search?company_size=0&job_functions={fn_id}&job_type=0&status=0&workex=1&limit=35&offset={page_offset}",
                headers,
            )
            objs = js.get("objects", [])
            total_scanned += len(objs)
            for obj in objs:
                jid = obj.get("id")
                if not jid or jid in seen_batch_jids:
                    continue
                if obj.get("interview_status"):
                    continue
                if obj.get("is_active") is False:
                    continue
                emp = obj.get("employer") or {}
                comp = emp.get("company_name") or ""
                title = obj.get("title") or obj.get("candidate_title") or ""
                pub_url = obj.get("public_url") or ""
                locs = obj.get("locations") or ""
                if isinstance(locs, list):
                    locs = ", ".join(str(x) for x in locs)
                if f"instahyre_{jid}" in seen_db_ids or (pub_url and pub_url in seen_db_urls):
                    continue
                sc = _score_instahyre_role(title, comp)
                if sc >= 75:
                    seen_batch_jids.add(jid)
                    candidates.append(
                        {
                            "job_id": int(jid),
                            "title": title,
                            "company": comp,
                            "public_url": pub_url,
                            "locations": str(locs),
                            "score": sc,
                        }
                    )
        except Exception as e:
            print(f"[Instahyre] job_search fn={fn_id} note: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    applied_count = 0
    applied_roles: List[str] = []

    for cand in candidates:
        if applied_count >= max_apps:
            break
        res = apply_instahyre_job_by_id(
            job_id=cand["job_id"],
            title=cand["title"],
            company=cand["company"],
            public_url=cand["public_url"],
            locations=cand["locations"],
            max_workex_min=3,
            enforce_workex=True,
            conn=conn,
        )
        if res.get("confirmed"):
            applied_count += 1
            applied_roles.append(f"{res.get('company')} — {res.get('title')}")
            print(f"  ✅ [Instahyre] Applied ({applied_count}/{max_apps}): {res.get('company')} — {res.get('title')} ({res.get('url')})")
            time.sleep(random.uniform(1.8, 3.6))
        else:
            time.sleep(0.35)

    conn.close()
    print(
        f"[Instahyre] Sweep #{_SWEEP_COUNTER} (offset={page_offset}) complete: "
        f"Scanned {total_scanned} listings | Qualified {len(candidates)} | Confirmed {applied_count} new applications."
    )
    return {
        "scanned": total_scanned,
        "qualified": len(candidates),
        "applied": applied_count,
        "applied_roles": applied_roles,
        "status": "ok",
    }
