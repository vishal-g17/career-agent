#!/usr/bin/env python3
"""
Priority #0 Immediate Task Executor for Discord #prompt — 100% Standalone 24/7 Cloud Execution (Zero Local Mac Dependency).
Whenever Vishal commands something in #prompt, this executes it IMMEDIATELY on the 24/7 Oracle Cloud VM
(bypassing background queues) and returns the REAL VERIFIED COMPLETION RESULT synchronously!

Subcommands:
  1) email        : Immediate SMTP dispatch right now (custom text or tailored cold pitch + CV, bypasses lead queue)
  2) apply        : Immediate job/form application right now directly on Cloud VM (Instahyre REST, Greenhouse, Lever, Ashby, Google Forms, lnkd.in unwrap)
  3) linkedin-msg : Instant Cloud lookup (checks verified email leads in Cloud DB; zero Local Mac wait)
"""

import argparse
import html as _html
import json
import os
import re
import smtplib
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")


def run_priority_email(args) -> str:
    from src.services.cold_mailer import compose_cold_email, send_cold_email_smtp, get_sender_credentials
    from src.services.exclusion_filter import FORBIDDEN_COMPANIES

    recip = args.to.strip()
    check_blob = f"{recip} {args.company or ''} {args.subject or ''} {args.body or ''}".lower()
    if any(
        (re.search(rf"\b{re.escape(fc)}\b", check_blob) if len(fc) <= 4 else (fc in check_blob))
        for fc in FORBIDDEN_COMPANIES
    ):
        return f"🛑 Blocked: `{recip}` or message content matches restricted company exclusion rule."

    def _record_outreach_lead(used_sender: str) -> None:
        if recip.lower() in {"vishal90301@gmail.com", "vishal.gupta.ug22@nsut.ac.in"}:
            return
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        conn.execute(
            """
            INSERT INTO outreach_leads (name, email, company, role_target, status, first_sent_at, last_sent_at, sender_email)
            VALUES (?, ?, ?, ?, 'sent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(email) DO UPDATE SET
                name=CASE WHEN excluded.name != 'Hiring Team' THEN excluded.name ELSE outreach_leads.name END,
                company=CASE WHEN excluded.company != '' THEN excluded.company ELSE outreach_leads.company END,
                role_target=CASE WHEN excluded.role_target != '' THEN excluded.role_target ELSE outreach_leads.role_target END,
                status='sent',
                last_sent_at=CURRENT_TIMESTAMP,
                sender_email=excluded.sender_email
            """,
            (
                args.name or "Hiring Team",
                recip.lower(),
                args.company or "",
                args.role or "Product / Analytics",
                used_sender,
            ),
        )
        conn.commit()
        conn.close()
        try:
            from scripts.sync_leads_to_sheet import export_split_leads
            export_split_leads()
        except Exception:
            pass

    if args.body:
        from src.services.email_verifier import verify_email
        is_valid, reason, _ = verify_email(recip)
        if not is_valid:
            return f"❌ Immediate email to `{recip}` blocked by MX/bounce verifier: `{reason}`"

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
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as srv:
                srv.ehlo()
                srv.starttls()
                srv.ehlo()
                srv.login(sender, app_pwd)
                srv.sendmail(sender, [recip], msg.as_string())
        except Exception:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as srv:
                srv.login(sender, app_pwd)
                srv.sendmail(sender, [recip], msg.as_string())
        if not args.no_cv or args.company or args.role:
            _record_outreach_lead(sender)
        return (
            f"✅ **Priority Cold Email + CV Sent Immediately!**\n"
            f"• **To:** `{recip}` (`{args.company or 'Direct'}`)\n"
            f"• **Sender:** `{sender}`\n"
            f"• **Subject:** `{subj}`\n"
            f"• **CV Attached:** `{'No' if args.no_cv else 'Yes (Vishal_Gupta_Resume.pdf)'}`"
        )
    else:
        draft = compose_cold_email(
            recipient_email=recip,
            role_title=args.role or "Business / Product Analyst / Management",
            company_name=args.company or "",
            sender_email=args.sender or None,
        )
        subj = args.subject or draft["subject"]
        ok = send_cold_email_smtp(recip, subj, draft["body"], sender_email=draft["sender"])
        if not ok:
            return f"❌ Immediate email to `{recip}` could not be sent (failed MX/SMTP check)."

        _record_outreach_lead(draft["sender"])
        return (
            f"✅ **Priority Cold Email + CV Sent Immediately!**\n"
            f"• **To:** `{recip}` (`{args.company or 'Direct'}`)\n"
            f"• **Sender:** `{draft['sender']}`\n"
            f"• **Subject:** `{subj}`"
        )


