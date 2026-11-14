import os
import random
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.expanduser("~/.linkedin_agent.db")
RESUME_PDF = str(PROJECT_ROOT / "data" / "resume.pdf")
if not os.path.exists(RESUME_PDF) and os.path.exists("/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"):
    RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"

TARGET_KEYWORDS = [
    "Product Analyst",
    "Associate Product Manager",
    "Business Analyst",
    "Data Analyst",
    "Category Manager",
    "Operations Analyst",
    "Forward Deployed Engineer",
    "Founders Office",
]


def _fill_easy_apply_modal_step(page, modal) -> None:
    """Fills file inputs, text/number inputs, <select> dropdowns, and radios inside LinkedIn Easy Apply modal."""
    # 1. Upload resume if file input visible and not yet selected
    try:
        for f_inp in modal.locator('input[type="file"]').all():
            try:
                f_inp.set_input_files(RESUME_PDF)
                time.sleep(0.4)
            except Exception:
                pass
    except Exception:
        pass

    # 2. Fill text & numeric inputs
    for inp in modal.locator('input[type="text"], input[type="number"], input[type="tel"], input:not([type])').all():
        try:
            if not inp.is_visible():
                continue
            val = (inp.input_value() or "").strip()
            if val:
                continue
            lbl = (
                (inp.get_attribute("aria-label") or "")
                + " "
                + (inp.evaluate("el => (el.labels && el.labels[0] ? el.labels[0].innerText : el.parentElement?.innerText || '')") or "")
            ).lower()
            inp_type = (inp.get_attribute("type") or "").lower()
            if "phone" in lbl or "mobile" in lbl:
                inp.fill("8368387245")
            elif "expected" in lbl and ("ctc" in lbl or "salary" in lbl or "compensation" in lbl):
                inp.fill("16" if "lpa" in lbl or "lakhs" in lbl else "1600000")
            elif "current" in lbl and ("ctc" in lbl or "salary" in lbl or "compensation" in lbl):
                inp.fill("12" if "lpa" in lbl or "lakhs" in lbl else "1200000")
            elif "salary" in lbl or "ctc" in lbl or "desired" in lbl:
                inp.fill("15" if "lpa" in lbl else "1500000")
            elif "notice" in lbl or "how soon" in lbl or "days" in lbl:
                inp.fill("15")
            elif "gpa" in lbl or "cgpa" in lbl:
                inp.fill("7.05")
            elif "city" in lbl or "location" in lbl:
                inp.fill("Gurugram, Haryana, India")
                time.sleep(0.6)
                opt = page.locator('[role="option"]').first
                if opt.count() > 0 and opt.is_visible():
                    opt.click()
            elif "linkedin" in lbl:
                inp.fill("https://www.linkedin.com/in/vshal-g007/")
            elif "experience" in lbl or "year" in lbl or inp_type == "number":
                inp.fill("2" if ("sql" in lbl or "python" in lbl or "excel" in lbl) else "1")
            else:
                inp.fill("1")
        except Exception:
            pass

    # 3. Handle <select> dropdowns inside modal
    for sel in modal.locator("select").all():
        try:
            if not sel.is_visible():
                continue
            cur_v = (sel.input_value() or "").strip().lower()
            if cur_v and "select" not in cur_v:
                continue
            s_lbl = (sel.evaluate("el => (el.labels && el.labels[0] ? el.labels[0].innerText : el.parentElement?.innerText || '')") or "").lower()
            opts = sel.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
            valid_opts = [o for o in opts if o and "select an option" not in o.lower() and o != "--"]
            if not valid_opts:
                continue
            pick = None
            if any(k in s_lbl for k in ["sponsor", "visa"]):
                pick = next((o for o in valid_opts if o.lower().startswith("no")), None)
            elif any(k in s_lbl for k in ["authoriz", "relocat", "comfortable", "bachelor", "degree", "commut", "onsite", "hybrid", "immediate"]):
                pick = next((o for o in valid_opts if o.lower().startswith("yes")), None)
            elif "country" in s_lbl or "phone" in s_lbl:
                pick = next((o for o in valid_opts if "india" in o.lower() or "+91" in o), None)
            if not pick:
                pick = next((o for o in valid_opts if o.lower() == "yes"), valid_opts[0])
            sel.select_option(label=pick)
        except Exception:
            pass

    # 4. Handle radio fieldsets & required checkboxes
    try:
        page.evaluate(
            """() => {
                const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
                if (!modal) return;
                modal.querySelectorAll('fieldset').forEach(fs => {
                    const legend = (fs.innerText || '').toLowerCase();
                    const radios = Array.from(fs.querySelectorAll('input[type="radio"]'));
                    if (!radios.length || radios.some(r => r.checked)) return;
                    let target = (legend.includes('sponsor') || legend.includes('visa') || legend.includes('relatives')) ? 'no' : 'yes';
                    let chosen = radios.find(r => ((r.labels && r.labels[0]?.innerText) || r.parentElement?.innerText || '').toLowerCase().includes(target)) || radios[0];
                    if (chosen) { chosen.click(); chosen.dispatchEvent(new Event('change', {bubbles: true})); }
                });
                modal.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                    const txt = ((cb.labels && cb.labels[0]?.innerText) || cb.parentElement?.innerText || '').toLowerCase();
                    if (!cb.checked && (cb.required || txt.includes('terms') || txt.includes('consent') || txt.includes('acknowledge') || txt.includes('follow') === false)) {
                        if (!txt.includes('follow ')) {
                            cb.click();
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    }
                });
            }"""
        )
    except Exception:
        pass


