import time
import os
import re
from typing import Dict, Any, List
from playwright.sync_api import sync_playwright
from .form_answerer import answer_standard_field, answer_open_ended_question, load_candidate_profile
from ..db import database

from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
possible_resume_paths = [
    os.environ.get("RESUME_PATH"),
    str(Path.home() / "career-agent" / "Vishal_Gupta_Resume.pdf"),
    str(Path.home() / "career-agent" / "data" / "resume.pdf"),
    str(PROJECT_ROOT / "Vishal_Gupta_Resume.pdf"),
    str(PROJECT_ROOT / "data" / "resume.pdf"),
    "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf",
]
RESUME_PATH = next((cand for cand in possible_resume_paths if cand and os.path.exists(cand)), str(PROJECT_ROOT / "data" / "resume.pdf"))
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/naukri-apply-bot"

def determine_radio_choice(question_text: str, options: List[str]) -> str:
    """Intelligently picks the best radio option for candidate Vishal Gupta."""
    q_lower = question_text.lower()
    
    # 1. Experience
    if any(k in q_lower for k in ["experience", "years", "exp"]):
        for opt in options:
            if any(m in opt for m in ["1-3", "1 to 3", "2", "2-3", "2 to 3", "1-2", "1 to 2"]):
                return opt
        for opt in options:
            if "yes" in opt.lower():
                return opt

    # 2. Relocation / Location / Remote / Commute
    if any(k in q_lower for k in ["relocate", "residing", "location", "commute", "travel", "work from"]):
        for opt in options:
            if "yes" in opt.lower():
                return opt
        for opt in options:
            if any(loc in opt.lower() for loc in ["delhi", "ncr", "remote", "hybrid"]):
                return opt

    # 3. Notice Period
    if any(k in q_lower for k in ["notice", "joining", "how soon", "available"]):
        for opt in options:
            if any(np in opt.lower() for np in ["immediate", "15", "0-15", "15 days", "1 month", "< 30"]):
                return opt

    # 4. Employment / Job Type
    if any(k in q_lower for k in ["employment", "job type", "work type", "preferred employment"]):
        for opt in options:
            if any(et in opt.lower() for et in ["full time", "full-time", "permanent"]):
                return opt

    # 4. CTC / Salary
    if any(k in q_lower for k in ["ctc", "salary", "fixed"]):
        if "expected" in q_lower:
            for opt in options:
                if any(c in opt for c in ["15", "12-15", "15-18", "14-16"]):
                    return opt
        else: # Current
            for opt in options:
                if any(c in opt for c in ["10", "8-10", "10-12", "9-11"]):
                    return opt

    # 5. Yes / No default handling
    yes_opt = next((o for o in options if o.strip().lower() in ["yes", "yep", "true"]), None)
    no_opt = next((o for o in options if o.strip().lower() in ["no", "nope", "false"]), None)

    # Negative triggers (criminal, sponsorship needed, bond, gap)
    if any(k in q_lower for k in ["sponsorship", "visa", "criminal", "bond", "backlog", "gap"]):
        if no_opt:
            return no_opt

    # Positive triggers (skills, authorization, full time, degree, comfortable)
    if yes_opt:
        return yes_opt

    # If nothing matched, pick first non-skip option or first option
    valid_opts = [o for o in options if "skip" not in o.lower()]
    return valid_opts[0] if valid_opts else options[0]


