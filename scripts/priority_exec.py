#!/usr/bin/env python3
"""
Priority #0 Immediate Task Executor for Discord #prompt (ALL Tasks — Not Just LinkedIn!).
Whenever Vishal commands something in #prompt, this executes it IMMEDIATELY on Priority #0
(bypassing background queues) and returns the REAL VERIFIED COMPLETION RESULT synchronously!

Subcommands:
  1) linkedin-msg : Immediate custom or pitch LinkedIn message (preempts Mac Ridealong, waits synchronously for receipt)
  2) email        : Immediate SMTP dispatch right now (custom text or tailored cold pitch + CV, bypasses 2,200-lead queue)
  3) apply        : Immediate job/form application right now (ATS/Form inline on Cloud VM, or LinkedIn Easy Apply on Mac)
  4) mac-sh       : Immediate shell execution on Local Mac via 1s HTTPS Priority Bridge
"""

import argparse
import html as _html
import json
import os
import re
import smtplib
import sqlite3
import sys
import time
import urllib.request
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Any, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")


def ensure_priority_table(conn: sqlite3.Connection) -> None:
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


def dispatch_and_wait_mac(task_type: str, payload: Dict[str, Any], channel_id: str = "", timeout_s: float = 42.0) -> str:
    """Enqueues a Priority #0 Mac task and waits synchronously until Mac completes it (~3-15s)."""
    if sys.platform == "darwin":
        return execute_mac_task_locally(task_type, payload)

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    ensure_priority_table(conn)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO priority_tasks (task_type, payload_json, channel_id, status) VALUES (?, ?, ?, ?)",
        (task_type, json.dumps(payload), channel_id, "pending"),
    )
    tid = cur.lastrowid
    conn.commit()
    conn.close()

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.35)
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        row = conn.execute("SELECT status, result_msg FROM priority_tasks WHERE id = ?", (tid,)).fetchone()
        conn.close()
        if row and row[0] in ("done", "failed"):
            return row[1] or f"Task #{tid} finished ({row[0]})."

    return (
        f"⏳ Priority task #{tid} is dispatched and running on Local Mac (`{task_type}`) — "
        "completion receipt will post here as soon as Ridealong finishes."
    )


def _get_ridealong_viewer_url() -> str:
    try:
        import glob
        logs = sorted(glob.glob(os.path.expanduser("~/.gemini/antigravity-cli/brain/*/.system_generated/tasks/*.log")), key=os.path.getmtime, reverse=True)
        for lp in logs[:25]:
            txt = Path(lp).read_text(encoding="utf-8", errors="ignore")
            if "127.0.0.1:8083" in txt or "localhost:8083" in txt:
                ms = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
                if ms:
                    return ms[-1]
    except Exception:
        pass
    return "http://127.0.0.1:8083"