def _try_apply_on_loaded_linkedin_job(page, context, port: str, jid: str, title: str, comp: str, job_url: str, conn) -> bool:
    """
    Attempts Easy Apply OR unwraps External Apply into persistent Ridealong ATS filler!
    Returns True ONLY if genuinely submitted and confirmed.
    """
    from src.services.exclusion_filter import FORBIDDEN_COMPANIES
    from src.services.ats_filler import fill_ats_application
    from src.agents.discord_notifier import DiscordNotifier

    if any(fc in f"{title} {comp} {job_url}".lower() for fc in FORBIDDEN_COMPANIES):
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (jid,))
        conn.commit()
        return False

    page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2.8)

    body_txt = (page.locator("body").inner_text() or "").lower()
    if "no longer accepting applications" in body_txt or "application submitted" in body_txt or "applied " in body_txt[:600]:
        if "no longer accepting applications" in body_txt:
            conn.execute("UPDATE jobs SET status='expired' WHERE id=?", (jid,))
            conn.commit()
            return False

    # Case A: LinkedIn Easy Apply button
    easy_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button[aria-label*="Easy Apply"]').first
    if easy_btn.count() > 0 and easy_btn.is_visible():
        easy_btn.click()
        time.sleep(2.0)
        modal = page.locator('div.jobs-easy-apply-modal, dialog[open], div[role="dialog"]').first
        if not modal.is_visible(timeout=3500):
            return False

        for _ in range(9):
            time.sleep(1.2)
            _fill_easy_apply_modal_step(page, modal)
            submit_btn = modal.locator('button[aria-label*="Submit application"], button:has-text("Submit application")').first
            if submit_btn.count() > 0 and submit_btn.is_visible():
                submit_btn.click()
                time.sleep(3.2)
                post_lower = (page.locator("body").inner_text() or "").lower()
                has_err = modal.locator('.artdeco-inline-feedback--error:visible').count() > 0
                if not has_err and any(p in post_lower for p in ["application sent", "your application was sent", "application submitted"]):
                    conn.execute(
                        """
                        INSERT INTO jobs (id, title, company, location, url, match_score, scorecard, status)
                        VALUES (?, ?, ?, 'India', ?, 90, json_object('applied_via', 'linkedin_easy_apply'), 'applied')
                        ON CONFLICT(id) DO UPDATE SET status='applied', scorecard=json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'linkedin_easy_apply')
                        """,
                        (jid, title, comp, job_url),
                    )
                    conn.commit()
                    try:
                        DiscordNotifier().send_application_alert({
                            "company": comp,
                            "role": title,
                            "platform": "LinkedIn Easy Apply (Verified)",
                            "url": job_url,
                            "status": "SUBMITTED & CONFIRMED",
                            "details": "Verified 'Application sent' confirmation modal + uploaded Vishal_Gupta_Resume.pdf.",
                            "resume": "Vishal_Gupta_Resume.pdf",
                        })
                    except Exception:
                        pass
                    done_btn = page.locator('button:has-text("Done"), button[aria-label="Dismiss"]').first
                    if done_btn.count() > 0 and done_btn.is_visible():
                        try:
                            done_btn.click()
                        except Exception:
                            pass
                    return True
                break

            rev_btn = modal.locator('button[aria-label*="Review"], button:has-text("Review")').first
            nxt_btn = modal.locator('button[aria-label*="Continue to next step"], button:has-text("Next")').first
            if rev_btn.count() > 0 and rev_btn.is_visible():
                rev_btn.click()
            elif nxt_btn.count() > 0 and nxt_btn.is_visible():
                nxt_btn.click()
            else:
                break

        # Close incomplete modal cleanly
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
            disc_btn = page.locator('button[data-control-name="discard_application_confirm_btn"], button:has-text("Discard")').first
            if disc_btn.count() > 0 and disc_btn.is_visible():
                disc_btn.click()
        except Exception:
            pass
        conn.execute("UPDATE jobs SET status='external_pending_review' WHERE id=?", (jid,))
        conn.commit()
        return False

    # Case B: LinkedIn External Apply button -> Unwrap & auto-fill via Persistent Chrome CDP!
    ext_btn = page.locator('button.jobs-apply-button, a.jobs-apply-button, button[aria-label*="Apply"]').first
    if ext_btn.count() > 0 and ext_btn.is_visible():
        before_pages = set(context.pages)
        try:
            ext_btn.click(timeout=3500)
            time.sleep(3.5)
        except Exception:
            pass
        after_pages = [pg for pg in context.pages if pg not in before_pages]
        ext_page = after_pages[0] if after_pages else None
        ext_url = ext_page.url if ext_page else (page.url if "linkedin.com" not in page.url else "")
        if ext_page:
            try:
                ext_page.close()
            except Exception:
                pass
        if ext_url and ext_url.startswith("http") and "linkedin.com" not in ext_url:
            print(f"[LinkedInJobs] Unwrapped External ATS URL -> {ext_url}")
            ats_res = fill_ats_application(ext_url, role_title=f"{title} @ {comp}", auto_submit=True, existing_page=page, leave_open=True)
            if ats_res.get("submitted") and ats_res.get("confirmed"):
                conn.execute(
                    "UPDATE jobs SET status='applied', url=?, scorecard=json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'linkedin_unwrapped_cdp') WHERE id=?",
                    (ats_res.get("ats_url") or ext_url, jid),
                )
                conn.commit()
                try:
                    DiscordNotifier().send_application_alert({
                        "company": comp,
                        "role": title,
                        "platform": f"LinkedIn -> {ats_res.get('ats_type', 'ATS').upper()} (Persistent CDP)",
                        "url": ats_res.get("ats_url") or ext_url,
                        "status": "SUBMITTED & CONFIRMED",
                        "details": f"Unwrapped external ATS from LinkedIn and confirmed submission ({len(ats_res.get('fields_filled', []))} fields)!",
                        "resume": "Vishal_Gupta_Resume.pdf",
                    })
                except Exception:
                    pass
                return True
            else:
                conn.execute(
                    "UPDATE jobs SET status='external_pending_review', url=?, scorecard=json_set(COALESCE(scorecard, '{}'), '$.last_error', ?) WHERE id=?",
                    (ext_url, str(ats_res.get("error") or "unconfirmed")[:120], jid),
                )
                conn.commit()
                return False

    conn.execute("UPDATE jobs SET status='external_pending_review' WHERE id=?", (jid,))
    conn.commit()
    return False