def determine_text_answer(question_text: str) -> str:
    """Returns concise, high-fit answer for text input."""
    q_lower = question_text.lower()
    
    if any(k in q_lower for k in ["experience", "years", "exp"]):
        return "1"
    if any(k in q_lower for k in ["notice", "joining", "how soon", "days"]):
        return "15"
    if "expected" in q_lower and any(k in q_lower for k in ["ctc", "salary"]):
        return "15"
    if any(k in q_lower for k in ["current ctc", "fixed ctc", "current salary", "present ctc"]):
        return "10"
    if any(k in q_lower for k in ["marks", "percentage", "10th", "12th", "score"]):
        return "90"
    if any(k in q_lower for k in ["cgpa", "gpa"]):
        return "7.05"
    if any(k in q_lower for k in ["city", "current location", "where do you live", "current address"]):
        return "Delhi NCR"
    if any(k in q_lower for k in ["relocate", "comfortable", "ready to"]):
        return "Yes"
    if any(k in q_lower for k in ["highest qualification", "degree", "education"]):
        return "B.Tech CSE (Mathematics & Computing), NSUT Delhi"
    if any(k in q_lower for k in ["college", "university", "institute"]):
        return "Netaji Subhas University of Technology (NSUT Delhi)"
    if any(k in q_lower for k in ["tools", "skills", "technologies"]):
        return "Product Strategy, SQL, Python, Agile, JIRA, LLM Integration, Data Analytics"

    # Fallback to standard field answerer or concise affirmative response
    ans = answer_standard_field(question_text)
    if ans:
        # STRICT: Scrub forbidden employer names
        ans = ans.replace("Magicpin", "a quick-commerce startup").replace("magicpin", "a quick-commerce startup")
        ans = ans.replace("Lifelong", "a consumer D2C brand").replace("lifelong", "a consumer D2C brand")
        return ans
    return "Yes, 1 year of relevant hands-on product and analytics experience."


