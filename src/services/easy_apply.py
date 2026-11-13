import json
import os
import random
import time
import re
from pathlib import Path
from typing import Dict, Any, List
from playwright.sync_api import sync_playwright
from .form_answerer import answer_standard_field, answer_open_ended_question, load_candidate_profile
from ..db import database

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/easy-apply"


def _resolve_resume_path(profile: Dict[str, Any]) -> str:
    candidates = [
        os.environ.get("RESUME_PATH"),
        str(Path.home() / "career-agent" / "Vishal_Gupta_Resume.pdf"),
        str(Path.home() / "career-agent" / "data" / "resume.pdf"),
        str(PROJECT_ROOT / "Vishal_Gupta_Resume.pdf"),
        str(PROJECT_ROOT / "data" / "resume.pdf"),
        profile.get("personal", {}).get("resume_local_pdf_path"),
    ]
    return next((c for c in candidates if c and os.path.exists(c)), str(PROJECT_ROOT / "data" / "resume.pdf"))


def _resolve_cookie_file() -> str:
    candidates = [
        os.environ.get("LINKEDIN_COOKIES_PATH"),
        str(PROJECT_ROOT / "data" / "linkedin_cookies.json"),
        str(Path.home() / ".linkedin_cookies.json"),
    ]
    return next((c for c in candidates if c and os.path.exists(c)), str(PROJECT_ROOT / "data" / "linkedin_cookies.json"))


