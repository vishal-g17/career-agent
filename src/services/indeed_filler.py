"""
Indeed Native Discovery & SmartApply Automation (Local Ridealong Session).

Runs strictly on Local Mac via Ridealong (`http://127.0.0.1:9230/default/indeed-bot`)
using the user's authenticated Indeed session:
  • Never attempts to solve, click, or wait through any Cloudflare / CAPTCHA / Turnstile gate;
    stops cleanly if any human-verification or login wall appears.
  • Never marks external company-site redirects as 'applied' unless `fill_ats_application`
    independently confirms submission receipt on the destination ATS.
  • Never leaks restricted past employer names anywhere in answers or relevant-experience fields.
"""

import os
import re
import socket
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from .exclusion_filter import FORBIDDEN_COMPANIES
from .form_answerer import answer_open_ended_question, answer_standard_field
from .naukri_filler import determine_radio_choice, determine_text_answer

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESUME_PATH = str(PROJECT_ROOT / "data" / "resume.pdf")
RIDEALONG_INDEED_CDP = "http://127.0.0.1:9230/default/indeed-bot"


def _has_ridealong() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        with socket.create_connection(("127.0.0.1", 9230), timeout=0.6):
            return True
    except OSError:
        return False


def _is_cloudflare_or_captcha_gate(page: Page) -> bool:
    """Strictly detects Cloudflare Turnstile / anti-bot / CAPTCHA gates without interacting."""
    try:
        title_l = (page.title() or "").lower()
        if "just a moment" in title_l or "additional verification" in title_l or "attention required" in title_l:
            return True
        return bool(
            page.evaluate(
                """() => {
                    const txt = ((document.body && document.body.innerText) || '').toLowerCase();
                    if (
                        txt.includes('additional verification required') ||
                        txt.includes('verify you are human') ||
                        txt.includes('checking if the site connection is secure') ||
                        document.querySelector('#challenge-running, #cf-challenge-running')
                    ) {
                        return true;
                    }
                    for (const f of Array.from(document.querySelectorAll('iframe'))) {
                        const src = (f.getAttribute('src') || '').toLowerCase();
                        const r = f.getBoundingClientRect();
                        if (r.width > 30 && r.height > 30 && (
                            src.includes('challenges.cloudflare.com') ||
                            src.includes('turnstile') ||
                            src.includes('hcaptcha.com') ||
                            (src.includes('recaptcha') && !src.includes('size=invisible'))
                        )) {
                            return true;
                        }
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _is_indeed_logged_out(page: Page) -> bool:
    try:
        u = (page.url or "").lower()
        if "secure.indeed.com/auth" in u or "/account/login" in u:
            return True
        return False
    except Exception:
        return False


def _scrub_forbidden(text: str) -> str:
    out = re.sub(r"(?i)magicpin", "Quick-Commerce Platform", str(text))
    out = re.sub(r"(?i)lifelong", "Consumer D2C Brand", out)
    out = re.sub(r"(?i)\bnom\b", "Early-Stage Tech Venture", out)
    return out


def scrape_fresh_indeed_jobs(keywords: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Scrapes fresh (<3 days) Indeed 'Easily apply' jobs in India via Local Ridealong session.
    Stops immediately if Cloudflare challenge or login wall appears.
    """
    from ..agents.job_hunter import generate_dedup_id

    if not _has_ridealong():
        return []

    search_queries = keywords or [
        "Product Analyst",
        "Business Analyst",
        "Associate Product Manager",
        "Data Analyst",
        "Category Manager",
        "Operations Analyst",
        "Founders Office",
    ]

    discovered: List[Dict[str, Any]] = []
    seen_jks = set()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_INDEED_CDP, timeout=8000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            for kw in search_queries:
                q_enc = urllib.parse.quote_plus(kw)
                # sc=0kf:attr(DSQF7); filters to Indeed Easily Apply roles; fromage=3 keeps listings fresh
                search_url = f"https://in.indeed.com/jobs?q={q_enc}&l=India&fromage=3&sc=0kf%3Aattr%28DSQF7%29%3B"
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=22000)
                    page.wait_for_timeout(2600)
                except Exception as ge:
                    print(f"[IndeedScrape] Navigation notice for '{kw}': {ge}")
                    continue

                if _is_cloudflare_or_captcha_gate(page):
                    print("[IndeedScrape] Cloudflare/anti-bot gate detected on Indeed; stopping scrape cleanly.")
                    break
                if _is_indeed_logged_out(page):
                    print("[IndeedScrape] Indeed session requires sign-in; stopping scrape cleanly.")
                    break

                cards = page.evaluate(
                    """() => {
                        return Array.from(document.querySelectorAll('div.job_seen_beacon')).map(c => {
                            const a = c.querySelector('a.jcs-JobTitle, a[data-jk], h2 a');
                            return {
                                title: (a?.innerText || '').trim(),
                                jk: a?.getAttribute('data-jk') || '',
                                company: (c.querySelector('[data-testid="company-name"]')?.innerText || '').trim(),
                                location: (c.querySelector('[data-testid="text-location"]')?.innerText || 'India').trim(),
                                easyApply: c.innerText.includes('Easily apply')
                            };
                        });
                    }"""
                )
                for c in cards or []:
                    jk = (c.get("jk") or "").strip()
                    title = (c.get("title") or "").strip()
                    company = (c.get("company") or "").strip()
                    loc = (c.get("location") or "India").strip()
                    if not jk or not title or not company or jk in seen_jks:
                        continue
                    # Keep strictly Indeed native 'Easily apply' cards for high-conversion verified applies
                    if not c.get("easyApply"):
                        continue
                    blob_l = f"{title} {company}".lower()
                    if any(
                        (re.search(rf"\b{re.escape(fc)}\b", blob_l) if len(fc) <= 4 else (fc in blob_l))
                        for fc in FORBIDDEN_COMPANIES
                    ):
                        continue
                    seen_jks.add(jk)
                    job_url = f"https://in.indeed.com/viewjob?jk={jk}"
                    discovered.append(
                        {
                            "dedup_id": generate_dedup_id(title, company, loc),
                            "job_id": f"indeed_{jk}",
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": job_url,
                            "posted_time": "Recent (<3d)",
                            "platform": "Indeed",
                            "keyword_matched": kw,
                        }
                    )
                time.sleep(1.2)
        finally:
            try:
                if len(ctx.pages) > 1:
                    page.close()
            except Exception:
                pass

    return discovered


def _fill_smartapply_step(page: Page, role_title: str, answered_log: List[Dict[str, str]]) -> None:
    """Fills visible fields on the current Indeed SmartApply wizard step."""
    # 1. Resume selection or upload (NEVER click 'Select file' button; Vishal_Gupta_Resume.pdf is pre-uploaded)
    try:
        already_checked = page.locator('input[type="radio"]:checked, [role="radio"][aria-checked="true"]').count() > 0
        if not already_checked:
            resume_card = page.locator(
                '[data-testid="FileResumeCardHeader-title"], '
                '[data-testid="ResumeSelectionCard"], '
                'label:has-text("Vishal"), div[role="radio"]:has-text("Vishal")'
            ).first
            if resume_card.count() > 0 and resume_card.is_visible():
                resume_card.click(timeout=1500)
                page.wait_for_timeout(400)
            else:
                fi = page.locator('input[type="file"]').first
                if fi.count() > 0 and os.path.exists(RESUME_PATH):
                    fi.set_input_files(RESUME_PATH)
                    page.wait_for_timeout(2000)
    except Exception:
        pass

    # 2. Radio button groups
    radio_names = set()
    for r in page.locator('input[type="radio"]').all():
        try:
            nm = r.get_attribute("name")
            if nm:
                radio_names.add(nm)
        except Exception:
            pass

    for nm in radio_names:
        try:
            radios = page.locator(f'input[type="radio"][name="{nm}"]').all()
            if not radios or any(r.is_checked() for r in radios):
                continue
            q_txt = (
                radios[0].evaluate(
                    """el => {
                        const fs = el.closest('fieldset, [role="radiogroup"], .ia-Questions-item, div[id*="question"]');
                        if (fs) {
                            const lg = fs.querySelector('legend, label, [class*="question" i], span');
                            return (lg ? lg.innerText : fs.innerText).trim().slice(0, 220);
                        }
                        return '';
                    }"""
                )
                or role_title
            )
            opts = []
            for r in radios:
                lbl = (
                    r.evaluate(
                        """el => {
                            if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
                            const p = el.closest('label');
                            return p ? p.innerText.trim() : (el.value || '');
                        }"""
                    )
                    or ""
                ).strip()
                if lbl:
                    opts.append((lbl, r))
            if opts:
                choice = determine_radio_choice(q_txt, [o[0] for o in opts])
                target = next((r for lbl, r in opts if lbl == choice), opts[0][1])
                target.check(force=True)
                answered_log.append({"q": q_txt[:60], "a": choice[:40]})
                page.wait_for_timeout(250)
        except Exception:
            continue

    # 3. Select dropdowns
    for sel in page.locator("select").all():
        try:
            if not sel.is_visible():
                continue
            cur_val = sel.input_value() or ""
            all_opts: List[str] = sel.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
            valid_opts = [o for o in all_opts if o and not o.lower().startswith(("select", "choose", "--"))]
            if not valid_opts:
                continue
            # Skip if already populated with a real option
            selected_txt = sel.evaluate("el => el.options[el.selectedIndex]?.text?.trim() || ''")
            if cur_val and selected_txt in valid_opts:
                continue
            q_txt = (
                sel.evaluate(
                    """el => {
                        const w = el.closest('.ia-Questions-item, .form-group, div');
                        return (w ? w.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 200);
                    }"""
                )
                or role_title
            )
            if "country" in q_txt.lower():
                pick = next((o for o in valid_opts if "india" in o.lower()), valid_opts[0])
            else:
                pick = determine_radio_choice(q_txt, valid_opts)
            sel.select_option(label=pick)
            answered_log.append({"q": q_txt[:60], "a": pick[:40]})
            page.wait_for_timeout(200)
        except Exception:
            continue

    # 4. Text / Number / Tel / Email inputs
    for inp in page.locator('input[type="text"], input[type="number"], input[type="tel"], input[type="email"]').all():
        try:
            if not inp.is_visible() or not inp.is_enabled():
                continue
            cur_v = (inp.input_value() or "").strip()
            # Scrub any auto-populated forbidden employer name on Relevant Experience step!
            if cur_v and any(fc in cur_v.lower() for fc in ("magicpin", "lifelong")):
                inp.fill(_scrub_forbidden(cur_v))
                continue
            if cur_v:
                continue

            q_txt = (
                inp.evaluate(
                    """el => {
                        const parts = [];
                        if (el.labels && el.labels[0]) parts.push(el.labels[0].innerText);
                        if (el.placeholder) parts.push(el.placeholder);
                        if (el.name) parts.push(el.name);
                        const w = el.closest('.ia-Questions-item, [data-testid*="input"], div');
                        if (w) {
                            const lbl = w.querySelector('label, span');
                            if (lbl) parts.push(lbl.innerText);
                        }
                        return parts.join(' ').replace(/\\s+/g, ' ').trim().slice(0, 200);
                    }"""
                )
                or ""
            )
            ql = q_txt.lower()
            if not ql or "search" in ql:
                continue

            if "job title" in ql:
                ans = "Product Analyst"
            elif "company" in ql:
                ans = "Quick-Commerce Platform"
            elif any(k in ql for k in ["first name", "firstname"]):
                ans = "Vishal"
            elif any(k in ql for k in ["last name", "lastname"]):
                ans = "Gupta"
            elif "phone" in ql or "mobile" in ql:
                ans = "8368387245"
            elif "city" in ql or "location" in ql:
                ans = "New Delhi, Delhi"
            elif inp.get_attribute("type") == "number":
                if "expected" in ql and any(k in ql for k in ["ctc", "salary", "pay"]):
                    ans = "1500000" if "year" in ql or "annual" in ql or "inr" in ql else "15"
                elif any(k in ql for k in ["ctc", "salary", "pay"]):
                    ans = "1000000" if "year" in ql or "annual" in ql or "inr" in ql else "10"
                elif any(k in ql for k in ["notice", "days"]):
                    ans = "15"
                else:
                    ans = "1"
            else:
                ans = _scrub_forbidden(determine_text_answer(q_txt))

            inp.fill(str(ans))
            answered_log.append({"q": q_txt[:60], "a": str(ans)[:40]})
            page.wait_for_timeout(200)
        except Exception:
            continue

    # 5. Textareas
    for ta in page.locator("textarea").all():
        try:
            if not ta.is_visible() or (ta.input_value() or "").strip():
                continue
            q_txt = (
                ta.evaluate(
                    """el => {
                        if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
                        const w = el.closest('.ia-Questions-item, div');
                        return (w ? w.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 200);
                    }"""
                )
                or role_title
            )
            ans = _scrub_forbidden(answer_open_ended_question(q_txt, role_title=role_title))
            ta.fill(ans[:350])
            answered_log.append({"q": q_txt[:60], "a": ans[:40]})
            page.wait_for_timeout(200)
        except Exception:
            continue

    # 6. Required checkboxes (privacy/terms/consent or multi-select questions)
    for chk in page.locator('input[type="checkbox"]').all():
        try:
            if not chk.is_visible() or chk.is_checked():
                continue
            c_txt = (
                chk.evaluate(
                    """el => {
                        const p = el.closest('label, div');
                        return (p ? p.innerText : '').toLowerCase();
                    }"""
                )
                or ""
            )
            if chk.get_attribute("required") is not None or any(
                w in c_txt for w in ["agree", "consent", "terms", "privacy", "acknowledge", "authorize"]
            ):
                chk.check(force=True)
        except Exception:
            continue


def apply_to_indeed_job(job_url: str, role_title: str = "Product Analyst") -> Dict[str, Any]:
    """
    Applies to a single Indeed job using Local Ridealong session.
    Strictly verifies SmartApply submission receipt before returning applied=True.
    """
    result: Dict[str, Any] = {
        "url": job_url,
        "title": role_title,
        "applied": False,
        "status": "failed",
        "questions_answered": [],
        "error": None,
    }

    if not _has_ridealong():
        result["status"] = "ridealong_unavailable"
        return result

    external_target_url: Optional[str] = None

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_INDEED_CDP, timeout=8000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        active_apply_page = page
        try:
            page.goto(job_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2800)

            if _is_cloudflare_or_captcha_gate(page):
                result["status"] = "captcha_gate"
                return result
            if _is_indeed_logged_out(page):
                result["status"] = "needs_login"
                return result

            body_l = (page.locator("body").inner_text() or "").lower()
            if "this job has expired on indeed" in body_l or "job you are looking for is no longer open" in body_l:
                result["status"] = "expired"
                return result

            if "you've applied to this job" in body_l or page.locator('button:disabled:has-text("Applied")').count() > 0:
                result["applied"] = True
                result["status"] = "already_applied"
                return result

            ia_btn = page.locator(
                '[data-testid="viewjob-indeed-apply"], button#indeedApplyButton, '
                'a[href*="smartapply.indeed.com"], button:has-text("Apply now")'
            ).first
            ext_btn = page.locator(
                'a:has-text("Apply on company site"), button:has-text("Apply on company site")'
            ).first

            if not ia_btn.is_visible() and ext_btn.is_visible():
                href = ext_btn.get_attribute("href")
                if href:
                    external_target_url = "https://in.indeed.com" + href if href.startswith("/") else href
                result["status"] = "external_redirect"
            elif not ia_btn.is_visible():
                result["status"] = "no_indeed_apply_button"
                return result
            else:
                ia_href = ia_btn.get_attribute("href")
                if ia_href and "smartapply.indeed.com" in ia_href:
                    page.goto(ia_href, wait_until="domcontentloaded", timeout=28000)
                    page.wait_for_timeout(3500)
                else:
                    pages_before = len(ctx.pages)
                    ia_btn.click()
                    page.wait_for_timeout(3500)
                    if len(ctx.pages) > pages_before:
                        active_apply_page = ctx.pages[-1]
                        active_apply_page.wait_for_load_state("domcontentloaded")
                        active_apply_page.wait_for_timeout(2000)

                # Step through SmartApply wizard (up to 18 steps; wait through 'Preparing review' spinner!)
                idle_waits = 0
                for step_idx in range(18):
                    if _is_cloudflare_or_captcha_gate(active_apply_page):
                        print("[IndeedApply] Anti-bot/CAPTCHA gate encountered inside SmartApply; stopping cleanly.")
                        result["status"] = "captcha_gate"
                        return result
                    if _is_indeed_logged_out(active_apply_page):
                        result["status"] = "needs_login"
                        return result

                    cur_u = (active_apply_page.url or "").lower()
                    try:
                        cur_txt = (active_apply_page.locator("body").inner_text() or "").lower()
                    except Exception:
                        cur_txt = ""

                    if (
                        "/post-apply" in cur_u
                        or "your application has been submitted" in cur_txt
                        or "application submitted" in cur_txt
                        or "you have applied" in cur_txt
                        or "we've sent your application" in cur_txt
                    ):
                        result["applied"] = True
                        result["status"] = "applied"
                        return result

                    # Wait patiently if Indeed SmartApply is rendering 'Preparing review' / 'Loading'
                    if "preparing review" in cur_txt or "loading" in cur_txt:
                        active_apply_page.wait_for_timeout(3000)
                        try:
                            cur_txt = (active_apply_page.locator("body").inner_text() or "").lower()
                        except Exception:
                            cur_txt = ""

                    _fill_smartapply_step(active_apply_page, role_title, result["questions_answered"])

                    def _pick_real_smartapply_btn(selector: str):
                        for cand in active_apply_page.locator(selector).all():
                            try:
                                tid = (cand.get_attribute("data-testid") or "").lower()
                                if tid.startswith("hp-"):
                                    continue
                                if not cand.is_visible() or not cand.is_enabled():
                                    continue
                                box = cand.bounding_box()
                                if not box or box.get("width", 0) < 24 or box.get("height", 0) < 20:
                                    continue
                                return cand
                            except Exception:
                                continue
                        return None

                    # Check for final Submit button first (strictly ignoring hp-* trap buttons)
                    submit_btn = _pick_real_smartapply_btn(
                        'button[data-testid="submit-application-button"], '
                        'button:not([data-testid^="hp-"]):has-text("Submit your application"), '
                        'button:not([data-testid^="hp-"]):has-text("Submit application"), '
                        'button[data-testid*="submit-application"]:not([data-testid^="hp-"])'
                    )
                    if submit_btn is not None:
                        idle_waits = 0
                        submit_btn.scroll_into_view_if_needed(timeout=2000)
                        submit_btn.click()
                        for _ in range(4):
                            active_apply_page.wait_for_timeout(3000)
                            if _is_cloudflare_or_captcha_gate(active_apply_page):
                                result["status"] = "captcha_gate"
                                return result
                            post_u = (active_apply_page.url or "").lower()
                            post_txt = (active_apply_page.locator("body").inner_text() or "").lower()
                            if (
                                "/post-apply" in post_u
                                or "your application has been submitted" in post_txt
                                or "application submitted" in post_txt
                                or "you have applied" in post_txt
                                or "we've sent your application" in post_txt
                            ):
                                result["applied"] = True
                                result["status"] = "applied"
                                return result

                    # Otherwise click real Continue / Review your application button (never hp-continue-button-*)
                    cont_btn = _pick_real_smartapply_btn(
                        'button[data-testid="continue-button"], '
                        'button:not([data-testid^="hp-"]):has-text("Review your application"), '
                        'button:not([data-testid^="hp-"]):has-text("Continue"), '
                        'button[data-testid*="continue"]:not([data-testid^="hp-"])'
                    )
                    if cont_btn is not None:
                        idle_waits = 0
                        prev_u = active_apply_page.url
                        cont_btn.scroll_into_view_if_needed(timeout=2000)
                        cont_btn.click()
                        active_apply_page.wait_for_timeout(3200)
                        apply_anyway = _pick_real_smartapply_btn(
                            'button:not([data-testid^="hp-"]):has-text("Apply anyway"), '
                            'button:not([data-testid^="hp-"]):has-text("Continue applying")'
                        )
                        if apply_anyway is not None:
                            apply_anyway.click()
                            active_apply_page.wait_for_timeout(2500)
                        if active_apply_page.url == prev_u and step_idx >= 14:
                            break
                    else:
                        idle_waits += 1
                        active_apply_page.wait_for_timeout(2800)
                        if idle_waits >= 4:
                            break
        except Exception as e:
            result["error"] = str(e)
        finally:
            if active_apply_page is not page:
                try:
                    active_apply_page.close()
                except Exception:
                    pass
            try:
                if len(ctx.pages) > 1:
                    page.close()
            except Exception:
                pass

    # If external redirect was extracted AND matches a supported zero-login ATS, run ATS filler outside Ridealong context cleanly
    supported_ats_tokens = (
        "greenhouse.io", "gh_jid=", "lever.co", "ashbyhq.com", "pyjamahr.com",
        "keka.com", "bamboohr.com", "smartrecruiters.com", "forms.gle", "docs.google.com/forms",
    )
    if external_target_url and any(tok in external_target_url.lower() for tok in supported_ats_tokens):
        try:
            from .ats_filler import fill_ats_application
            ats_res = fill_ats_application(external_target_url, role_title=role_title, auto_submit=True)
            if ats_res.get("submitted") and ats_res.get("confirmed"):
                result["applied"] = True
                result["status"] = "external_applied"
                result["external_url"] = ats_res.get("ats_url", external_target_url)
                return result
        except Exception as ae:
            result["error"] = str(ae)
        result["applied"] = False
        result["status"] = "external_redirect"

    return result