def execute_mac_task_locally(task_type: str, payload: Dict[str, Any]) -> str:
    if task_type == "linkedin_msg":
        from scripts.send_linkedin_person_msg import send_person_outreach
        res = send_person_outreach(
            query=payload.get("query", ""),
            name_hint=payload.get("name", ""),
            company_hint=payload.get("company", ""),
            role_hint=payload.get("role", ""),
            custom_message=payload.get("custom_message", ""),
            attach_cv=payload.get("attach_cv"),
        )
        return res.get("message") or json.dumps(res)
    elif task_type == "linkedin_apply":
        import hashlib
        from playwright.sync_api import sync_playwright
        from scripts.send_linkedin_person_msg import get_direct_cdp_endpoint, DEVTOOLS_PORT_FILE
        from src.agents.linkedin_job_applicant import _acquire_cdp_page, _try_apply_on_loaded_linkedin_job, _sync_job_statuses_to_cloud_vm

        url = payload.get("url", "").strip()
        jid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        port = DEVTOOLS_PORT_FILE.read_text(encoding="utf-8").splitlines()[0].strip()
        cdp_ep = get_direct_cdp_endpoint()
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        ok = False
        try:
            with sync_playwright() as p:
                b = p.chromium.connect_over_cdp(cdp_ep, timeout=12000)
                pg, ctx = _acquire_cdp_page(b, port)
                try:
                    ok = _try_apply_on_loaded_linkedin_job(pg, ctx, port, jid, "Product / Business Analyst", "LinkedIn", url, conn)
                finally:
                    try:
                        pg.close()
                    except Exception:
                        pass
            _sync_job_statuses_to_cloud_vm(conn)
        finally:
            conn.close()
        if ok:
            return f"✅ **LinkedIn Job Application Submitted & Confirmed Immediately!**\n• **URL:** {url}"
        return f"⚠️ **LinkedIn Job Attempted — Not Auto-Confirmed (`NOT` marked applied):**\n• **URL:** {url}\n• Either already applied, closed, or gated by an external portal verification."
    elif task_type == "ats_apply":
        from playwright.sync_api import sync_playwright
        from scripts.send_linkedin_person_msg import get_direct_cdp_endpoint, DEVTOOLS_PORT_FILE
        from src.agents.linkedin_job_applicant import _acquire_cdp_page
        from src.services.ats_filler import fill_ats_application
        from src.agents.discord_notifier import DiscordNotifier

        url = payload.get("url", "").strip()
        role_hint = payload.get("role") or "Product / Business Analyst"
        port = DEVTOOLS_PORT_FILE.read_text(encoding="utf-8").splitlines()[0].strip()
        cdp_ep = get_direct_cdp_endpoint()
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(cdp_ep, timeout=12000)
            pg, _ = _acquire_cdp_page(b, port)
            res = fill_ats_application(url, role_title=role_hint, auto_submit=True, existing_page=pg, leave_open=True)
            final_u = res.get("ats_url") or url
            ats_t = res.get("ats_type") or "generic"
            err = res.get("error") or ""
            if res.get("submitted") and res.get("confirmed"):
                try:
                    pg.close()
                except Exception:
                    pass
                _record_job_attempt(final_u, role_hint, ats_t.upper(), "applied", ats_t)
                try:
                    DiscordNotifier().send_application_alert({
                        "company": ats_t.upper(),
                        "role": role_hint,
                        "platform": f"Persistent Chrome CDP ({ats_t.upper()})",
                        "url": final_u,
                        "status": "SUBMITTED & CONFIRMED",
                        "details": f"Filled {len(res.get('fields_filled', []))} fields + uploaded Vishal_Gupta_Resume.pdf via Ridealong CDP!",
                        "resume": "Vishal_Gupta_Resume.pdf",
                    })
                except Exception:
                    pass
                return (
                    f"✅ **Application Submitted & Confirmed Immediately (Persistent Chrome CDP)!**\n"
                    f"• **ATS:** `{ats_t}`\n"
                    f"• **URL:** {final_u}\n"
                    f"• **Fields Filled:** `{len(res.get('fields_filled', []))}` | **Resume Uploaded:** `{res.get('resume_uploaded')}`"
                )
            if err == "expired":
                try:
                    pg.close()
                except Exception:
                    pass
                _record_job_attempt(final_u, role_hint, ats_t.upper(), "expired", ats_t, "expired")
                return f"⚠️ **Job Expired / Closed by Company (`404`):** {final_u}"
            if err in ("human_verification_code_required_in_email", "captcha_gate_requires_manual_apply"):
                _record_job_attempt(final_u, role_hint, ats_t.upper(), "external_pending_review", ats_t, err)
                viewer = _get_ridealong_viewer_url()
                if err == "human_verification_code_required_in_email":
                    return (
                        f"🛡️ **100% Pre-Filled in Ridealong — Only 8-Char Email Code Needed (`NOT` marked applied yet):**\n"
                        f"• **Portal:** `{ats_t}` ({final_u})\n"
                        f"• **Pre-Filled:** `{len(res.get('fields_filled', []))}` fields + `Vishal_Gupta_Resume.pdf` left open in your Ridealong browser!\n"
                        f"• **5-Second Finish:** Open {viewer} and paste the 8-character security code sent to `vishal90301@gmail.com`."
                    )
                return (
                    f"🛡️ **Pre-Filled up to CAPTCHA Gate (`NOT` marked applied — 100% honest status):**\n"
                    f"• **Portal:** `{ats_t}` ({final_u})\n"
                    f"• **Live Pre-Filled Tab:** {viewer} (or click {final_u} directly)."
                )
            try:
                pg.close()
            except Exception:
                pass
            _record_job_attempt(final_u, role_hint, ats_t.upper(), "external_pending_review", ats_t, err)
            return (
                f"⚠️ **Application Attempted — Not Confirmed (`NOT` marked applied):**\n"
                f"• **Portal:** `{ats_t}` ({final_u})\n"
                f"• **Reason:** `{err or 'unconfirmed_after_submit'}` | **Fields Filled:** `{len(res.get('fields_filled', []))}`"
            )
    elif task_type == "agy_turn":
        from src.agents.discord_listener import DiscordListener
        dl = DiscordListener(channel_id=payload.get("channel_id") or "1570342549435187290")
        return dl.run_antigravity_cli_turn(
            payload.get("prompt", ""),
            payload.get("channel_id") or "1570342549435187290",
            payload.get("msg_id") or "",
        )
    elif task_type == "mac_sh":
        import subprocess
        proc = subprocess.run(
            payload.get("cmd", ""),
            shell=True,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[:1800] or "Exit 0"
    return f"Unknown task_type: {task_type}"


def run_priority_email(args) -> str:
    from src.services.cold_mailer import compose_cold_email, send_cold_email_smtp, get_sender_credentials
    from src.services.exclusion_filter import FORBIDDEN_COMPANIES

    recip = args.to.strip()
    if not args.body and any(fc in f"{recip} {args.company}".lower() for fc in FORBIDDEN_COMPANIES):
        return f"🛑 Blocked: `{recip}` matches restricted company exclusion rule."

    if args.body:
        # Custom immediate email!
        sender, app_pwd = get_sender_credentials(args.sender or None)
        subj = args.subject or "Quick note from Vishal Gupta"
        msg = MIMEMultipart()
        msg["From"] = f"Vishal Gupta <{sender}>"
        msg["To"] = recip
        msg["Subject"] = subj
        msg.attach(MIMEText(args.body, "plain"))
        if not args.no_cv:
            pdf_p = PROJECT_ROOT / "data" / "resume.pdf"
            if pdf_p.exists():
                part = MIMEApplication(pdf_p.read_bytes(), Name="Vishal_Gupta_Resume.pdf")
                part["Content-Disposition"] = 'attachment; filename="Vishal_Gupta_Resume.pdf"'
                msg.attach(part)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as srv:
            srv.ehlo()
            srv.starttls()
            srv.ehlo()
            srv.login(sender, app_pwd)
            srv.sendmail(sender, [recip], msg.as_string())
        return (
            f"✅ **Priority Email Sent Immediately!**\n"
            f"• **To:** `{recip}` (from `{sender}`)\n"
            f"• **Subject:** `{subj}`\n"
            f"• **CV Attached:** `{'No' if args.no_cv else 'Yes'}`"
        )
    else:
        draft = compose_cold_email(
            recipient_email=recip,
            role_title=args.role or "APM / Product & Business Analyst",
            company_name=args.company or "",
            preferred_sender=args.sender or None,
        )
        subj = args.subject or draft["subject"]
        ok = send_cold_email_smtp(recip, subj, draft["body"], sender_email=draft["sender"])
        if not ok:
            return f"❌ Immediate email to `{recip}` could not be sent (failed MX/SMTP check)."

        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        conn.execute(
            """
            INSERT INTO outreach_leads (name, email, company, role_target, status, first_sent_at, last_sent_at, sender_email)
            VALUES (?, ?, ?, ?, 'sent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(email) DO UPDATE SET
                status='sent',
                last_sent_at=CURRENT_TIMESTAMP,
                sender_email=excluded.sender_email
            """,
            (args.name or "Hiring Team", recip.lower(), args.company or "", args.role or "Product / Analytics", draft["sender"]),
        )
        conn.commit()
        conn.close()
        return (
            f"✅ **Priority Cold Email + CV Sent Immediately!**\n"
            f"• **To:** `{recip}` (`{args.company or 'Direct'}`)\n"
            f"• **Sender:** `{draft['sender']}`\n"
            f"• **Subject:** `{subj}`"
        )


def _is_mac_awake() -> bool:
    if sys.platform == "darwin":
        return True
    try:
        hb = Path("/tmp/mac_bridge_heartbeat")
        if hb.exists():
            age = time.time() - float(hb.read_text(encoding="utf-8").strip())
            return age < 20.0
    except Exception:
        pass
    return False


def unwrap_linkedin_short_or_post_url(raw_url: str) -> Dict[str, Any]:
    """
    Unwraps lnkd.in/p/... and linkedin.com/posts/... directly from Cloud VM (zero Mac needed!)
    using Discordbot/2.0 OpenGraph HTML + secondary lnkd.in redirect resolution.
    Returns: {"target_urls": [...], "emails": [...], "post_title": ..., "post_text": ...}
    """
    info: Dict[str, Any] = {"target_urls": [], "emails": [], "post_title": "", "post_text": "", "final_url": raw_url}
    headers = {"User-Agent": "Discordbot/2.0 (+https://discordapp.com)"}
    try:
        req = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(req, timeout=14) as resp:
            final_u = resp.geturl()
            info["final_url"] = final_u
            if "linkedin.com" not in final_u.lower() and "lnkd.in" not in final_u.lower():
                info["target_urls"].append(final_u)
                return info
            raw_html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        info["error"] = str(e)
        return info

    m_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', raw_html, re.I)
    if not m_title:
        m_title = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', raw_html, re.I)
    if m_title:
        info["post_title"] = _html.unescape(m_title.group(1)).strip()

    m_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', raw_html, re.I)
    if not m_desc:
        m_desc = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', raw_html, re.I)
    desc_txt = _html.unescape(m_desc.group(1)).strip() if m_desc else ""
    info["post_text"] = desc_txt

    # Extract recruiter emails from post body
    for em in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", desc_txt):
        em_l = em.lower().rstrip(".,;)")
        if not any(bad in em_l for bad in ["example.com", "linkedin.com"]):
            if em_l not in info["emails"]:
                info["emails"].append(em_l)

    # Extract all URLs from post description and resolve lnkd.in redirects
    cand_links = re.findall(r"https?://[^\s\"'<>)\]]+", desc_txt)
    for cl in cand_links:
        cl = cl.rstrip(".,;)")
        if "lnkd.in/" in cl.lower():
            try:
                r2 = urllib.request.Request(cl, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(r2, timeout=12) as resp2:
                    resolved = resp2.geturl()
                    if "linkedin.com/in/" not in resolved.lower() and "linkedin.com/company/" not in resolved.lower():
                        if resolved not in info["target_urls"]:
                            info["target_urls"].append(resolved)
            except Exception as he:
                # Some servers reject urllib default UA or return 403 after redirect; capture redirect URL if present
                if hasattr(he, "url") and he.url and "linkedin.com/in/" not in he.url.lower():
                    if he.url not in info["target_urls"]:
                        info["target_urls"].append(he.url)
                else:
                    try:
                        r3 = urllib.request.Request(cl, headers=headers)
                        with urllib.request.urlopen(r3, timeout=12) as resp3:
                            resolved3 = resp3.geturl()
                            if "linkedin.com/in/" not in resolved3.lower() and resolved3 not in info["target_urls"]:
                                info["target_urls"].append(resolved3)
                    except Exception as he2:
                        if hasattr(he2, "url") and he2.url and "linkedin.com/in/" not in he2.url.lower():
                            if he2.url not in info["target_urls"]:
                                info["target_urls"].append(he2.url)
        elif "linkedin.com/in/" not in cl.lower() and "linkedin.com/company/" not in cl.lower():
            if cl not in info["target_urls"]:
                info["target_urls"].append(cl)

    return info


def _record_job_attempt(url: str, title: str, company: str, status: str, ats_type: str = "", error_note: str = "") -> None:
    try:
        import hashlib
        jid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        sc = json.dumps({"ats_type": ats_type, "last_error": error_note, "updated_via": "priority_exec"})
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        conn.execute(
            """
            INSERT INTO jobs (id, title, company, location, url, match_score, status, scorecard)
            VALUES (?, ?, ?, 'India', ?, 90, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status, scorecard=excluded.scorecard
            """,
            (jid, title or "Product / Business Analyst", company or "Direct ATS", url, status, sc),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_priority_apply(args) -> str:
    url = args.url.strip().strip("<>")
    role_hint = args.role or "Product / Business Analyst"
    notes: List[str] = []

    # 1. Automatically unwrap lnkd.in/p/... or linkedin.com/posts/... on Cloud VM (zero Mac required!)
    if any(p in url.lower() for p in ["lnkd.in/", "linkedin.com/posts/", "linkedin.com/feed/update/"]):
        unwrapped = unwrap_linkedin_short_or_post_url(url)
        if unwrapped.get("post_title"):
            notes.append(f"🔎 **Unwrapped LinkedIn Post:** {unwrapped['post_title'][:110]}")
        # Send immediate cold email if post contains recruiter email!
        for rec_email in unwrapped.get("emails", [])[:2]:
            try:
                em_ns = argparse.Namespace(
                    to=rec_email, name="Hiring Team", company="", role=role_hint, subject="", body="", sender="", no_cv=False
                )
                notes.append(run_priority_email(em_ns))
            except Exception as ee:
                notes.append(f"⚠️ Email attempt to `{rec_email}`: `{ee}`")
        ext_targets = unwrapped.get("target_urls", [])
        if ext_targets:
            url = ext_targets[0]
            notes.append(f"🔗 **Extracted Direct Apply Link:** {url}")
        elif unwrapped.get("emails"):
            return "\n\n".join(notes)
        else:
            return (
                "\n".join(notes)
                + f"\n⚠️ Could not find an external career URL or email inside `{url}`. "
                f"Post snippet: `{(unwrapped.get('post_text') or '')[:280]}`"
            ).strip()

    # 2. Pure LinkedIn Easy Apply / Job View URL -> Route to Mac Ridealong
    if "linkedin.com/jobs" in url.lower():
        out = dispatch_and_wait_mac("linkedin_apply", {"url": url}, channel_id=args.channel, timeout_s=45.0)
        return ("\n\n".join(notes + [out])).strip()

    # 3. Google Forms (forms.gle / docs.google.com/forms)
    if "forms.gle" in url.lower() or "docs.google.com/forms" in url.lower():
        from src.services.google_form_filler import fill_google_form
        gf_res = fill_google_form(url, role_title=role_hint, auto_submit=True)
        if gf_res.get("submitted"):
            _record_job_attempt(url, role_hint, "Google Form", "applied", "google_form")
            return "\n\n".join(notes + [f"✅ **Google Form Application Submitted Immediately!**\n• **URL:** {url}"])
        _record_job_attempt(url, role_hint, "Google Form", "external_pending_review", "google_form", str(gf_res.get("error") or "unconfirmed"))
        return "\n\n".join(notes + [f"⚠️ **Google Form Not Confirmed (`{gf_res.get('error')}`):** {url}"])

    # 4. Direct Company ATS / Career Portal (Greenhouse, Lever, Ashby, Keka, Workday, iCIMS, etc.)
    # When Mac Ridealong Bridge is online, execute via Persistent Residential Chrome CDP first to avoid datacenter IP flags!
    if _is_mac_awake():
        mac_out = dispatch_and_wait_mac("ats_apply", {"url": url, "role": role_hint}, channel_id=args.channel, timeout_s=65.0)
        if sys.platform != "darwin":
            if mac_out.startswith("✅"):
                _record_job_attempt(url, role_hint, "ATS", "applied", "persistent_cdp")
            elif "Job Expired" in mac_out:
                _record_job_attempt(url, role_hint, "ATS", "expired", "persistent_cdp", "expired")
            elif not mac_out.startswith("⏳"):
                _record_job_attempt(url, role_hint, "ATS", "external_pending_review", "persistent_cdp", "gated")
        return ("\n\n".join(notes + [mac_out])).strip()

    from src.services.ats_filler import fill_ats_application
    res = fill_ats_application(url, role_title=role_hint, auto_submit=True)
    final_ats_url = res.get("ats_url") or url
    ats_type = res.get("ats_type") or "generic"
    err = res.get("error") or ""

    if res.get("submitted") and res.get("confirmed"):
        _record_job_attempt(final_ats_url, role_hint, ats_type.upper(), "applied", ats_type)
        try:
            from src.agents.discord_notifier import DiscordNotifier
            DiscordNotifier().send_application_alert(
                {
                    "company": ats_type.upper(),
                    "role": role_hint,
                    "platform": f"Priority #0 Direct ATS ({ats_type.upper()})",
                    "url": final_ats_url,
                    "status": "SUBMITTED & CONFIRMED",
                    "details": f"Filled {len(res.get('fields_filled', []))} fields + uploaded Vishal_Gupta_Resume.pdf.",
                    "resume": "Vishal_Gupta_Resume.pdf",
                }
            )
        except Exception:
            pass
        return "\n\n".join(
            notes
            + [
                f"✅ **Application Submitted & Confirmed Immediately!**\n"
                f"• **ATS:** `{ats_type}`\n"
                f"• **URL:** {final_ats_url}\n"
                f"• **Fields Filled:** `{len(res.get('fields_filled', []))}` | **Resume Uploaded:** `{res.get('resume_uploaded')}`"
            ]
        )

    # Honest reporting when gated by CAPTCHA or human-verification code (NEVER fake 'applied'!)
    _record_job_attempt(final_ats_url, role_hint, ats_type.upper(), "external_pending_review", ats_type, err)
    if err == "human_verification_code_required_in_email":
        return "\n\n".join(
            notes
            + [
                f"🛡️ **Form Filled & Resume Uploaded — Human Email Code Required to Finish (`NOT` marked applied yet):**\n"
                f"• **Portal:** `{ats_type}` ({final_ats_url})\n"
                f"• **What happened:** Filled `{len(res.get('fields_filled', []))}` fields + uploaded `Vishal_Gupta_Resume.pdf`, and `{ats_type}` sent an 8-character human-verification code (`confirm you're a human`) to `vishal90301@gmail.com`.\n"
                f"• **Next step:** Open the link (or Ridealong tab if on Mac) and enter the 8-char code from Gmail to complete submission."
            ]
        )
    if err == "captcha_gate_requires_manual_apply":
        return "\n\n".join(
            notes
            + [
                f"🛡️ **CAPTCHA Gate Detected (`NOT` marked applied — 100% honest status):**\n"
                f"• **Portal:** `{ats_type}`\n"
                f"• **Direct Application Link:** {final_ats_url}\n"
                f"• **Why:** This portal enforces an interactive CAPTCHA challenge before submission, which automated tools strictly cannot bypass. Click the direct link above to submit in ~20 seconds."
            ]
        )

    return "\n\n".join(
        notes
        + [
            f"⚠️ **Application Attempted — Not Confirmed (`NOT` marked applied):**\n"
            f"• **Portal:** `{ats_type}` ({final_ats_url})\n"
            f"• **Reason:** `{err or 'unconfirmed_after_submit'}`\n"
            f"• **Fields Filled:** `{len(res.get('fields_filled', []))}` | **Resume Uploaded:** `{res.get('resume_uploaded')}`"
        ]
    )


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    li = sub.add_parser("linkedin-msg")
    li.add_argument("--query", required=True)
    li.add_argument("--name", default="")
    li.add_argument("--company", default="")
    li.add_argument("--role", default="")
    li.add_argument("--custom-message", default="")
    li.add_argument("--attach-cv", action="store_true")
    li.add_argument("--channel", default="1570342549435187290")

    em = sub.add_parser("email")
    em.add_argument("--to", required=True)
    em.add_argument("--name", default="")
    em.add_argument("--company", default="")
    em.add_argument("--role", default="")
    em.add_argument("--subject", default="")
    em.add_argument("--body", default="")
    em.add_argument("--sender", default="")
    em.add_argument("--no-cv", action="store_true")

    ap = sub.add_parser("apply")
    ap.add_argument("--url", required=True)
    ap.add_argument("--role", default="Product Analyst")
    ap.add_argument("--channel", default="1570342549435187290")

    sh = sub.add_parser("mac-sh")
    sh.add_argument("--cmd", required=True)
    sh.add_argument("--channel", default="1570342549435187290")

    args = parser.parse_args()
    if args.cmd == "linkedin-msg":
        out = dispatch_and_wait_mac(
            "linkedin_msg",
            {
                "query": args.query,
                "name": args.name,
                "company": args.company,
                "role": args.role,
                "custom_message": args.custom_message,
                "attach_cv": args.attach_cv,
            },
            channel_id=args.channel,
            timeout_s=42.0,
        )
        print(out)
    elif args.cmd == "email":
        print(run_priority_email(args))
    elif args.cmd == "apply":
        print(run_priority_apply(args))
    elif args.cmd == "mac-sh":
        print(dispatch_and_wait_mac("mac_sh", {"cmd": args.cmd}, channel_id=args.channel, timeout_s=45.0))


if __name__ == "__main__":
    main()