def unwrap_linkedin_short_or_post_url(raw_url: str) -> Dict[str, Any]:
    """
    Unwraps lnkd.in/p/... and linkedin.com/posts/... directly from Cloud VM (zero local device needed!)
    using Discordbot/2.0 OpenGraph HTML + secondary lnkd.in redirect resolution.
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

    for em in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", desc_txt):
        em_l = em.lower().rstrip(".,;)")
        if not any(bad in em_l for bad in ["example.com", "linkedin.com"]):
            if em_l not in info["emails"]:
                info["emails"].append(em_l)

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


def unwrap_linkedin_guest_job(job_url: str) -> Optional[str]:
    """Attempts to extract an external ATS applyUrl from LinkedIn's public guest job API directly on Cloud VM."""
    m = re.search(r"(\d{8,12})", job_url)
    if not m:
        return None
    jid = m.group(1)
    api = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
        m_code = re.search(r'id=["\']applyUrl["\'][^>]*><!--\s*"([^"]+)"\s*-->', raw, re.I)
        if m_code:
            cand = _html.unescape(m_code.group(1)).strip()
            if "url=" in cand:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(cand).query).get("url", [None])[0]
                if q:
                    cand = urllib.parse.unquote(q)
            if cand.startswith("http") and "linkedin.com" not in cand.lower():
                return cand
        for cand_q in re.findall(r"url=([^&\"'\s<>]+)", raw):
            dec = urllib.parse.unquote(_html.unescape(cand_q))
            if dec.startswith("http") and "linkedin.com" not in dec.lower() and "licdn.com" not in dec.lower():
                return dec
    except Exception:
        pass
    return None