def _pull_fresh_jobs_from_cloud_vm(conn) -> None:
    """Pulls fresh 'new' LinkedIn jobs and unattempted Greenhouse/Lever/Ashby jobs from Cloud VM DB into Mac DB."""
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if not os.path.exists(ssh_key):
        return
    try:
        import json, subprocess
        remote_py = (
            "import json, sqlite3; "
            "c = sqlite3.connect('/home/opc/.linkedin_agent.db', timeout=15.0).cursor(); "
            "rows = c.execute(\"\"\""
            "SELECT id, title, company, COALESCE(location, 'India'), url, COALESCE(match_score, 85), status, COALESCE(scorecard, '{}') "
            "FROM jobs "
            "WHERE (status = 'new' AND url LIKE '%linkedin.com/jobs/view/%') "
            "   OR (status IN ('new', 'external_pending_review') "
            "       AND (url LIKE '%greenhouse.io%' OR url LIKE '%lever.co%' OR url LIKE '%ashbyhq.com%') "
            "       AND COALESCE(scorecard, '') NOT LIKE '%cdp_attempted%' "
            "       AND COALESCE(scorecard, '') NOT LIKE '%human_verification%' "
            "       AND COALESCE(scorecard, '') NOT LIKE '%captcha_gate%') "
            "ORDER BY COALESCE(match_score, 85) DESC, created_at DESC LIMIT 60"
            "\"\"\").fetchall(); "
            "print(json.dumps(rows))"
        )
        proc = subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "opc@130.210.108.55", f"/home/opc/career-agent/venv/bin/python3 -c \"{remote_py}\""],
            capture_output=True,
            text=True,
            timeout=15,
        )
        raw = (proc.stdout or "").strip()
        if raw.startswith("["):
            rows = json.loads(raw)
            conn.executemany(
                "INSERT OR IGNORE INTO jobs (id, title, company, location, url, match_score, status, scorecard) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
    except Exception as e:
        print(f"[LinkedInJobs] Cloud pull notice: {e}")


def _sync_job_statuses_to_cloud_vm(conn) -> None:
    """Syncs confirmed 'applied', 'expired', and attempted job rows from Mac DB to Cloud VM DB so Live Dashboard stays 100% accurate."""
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if not os.path.exists(ssh_key):
        return
    try:
        import base64, json, subprocess
        rows = conn.execute(
            """
            SELECT id, title, company, COALESCE(location, 'India'), url, COALESCE(match_score, 90), status, COALESCE(scorecard, '{}')
            FROM jobs
            WHERE status IN ('applied', 'expired')
               OR (status = 'external_pending_review' AND COALESCE(scorecard, '') LIKE '%cdp_attempted%')
            ORDER BY created_at DESC
            LIMIT 150
            """
        ).fetchall()
        if not rows:
            return
        b64 = base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
        remote_py = (
            "import base64, json, sqlite3; "
            f"rows = json.loads(base64.b64decode('{b64}').decode()); "
            "conn = sqlite3.connect('/home/opc/.linkedin_agent.db', timeout=20.0); "
            "c = conn.cursor(); "
            "[c.execute(\"UPDATE jobs SET status=?, scorecard=? WHERE url=? OR id=?\", (r[6], r[7], r[4], r[0])) for r in rows]; "
            "[c.execute(\"INSERT OR IGNORE INTO jobs (id, title, company, location, url, match_score, status, scorecard) VALUES (?,?,?,?,?,?,?,?)\", r) for r in rows if r[6] == 'applied']; "
            "conn.commit(); conn.close()"
        )
        subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "opc@130.210.108.55", f"/home/opc/career-agent/venv/bin/python3 -c \"{remote_py}\""],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        print(f"[LinkedInJobs] Cloud DB sync notice: {e}")


