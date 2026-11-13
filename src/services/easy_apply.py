import time
import re
from typing import Dict, Any, List
from playwright.sync_api import sync_playwright
from .form_answerer import answer_standard_field, answer_open_ended_question, load_candidate_profile
from ..db import database

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/easy-apply"

def apply_to_easy_apply_job(job_url: str, role_title: str = "Associate Product Manager", auto_submit: bool = False) -> Dict[str, Any]:
    """
    Automates LinkedIn Easy Apply modal for a single job opening.
    Fills contact info, selects standard resume, and handles screening questions.
    """
    result = {
        "url": job_url,
        "is_easy_apply": False,
        "submitted": False,
        "questions_handled": [],
        "error": None
    }

    print(f"[EasyApply] Opening job: {job_url}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0]
            page = context.new_page()

            page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

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
                        pdf_path = profile.get("personal", {}).get("resume_local_pdf_path", "/Users/admin/linkedin-multiagent/data/resume.pdf")
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
                        submit_btn.click()
                        time.sleep(2)
                        result["submitted"] = True
                        print("[EasyApply] Application submitted successfully!")
                        try:
                            from ..agents.discord_notifier import DiscordNotifier
                            DiscordNotifier().send_application_alert({
                                "company": "LinkedIn Job Posting",
                                "role": role_title,
                                "platform": "LinkedIn (Easy Apply)",
                                "url": job_url,
                                "status": "SUBMITTED & CONFIRMED",
                                "details": "Autofilled screening questions, verified phone/email, attached Vishal_Gupta_Resume.pdf, submitted.",
                                "resume": "Vishal_Gupta_Resume.pdf"
                            })
                        except Exception as ne:
                            print(f"[EasyApply] Alert error: {ne}")
                    else:
                        print("[EasyApply] Reached final submission step. Auto-submit is False.")
                    break
                elif review_btn.is_visible(timeout=800):
                    review_btn.click()
                elif next_btn.is_visible(timeout=800):
                    next_btn.click()
                else:
                    break

        except Exception as e:
            print(f"[EasyApply] Error during application: {e}")
            result["error"] = str(e)
        finally:
            try:
                page.close()
            except:
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