def apply_to_easy_apply_job(job_url: str, role_title: str = "Associate Product Manager", auto_submit: bool = False) -> Dict[str, Any]:
    """
    Automates LinkedIn Easy Apply modal for a single job opening (1-task-at-a-time).
    Connects to Ridealong if running locally, or falls back to pre-authenticated session cookies
    in headless Playwright on the Cloud VM. Strictly verifies post-submit receipt + screenshot.
    """
    result = {
        "url": job_url,
        "is_easy_apply": False,
        "submitted": False,
        "confirmed": False,
        "already_applied": False,
        "screenshot_path": None,
        "questions_handled": [],
        "error": None
    }

    print(f"[EasyApply] Opening job: {job_url}")
    data_dir = PROJECT_ROOT / "data"
    os.makedirs(data_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        page = None
        is_local_headless = False
        try:
            try:
                browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=3000)
                context = browser.contexts[0]
            except Exception:
                cookie_file = _resolve_cookie_file()
                if not os.path.exists(cookie_file):
                    result["error"] = "no_authenticated_linkedin_session"
                    return result
                browser = p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 820}
                )
                with open(cookie_file, "r", encoding="utf-8") as cf:
                    cookies = json.load(cf)
                context.add_cookies(cookies)
                is_local_headless = True

            page = context.new_page()

            page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2.5, 4.2))
            try:
                page.mouse.move(random.randint(250, 650), random.randint(200, 480))
            except Exception:
                pass

            if any(k in page.url.lower() for k in ["linkedin.com/login", "authwall", "checkpoint"]):
                result["error"] = "linkedin_session_expired_or_login_wall"
                return result

            # Check if already applied on LinkedIn
            already_badge = page.locator(
                'span.artdeco-inline-feedback__message:has-text("Applied"), '
                'li-icon[type="success-pebble-icon"] + span:has-text("Applied")'
            ).first
            if already_badge.is_visible(timeout=1200):
                result["already_applied"] = True
                result["error"] = "already_applied_on_linkedin"
                return result

            # Close any open messaging popups that might block clicks or intercept modal selectors
            for cb in page.locator('aside button:has-text("Close"), button[aria-label*="Close conversation"]').all():
                try:
                    cb.click()
                except:
                    pass

            # Check for Easy Apply button
            easy_apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button:has-text("Easy Apply")').first
            if not easy_apply_btn.is_visible(timeout=4000):
                print("[EasyApply] Not an Easy Apply role (redirects to external company career page).")
                result["is_easy_apply"] = False
                return result

            result["is_easy_apply"] = True
            easy_apply_btn.scroll_into_view_if_needed()
            easy_apply_btn.click(force=True)
            time.sleep(2)

            modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]:has-text("Apply to"), dialog').first
            if not modal.is_visible(timeout=5000):
                result["error"] = "Modal failed to open"
                return result

            profile = load_candidate_profile()

            # Modal wizard loop (up to 12 steps for multi-page questionnaires like Fam)
            for step in range(12):
                time.sleep(1.5)

                # 1. Fill phone number if visible and empty
                phone_inputs = modal.locator('input[id*="phoneNumber"], input[type="tel"]').all()
                for phone_input in phone_inputs:
                    try:
                        if phone_input.is_visible():
                            val = phone_input.input_value()
                            if not val or len(val) < 5:
                                phone_input.fill(profile.get("personal", {}).get("phone", "+918368387245"))
                    except:
                        pass

                # 1b. Handle resume upload / selection step
                file_input = modal.locator('input[type="file"]').first
                try:
                    if file_input.is_visible(timeout=300):
                        pdf_path = _resolve_resume_path(profile)
                        file_input.set_input_files(pdf_path)
                        time.sleep(1)
                except Exception as fe:
                    pass

                # 1c. Handle custom radio options (e.g. 1-3 years experience on Fam)
                try:
                    exp_1_3 = modal.locator('p:has-text("1-3")').first
                    if exp_1_3.count() > 0 and exp_1_3.is_visible():
                        exp_1_3.click()
                        time.sleep(0.5)
                except:
                    pass

                # 2. Handle Custom Questions on current step (supports 2026 redesign aria-labels and labels)
                interactive_inputs = modal.locator('input:not([type="hidden"]):not([type="file"]):not([type="radio"]), textarea, select').all()
                for inp in interactive_inputs:
                    try:
                        if not inp.is_visible():
                            continue

                        tag = inp.evaluate('el => el.tagName.toLowerCase()')
                        aria_lbl = inp.get_attribute('aria-label') or inp.get_attribute('placeholder') or ''

                        # Also search ancestor or sibling label if aria-label is empty
                        if not aria_lbl:
                            lbl_match = inp.locator('xpath=ancestor::*[contains(@class, "form") or contains(@class, "group") or @role="group"]//label').first
                            if lbl_match.count() > 0:
                                aria_lbl = lbl_match.inner_text().strip()

                        clean_label = re.sub(r"[\*\n]+", " ", aria_lbl).strip()
                        if not clean_label:
                            continue

                        # Text or Textarea input
                        if tag in ['input', 'textarea']:
                            cur_val = inp.input_value()
                            if not cur_val:
                                ans = answer_standard_field(clean_label) or answer_open_ended_question(clean_label, role_title)
                                if ans:
                                    inp.fill(str(ans))
                                    result["questions_handled"].append({"question": clean_label, "answer": ans})

                        # Select dropdown
                        elif tag == 'select':
                            options = [o.inner_text().strip() for o in inp.locator('option').all() if o.inner_text().strip()]
                            matched_opt = answer_standard_field(clean_label, options=options)
                            if matched_opt:
                                inp.select_option(label=matched_opt)
                                result["questions_handled"].append({"question": clean_label, "answer": matched_opt})

                    except Exception as ie:
                        pass

                # Check general radio groups
                try:
                    radios = modal.locator('input[type="radio"]').all()
                    for r in radios:
                        if r.is_checked():
                            continue
                        r_label = r.locator('xpath=ancestor::label | ..').inner_text().strip()
                        clean_r = re.sub(r"[\*\n]+", " ", r_label).strip()
                        matched_radio = answer_standard_field(clean_r)
                        if matched_radio and (matched_radio.lower() in clean_r.lower() or clean_r.lower() in ["yes", "15 days"]):
                            r.locator('xpath=ancestor::label | ..').first.click()
                except:
                    pass

                # 3. Check for Next, Review, or Submit
                submit_btn = modal.locator('button:has-text("Submit application"), button:has-text("Submit")').first
                review_btn = modal.locator('button:has-text("Review")').first
                next_btn = modal.locator('button:has-text("Next")').first

                if submit_btn.is_visible(timeout=800):
                    if auto_submit:
                        # Uncheck "Follow <Company>" if visible so feed stays clean
                        try:
                            follow_cb = modal.locator('label[for*="follow-company-checkbox"]').first
                            if follow_cb.is_visible(timeout=400):
                                inp_cb = modal.locator('input[id*="follow-company-checkbox"]').first
                                if inp_cb.count() > 0 and inp_cb.is_checked():
                                    follow_cb.click()
                        except Exception:
                            pass

                        submit_btn.click()
                        time.sleep(3.5)
                        result["submitted"] = True

                        # Verify explicit LinkedIn confirmation banner/modal
                        shot_path = str(data_dir / f"linkedin_apply_{int(time.time())}.png")
                        try:
                            page.screenshot(path=shot_path)
                            result["screenshot_path"] = shot_path
                        except Exception:
                            pass

                        body_lower = page.locator("body").inner_text().lower()
                        has_validation_err = modal.locator('.artdeco-inline-feedback--error:visible').count() > 0
                        if not has_validation_err and any(
                            phrase in body_lower
                            for phrase in [
                                "application sent",
                                "your application was sent",
                                "application submitted",
                            ]
                        ):
                            result["confirmed"] = True
                            print(f"[EasyApply] Application SUBMITTED & CONFIRMED! Screenshot: {shot_path}")
                            try:
                                from ..agents.discord_notifier import DiscordNotifier
                                DiscordNotifier().send_application_alert({
                                    "company": "LinkedIn Verified Apply",
                                    "role": role_title,
                                    "platform": "LinkedIn (Easy Apply — Verified)",
                                    "url": job_url,
                                    "status": "SUBMITTED & CONFIRMED",
                                    "details": f"Verified 'Application sent' screen. Questions answered: {len(result['questions_handled'])}. Screenshot saved.",
                                    "resume": "Vishal_Gupta_Resume.pdf"
                                })
                            except Exception as ne:
                                print(f"[EasyApply] Alert error: {ne}")
                        else:
                            result["confirmed"] = False
                            result["error"] = "unconfirmed_or_validation_error_on_submit"
                            print(f"[EasyApply] Submit clicked, but 'Application sent' NOT confirmed (err_visible={has_validation_err}).")
                    else:
                        shot_path = str(data_dir / f"linkedin_preview_{int(time.time())}.png")
                        try:
                            page.screenshot(path=shot_path)
                            result["screenshot_path"] = shot_path
                        except Exception:
                            pass
                        print(f"[EasyApply] Reached final submission step (auto_submit=False). Preview: {shot_path}")
                    break
                elif review_btn.is_visible(timeout=800):
                    review_btn.click()
                elif next_btn.is_visible(timeout=800):
                    next_btn.click()
                else:
                    # Check if stuck on validation error on intermediate step
                    if modal.locator('.artdeco-inline-feedback--error:visible').count() > 0:
                        result["error"] = "unanswered_custom_validation_field"
                    break

        except Exception as e:
            print(f"[EasyApply] Error during application: {e}")
            result["error"] = str(e)
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            if browser and is_local_headless:
                try:
                    browser.close()
                except Exception:
                    pass

    return result

def run_easy_apply_batch(job_urls: List[Dict[str, str]], batch_size: int = 3, auto_submit: bool = True) -> List[Dict[str, Any]]:
    """
    Applies to a batch of Easy Apply jobs with polite intervals between runs
    to prevent anti-bot detection or LinkedIn account flags.
    """
    results = []
    selected_batch = job_urls[:batch_size]
    print(f"[EasyApplyBatch] Starting batch of {len(selected_batch)} applications (Auto-Submit={auto_submit})...")

    for i, job in enumerate(selected_batch):
        url = job.get("url")
        title = job.get("title", "Associate Product Manager")
        res = apply_to_easy_apply_job(url, role_title=title, auto_submit=auto_submit)
        results.append(res)

        # Log event in SQLite
        database.log_event("AUTO_APPLY", "INFO", f"Applied to {title}", {
            "url": url,
            "submitted": res["submitted"],
            "is_easy_apply": res["is_easy_apply"]
        })

        if i < len(selected_batch) - 1:
            # Polite human-like delay between applications (30–45s)
            delay = 35
            print(f"[EasyApplyBatch] Waiting {delay}s before next application to maintain human rate profile...")
            time.sleep(delay)

    return results