def _acquire_cdp_page(browser, port: str = ""):
    """Opens a clean isolated tab directly on Chrome's native DevTools WebSocket (zero proxy crash, zero race)."""
    ctx = browser.contexts[0]
    return ctx.new_page(), ctx


def apply_linkedin_jobs_stage(duration_minutes: int = 10, max_apps: int = 8) -> int:
    """
    Applies to queued Dashboard LinkedIn jobs (both Easy Apply AND External ATS redirects)
    + fresh LinkedIn Easy Apply searches using Ridealong's direct Chrome CDP (zero new_page crash!).
    """
    from scripts.send_linkedin_person_msg import get_direct_cdp_endpoint, DEVTOOLS_PORT_FILE
    from src.services.linkedin_control import sync_control_with_cloud, is_linkedin_logged_out, set_linkedin_enabled

    print("\n" + "=" * 70)
    print(f"💼 [LinkedInJobs] Starting ~{duration_minutes}-minute Persistent CDP Job Application Stage (max {max_apps})")
    print("=" * 70 + "\n")

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    _pull_fresh_jobs_from_cloud_vm(conn)
    cur = conn.cursor()
    end_time = time.time() + (duration_minutes * 60)
    applied_count = 0

    port = DEVTOOLS_PORT_FILE.read_text(encoding="utf-8").splitlines()[0].strip()
    cdp_ep = get_direct_cdp_endpoint()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_ep, timeout=12000)
        page, context = _acquire_cdp_page(browser, port)
        try:
            # Phase 0: Drain unattempted direct Greenhouse / Lever / Ashby jobs via Persistent Chrome CDP!
            from src.services.ats_filler import fill_ats_application
            from src.agents.discord_notifier import DiscordNotifier
            ats_queued = cur.execute(
                """
                SELECT id, title, company, url
                FROM jobs
                WHERE status IN ('new', 'redirect_pending', 'external_pending_review')
                  AND (url LIKE '%greenhouse.io%' OR url LIKE '%lever.co%' OR url LIKE '%ashbyhq.com%')
                  AND COALESCE(scorecard, '') NOT LIKE '%human_verification%'
                  AND COALESCE(scorecard, '') NOT LIKE '%captcha_gate%'
                  AND COALESCE(scorecard, '') NOT LIKE '%cdp_attempted%'
                ORDER BY COALESCE(match_score, 85) DESC, created_at DESC
                LIMIT 6
                """
            ).fetchall()
            for jid, title, comp, aurl in ats_queued:
                if time.time() >= end_time or applied_count >= max_apps:
                    break
                res = fill_ats_application(aurl, role_title=f"{title} @ {comp}", auto_submit=True, existing_page=page, leave_open=True)
                if res.get("submitted") and res.get("confirmed"):
                    conn.execute(
                        "UPDATE jobs SET status='applied', scorecard=json_set(COALESCE(scorecard, '{}'), '$.applied_via', 'persistent_cdp') WHERE id=?",
                        (jid,),
                    )
                    conn.commit()
                    applied_count += 1
                    try:
                        DiscordNotifier().send_application_alert({
                            "company": comp,
                            "role": title,
                            "platform": f"Persistent Chrome CDP ({res.get('ats_type', 'ATS').upper()})",
                            "url": res.get("ats_url") or aurl,
                            "status": "SUBMITTED & CONFIRMED",
                            "details": f"Filled {len(res.get('fields_filled', []))} fields + uploaded Vishal_Gupta_Resume.pdf via Ridealong CDP!",
                            "resume": "Vishal_Gupta_Resume.pdf",
                        })
                    except Exception:
                        pass
                elif res.get("error") == "expired":
                    conn.execute("UPDATE jobs SET status='expired' WHERE id=?", (jid,))
                    conn.commit()
                else:
                    conn.execute(
                        "UPDATE jobs SET status='external_pending_review', scorecard=json_set(COALESCE(scorecard, '{}'), '$.last_error', ?, '$.cdp_attempted', 1) WHERE id=?",
                        (str(res.get("error") or "unconfirmed")[:100], jid),
                    )
                    conn.commit()

            # Phase 1: Drain queued 'new' LinkedIn jobs from Dashboard DB first!
            queued = cur.execute(
                """
                SELECT id, title, company, url
                FROM jobs
                WHERE status = 'new' AND url LIKE '%linkedin.com/jobs/view/%'
                ORDER BY COALESCE(match_score, 80) DESC, created_at DESC
                LIMIT 25
                """
            ).fetchall()
            print(f"[LinkedInJobs] Processing {len(queued)} queued Dashboard LinkedIn jobs...")

            for jid, title, comp, jurl in queued:
                if time.time() >= end_time or applied_count >= max_apps:
                    break
                if not sync_control_with_cloud().get("linkedin_enabled", True):
                    break
                try:
                    ok = _try_apply_on_loaded_linkedin_job(page, context, port, jid, title, comp, jurl, conn)
                    if ok:
                        applied_count += 1
                        print(f"[LinkedInJobs] ✅ Confirmed #{applied_count}: {title} @ {comp}")
                        time.sleep(random.uniform(18, 32))
                    else:
                        time.sleep(random.uniform(3, 6))
                except Exception as qe:
                    print(f"[LinkedInJobs] Queued job note ({title}): {qe}")

            # Phase 2: Fresh Easy Apply search if time & quota remain
            kw_idx = 0
            while time.time() < end_time and applied_count < max_apps:
                if not sync_control_with_cloud().get("linkedin_enabled", True):
                    break
                kw = TARGET_KEYWORDS[kw_idx % len(TARGET_KEYWORDS)]
                kw_idx += 1
                s_url = f"https://www.linkedin.com/jobs/search/?keywords={urllib.parse.quote(kw)}&f_AL=true&f_TPR=r86400&location=India"
                page.goto(s_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3.0)
                if is_linkedin_logged_out(page):
                    set_linkedin_enabled(False, reason="linkedin_logged_out", updated_by="linkedin_jobs")
                    break
                links = page.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href*="/jobs/view/"]')).slice(0, 8).map(a => ({
                        url: a.href.split('?')[0],
                        title: (a.innerText || '').trim().split('\\n')[0] || 'Product Role'
                    }))"""
                )
                for item in links:
                    if time.time() >= end_time or applied_count >= max_apps:
                        break
                    lu = item["url"]
                    if cur.execute("SELECT 1 FROM jobs WHERE url LIKE ? AND status='applied'", (f"%{lu[-12:]}%",)).fetchone():
                        continue
                    fid = f"li_{lu.rstrip('/').split('-')[-1][:16]}"
                    if _try_apply_on_loaded_linkedin_job(page, context, port, fid, item["title"], "LinkedIn", lu, conn):
                        applied_count += 1
                        time.sleep(random.uniform(18, 32))
        finally:
            try:
                page.close()
            except Exception:
                pass

    _sync_job_statuses_to_cloud_vm(conn)
    conn.close()
    print(f"\n[LinkedInJobs] Stage complete! Submitted & confirmed {applied_count} applications.")
    return applied_count


if __name__ == "__main__":
    apply_linkedin_jobs_stage(duration_minutes=12, max_apps=8)