def apply_to_naukri_job(job_url: str, role_title: str = "Associate Product Manager") -> Dict[str, Any]:
    """
    Automates 1-click and Chatbot-assisted job application on Naukri.com.
    """
    result = {
        "url": job_url,
        "title": role_title,
        "applied": False,
        "status": "failed",
        "questions_answered": [],
        "error": None
    }

    print(f"\n[NaukriApply] Starting application for: {role_title} ({job_url})")

    with sync_playwright() as p:
        browser = None
        is_local = False
        try:
            try:
                browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=3000)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
            except Exception:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768}
                )
                is_local = True

            page = context.new_page()

            page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.5)

            # Check if job listing has expired or redirected to search
            if "expJD=true" in page.url or "/job-listings" not in page.url:
                print(f"[NaukriApply] Job listing is expired (redirected to search). Skipping.")
                result["status"] = "expired"
                page.close()
                return result

            # 1. Check login requirement vs already applied vs external company redirect
            login_apply_btn = page.locator(
                '#login-button, button:has-text("Login to apply"), button:has-text("Register to apply"), a:has-text("Login to apply")'
            ).first
            applied_indicator = page.locator(
                '#already-applied, .already-applied, span.apply-message:has-text("Applied"), '
                '.styles_jhc__apply-button-container:has-text("Applied")'
            ).first
            apply_btn = page.locator('#apply-button, button.apply-button').first
            company_btn = page.locator(
                '#company-site-button, button.company-site-button, button:has-text("Apply on company site"), a:has-text("Apply on company site")'
            ).first

            # 2. Check if external company redirect FIRST (works with or without Naukri login)
            if company_btn.is_visible() and not apply_btn.is_visible():
                print(f"[NaukriApply] External redirect role ('Apply on company site'). Extracting destination URL...")
                dest_url = company_btn.get_attribute("href")
                if not dest_url or "naukri.com" in dest_url:
                    dest_url = None
                    try:
                        with page.expect_popup(timeout=6000) as popup_info:
                            company_btn.click()
                        dest_page = popup_info.value
                        dest_page.wait_for_timeout(2500)
                        dest_url = dest_page.url
                        dest_page.close()
                    except Exception as pe:
                        print(f"[NaukriApply] Popup extraction notice: {pe}")

                page.close()

                # If external portal URL was extracted, run ATS autofill and STRICTLY require confirmed receipt
                if dest_url and dest_url != job_url and "naukri.com" not in dest_url:
                    print(f"[NaukriApply] Extracted company portal URL: {dest_url}")
                    from .ats_filler import fill_ats_application
                    ats_res = fill_ats_application(dest_url, role_title=role_title, auto_submit=True)
                    if ats_res.get("submitted") and ats_res.get("confirmed"):
                        result["applied"] = True
                        result["status"] = "external_applied"
                        result["external_url"] = dest_url
                        return result
                    else:
                        result["applied"] = False
                        result["status"] = "external_redirect"
                        result["external_url"] = dest_url
                        return result
                else:
                    result["applied"] = False
                    result["status"] = "external_redirect"
                    return result

            if login_apply_btn.is_visible() and not apply_btn.is_visible():
                print(f"[NaukriApply] Naukri session is logged out ('Login to apply' visible). Skipping without marking failed.")
                result["status"] = "needs_login"
                page.close()
                return result

            if applied_indicator.is_visible() and not apply_btn.is_visible() and not company_btn.is_visible():
                print(f"[NaukriApply] Job already applied on Naukri!")
                result["applied"] = True
                result["status"] = "already_applied"
                page.close()
                return result

            if not apply_btn.is_visible():
                print(f"[NaukriApply] No direct apply button visible.")
                result["status"] = "no_apply_button"
                page.close()
                return result

            # 3. Click direct Apply button
            print(f"[NaukriApply] Clicking Apply button...")
            apply_btn.click()
            time.sleep(3)

            # 4. Check if immediately applied without chatbot
            page_text_lower = page.locator('body').inner_text().lower()
            if (
                "you have successfully applied" in page_text_lower
                or "applied successfully" in page_text_lower
                or applied_indicator.is_visible()
            ):
                drawer_check = page.locator('.chatbot_Drawer')
                if not drawer_check.is_visible() and not apply_btn.is_visible():
                    print(f"[NaukriApply] 1-Click applied directly without screening!")
                    result["applied"] = True
                    result["status"] = "applied"
                    page.close()
                    return result

            # 5. Handle Chatbot Drawer Screening
            drawer = page.locator('.chatbot_Drawer')
            if not drawer.is_visible():
                time.sleep(2)

            if drawer.is_visible():
                print(f"[NaukriApply] Chatbot screening drawer detected. Starting automated answering...")
                
                for step in range(12):
                    time.sleep(2)
                    
                    # If drawer closed, application completed!
                    if not drawer.is_visible():
                        print(f"[NaukriApply] Chatbot drawer closed - application complete!")
                        result["applied"] = True
                        result["status"] = "applied"
                        break

                    # Check for completion / applied banner inside drawer or page
                    try:
                        drawer_text = drawer.inner_text(timeout=1500)
                    except Exception:
                        drawer_text = ""

                    if any(c in drawer_text.lower() for c in ["successfully applied", "application sent", "applied successfully", "thank you for applying"]):
                        print(f"[NaukriApply] Chatbot confirms application submitted!")
                        result["applied"] = True
                        result["status"] = "applied"
                        break

                    # Check if page has changed to 'Applied'
                    if page.locator('text="Applied", .styles_jhc__top__1o_O3:has-text("Applied")').first.is_visible() and not apply_btn.is_visible():
                        print(f"[NaukriApply] Background page transitioned to 'Applied'!")
                        result["applied"] = True
                        result["status"] = "applied"
                        break

                    # Get latest bot question text
                    bot_msgs = drawer.locator('.botItem.chatbot_ListItem, .botMsg').all()
                    latest_question = ""
                    if bot_msgs:
                        latest_question = bot_msgs[-1].inner_text().strip()
                        print(f"[NaukriApply] Question {step+1}: {latest_question[:100]}...")

                    # Check for SingleSelect Radio Buttons
                    radio_containers = drawer.locator('.ssrc__radio-btn-container').all()
                    if radio_containers and any(rc.is_visible() for rc in radio_containers):
                        labels = [rc.locator('label').inner_text().strip() for rc in radio_containers if rc.is_visible()]
                        choice = determine_radio_choice(latest_question, labels)
                        print(f"[NaukriApply] Available options: {labels} -> Selected: {choice}")
                        
                        target_label = drawer.locator(f'label:has-text("{choice}")').first
                        box = target_label.bounding_box()
                        if box:
                            page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                        else:
                            page.evaluate(f'''() => {{
                                const l = Array.from(document.querySelectorAll('.ssrc__label')).find(el => el.innerText.includes("{choice}"));
                                if (l) l.click();
                            }}''')
                        time.sleep(0.8)

                        # Click Save/Send button if present
                        send_div = drawer.locator('.sendMsg, .sendMsgbtn_container').first
                        if send_div.is_visible():
                            send_div.click()
                        time.sleep(2)
                        result["questions_answered"].append({"q": latest_question, "a": choice})
                        continue

                    # Check for Chips, Buttons, or Quick-Replies
                    chips = drawer.locator('.chip, .quick-reply, .chatbot_ListItem button, .chips-container button, .ssrc__checkbox-btn-container label').all()
                    visible_chips = [c for c in chips if c.is_visible()]
                    if visible_chips:
                        chip_texts = [c.inner_text().strip() for c in visible_chips]
                        choice = determine_radio_choice(latest_question, chip_texts)
                        print(f"[NaukriApply] Chip/pill options: {chip_texts} -> Selected: {choice}")
                        target_chip = next((c for c in visible_chips if choice.lower() in c.inner_text().lower()), visible_chips[0])
                        target_chip.click()
                        time.sleep(1)

                        send_div = drawer.locator('.sendMsg, .sendMsgbtn_container, button:has-text("Save"), button:has-text("Submit")').first
                        if send_div.is_visible():
                            send_div.click()
                        time.sleep(2)
                        result["questions_answered"].append({"q": latest_question, "a": choice})
                        continue

                    # Check for Text / Number input
                    text_input = drawer.locator('.textArea[contenteditable="true"], [contenteditable="true"], input[type="text"], input[type="number"], textarea').first
                    if text_input.is_visible():
                        ans = determine_text_answer(latest_question)
                        print(f"[NaukriApply] Typing text answer: '{ans}'")
                        text_input.click()
                        time.sleep(0.3)
                        tag = text_input.evaluate('el => el.tagName.toLowerCase()')
                        is_ce = text_input.evaluate('el => el.getAttribute("contenteditable") === "true"')
                        if is_ce or tag not in ['input', 'textarea']:
                            page.keyboard.insert_text(ans)
                        else:
                            text_input.fill(ans)
                        time.sleep(0.5)
                        page.keyboard.press("Enter")
                        time.sleep(0.8)
                        
                        send_div = drawer.locator('.sendMsg, .sendMsgbtn_container, button:has-text("Save"), button:has-text("Submit"), .send-btn').first
                        if send_div.is_visible() and send_div.is_enabled():
                            send_div.click()
                        time.sleep(2)
                        result["questions_answered"].append({"q": latest_question, "a": ans})
                        continue

                    # Check for File Upload (only if question explicitly asks for resume/file)
                    if any(w in latest_question.lower() for w in ["resume", "cv", "upload", "attach"]):
                        file_input = drawer.locator('input[type="file"]').first
                        if file_input.count() > 0:
                            try:
                                file_input.set_input_files(RESUME_PATH)
                                print(f"[NaukriApply] Uploaded resume: {RESUME_PATH}")
                                time.sleep(1.5)
                                send_div = drawer.locator('.sendMsg, .sendMsgbtn_container').first
                                if send_div.is_visible():
                                    send_div.click()
                                time.sleep(2)
                                continue
                            except:
                                pass

                    # If no interactive elements found, check if completed via explicit indicator (never substring 'applied' in JD)
                    time.sleep(2)
                    if not drawer.is_visible() and applied_indicator.is_visible() and not apply_btn.is_visible():
                        result["applied"] = True
                        result["status"] = "applied"
                        break

            # Final verification
            page.wait_for_timeout(2000)
            if applied_indicator.is_visible() and not apply_btn.is_visible() and not company_btn.is_visible():
                result["applied"] = True
                result["status"] = "applied"
            
            page.close()
            return result

        except Exception as e:
            print(f"[NaukriApply] Exception while applying: {e}")
            result["error"] = str(e)
            return result
        finally:
            if browser and is_local:
                try:
                    browser.close()
                except:
                    pass