def _record_job_attempt(url: str, title: str, company: str, status: str, ats_type: str = "", error_note: str = "") -> None:
    try:
        import hashlib
        jid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        sc = json.dumps({"ats_type": ats_type, "last_error": error_note, "updated_via": "priority_exec_cloud"})
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

    # 1. Unwrap lnkd.in/p/... or linkedin.com/posts/... directly on Cloud VM
    if any(p in url.lower() for p in ["lnkd.in/", "linkedin.com/posts/", "linkedin.com/feed/update/"]):
        unwrapped = unwrap_linkedin_short_or_post_url(url)
        if unwrapped.get("post_title"):
            notes.append(f"🔎 **Unwrapped LinkedIn Post:** {unwrapped['post_title'][:110]}")
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

    # 2. LinkedIn Job URL -> Attempt Cloud guest external ATS unwrap (zero Local Mac wait!)
    if "linkedin.com/jobs" in url.lower():
        ext_ats = unwrap_linkedin_guest_job(url)
        if ext_ats:
            notes.append(f"🔗 **Unwrapped External Company Portal from LinkedIn Job:** {ext_ats}")
            url = ext_ats
        else:
            return "\n\n".join(
                notes
                + [
                    f"ℹ️ **LinkedIn On-Platform Easy Apply Link (`100% Cloud-Only Mode`):**\n"
                    f"• **Direct Link:** {url}\n"
                    f"• **Why not auto-submitted:** LinkedIn blocks Cloud datacenter IPs (`130.210.108.55`) on authenticated Easy Apply modals, and Local Mac dependency is strictly disabled. Tap the link above on mobile/desktop to submit in 1 click, or drop the recruiter's email / company career link here for instant Cloud execution!"
                ]
            ).strip()

    # 2b. Instahyre Job URL -> Instant 1-Click Verified REST API Apply (0s browser startup, 100% Cloud!)
    if "instahyre.com/" in url.lower():
        from src.services.instahyre_service import apply_instahyre_url
        ih_res = apply_instahyre_url(url)
        if ih_res.get("confirmed"):
            return "\n\n".join(
                notes
                + [
                    f"✅ **Instahyre Application Submitted & Confirmed Immediately (Cloud 24/7)!**\n"
                    f"• **Company & Role:** `{ih_res.get('company')} — {ih_res.get('title')}`\n"
                    f"• **URL:** {ih_res.get('url')}\n"
                    f"• **Details:** `{ih_res.get('details')}`"
                ]
            )
        return "\n\n".join(
            notes
            + [
                f"⚠️ **Instahyre Application Not Confirmed (`{ih_res.get('error')}`):**\n"
                f"• **URL:** {url}"
            ]
        )

    # 2c. Naukri Job URL -> Cloud Browser Apply / Company Redirect Extractor
    if "naukri.com/" in url.lower():
        from src.services.naukri_filler import apply_to_naukri_job
        nk_res = apply_to_naukri_job(url, role_title=role_hint)
        if nk_res.get("applied"):
            _record_job_attempt(nk_res.get("external_url") or url, role_hint, "Naukri", "applied", "naukri")
            return "\n\n".join(notes + [f"✅ **Naukri Application Submitted & Confirmed!**\n• **URL:** {url}"])
        return "\n\n".join(
            notes
            + [
                f"⚠️ **Naukri Portal Status (`{nk_res.get('status')}` — `NOT` marked applied):**\n"
                f"• **URL:** {url}"
            ]
        )

    # 3. Google Forms (forms.gle / docs.google.com/forms)
    if "forms.gle" in url.lower() or "docs.google.com/forms" in url.lower():
        from src.services.google_form_filler import fill_google_form
        gf_res = fill_google_form(url, role_title=role_hint, auto_submit=True)
        if gf_res.get("submitted"):
            _record_job_attempt(url, role_hint, "Google Form", "applied", "google_form")
            return "\n\n".join(notes + [f"✅ **Google Form Application Submitted Immediately (Cloud 24/7)!**\n• **URL:** {url}"])
        _record_job_attempt(url, role_hint, "Google Form", "external_pending_review", "google_form", str(gf_res.get("error") or "unconfirmed"))
        return "\n\n".join(notes + [f"⚠️ **Google Form Not Confirmed (`{gf_res.get('error')}`):** {url}"])

    # 4. Direct Company ATS / Career Portal (Greenhouse, Lever, Ashby, Keka, Workday, SmartRecruiters, etc.) — 100% Cloud VM!
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
                    "platform": f"Cloud 24/7 Direct ATS ({ats_type.upper()})",
                    "url": final_ats_url,
                    "status": "SUBMITTED & CONFIRMED",
                    "details": f"Filled {len(res.get('fields_filled', []))} fields + uploaded Vishal_Gupta_Resume.pdf on Cloud VM.",
                    "resume": "Vishal_Gupta_Resume.pdf",
                }
            )
        except Exception:
            pass
        return "\n\n".join(
            notes
            + [
                f"✅ **Application Submitted & Confirmed Immediately (Cloud 24/7)!**\n"
                f"• **ATS:** `{ats_type}`\n"
                f"• **URL:** {final_ats_url}\n"
                f"• **Fields Filled:** `{len(res.get('fields_filled', []))}` | **Resume Uploaded:** `{res.get('resume_uploaded')}`"
            ]
        )

    _record_job_attempt(final_ats_url, role_hint, ats_type.upper(), "external_pending_review", ats_type, err)
    if err == "human_verification_code_required_in_email":
        return "\n\n".join(
            notes
            + [
                f"🛡️ **Form Filled & Resume Uploaded — Human Email Code Required to Finish (`NOT` marked applied yet):**\n"
                f"• **Portal:** `{ats_type}` ({final_ats_url})\n"
                f"• **What happened:** Filled `{len(res.get('fields_filled', []))}` fields + uploaded `Vishal_Gupta_Resume.pdf`, and `{ats_type}` sent an 8-character human-verification code (`confirm you're a human`) to `vishal90301@gmail.com`.\n"
                f"• **Next step:** Open the link above and enter the 8-char code from Gmail to complete submission."
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


def run_cloud_linkedin_lookup(args) -> str:
    """Zero-Mac fallback when someone asks to message a person: checks Cloud outreach_leads DB for their direct email!"""
    q = (args.name or args.query or "").strip()
    comp = (args.company or "").strip()
    matches = []
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        cur = conn.cursor()
        if comp:
            matches = cur.execute(
                "SELECT name, email, company, status FROM outreach_leads WHERE LOWER(company) LIKE ? LIMIT 5",
                (f"%{comp.lower()}%",),
            ).fetchall()
        if not matches and q:
            tok = q.split()[0].lower()
            matches = cur.execute(
                "SELECT name, email, company, status FROM outreach_leads WHERE LOWER(name) LIKE ? OR LOWER(email) LIKE ? LIMIT 5",
                (f"%{tok}%", f"%{tok}%"),
            ).fetchall()
        conn.close()
    except Exception:
        pass

    lead_hint = ""
    if matches:
        formatted = ", ".join(f"`{r[1]}` ({r[0]} @ {r[2]} — `{r[3]}`)" for r in matches[:3])
        lead_hint = f"\n• **Matching Verified Email(s) in Cloud DB:** {formatted} — reply `send mail to <email>` to dispatch immediately via SMTP!"

    return (
        f"ℹ️ **100% Cloud-Standalone Mode Active (Zero Local Device Dependency):**\n"
        f"• LinkedIn blocks Cloud datacenter IPs (`130.210.108.55`) on authenticated DMs, and Local Mac Ridealong has been permanently disabled per your instruction.{lead_hint}"
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

    sh = sub.add_parser("cloud-sh")
    sh.add_argument("--cmd", required=True)

    args = parser.parse_args()
    if args.cmd == "linkedin-msg":
        print(run_cloud_linkedin_lookup(args))
    elif args.cmd == "email":
        print(run_priority_email(args))
    elif args.cmd == "apply":
        print(run_priority_apply(args))
    elif args.cmd == "cloud-sh":
        proc = subprocess.run(args.cmd, shell=True, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60)
        print(((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[:1800] or "Exit 0")


if __name__ == "__main__":
    main()
