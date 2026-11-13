import time
import re
from typing import Dict, Any, List
from playwright.sync_api import sync_playwright
from .form_answerer import answer_standard_field, answer_open_ended_question, load_candidate_profile
from ..db import database

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/gform-filler"

def fill_google_form(form_url: str, auto_submit: bool = False, role_context: str = "Associate Product Manager") -> Dict[str, Any]:
    """
    Connects to Ridealong Chrome, navigates to a Google Form,
    automatically detects fields, fills standard candidate details,
    uses the LLM to draft answers to open-ended questions,
    and returns a summary + screenshot.
    """
    result = {
        "url": form_url,
        "fields_filled": [],
        "open_ended_answered": [],
        "submitted": False,
        "screenshot_path": None,
        "error": None
    }
    
    print(f"[GFormFiller] Connecting to Ridealong Chrome for Google Form: {form_url}")
    
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0]
            page = context.new_page()

            page.goto(form_url, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # Identify all question blocks in the Google Form
            # Google Form questions are typically wrapped in role="listitem"
            question_items = page.locator('div[role="listitem"]').all()
            print(f"[GFormFiller] Found {len(question_items)} question blocks.")

            for item in question_items:
                # Find question label
                heading_el = item.locator('div[role="heading"], span.M7eMe, div.HoDaR').first
                if not heading_el.is_visible(timeout=500):
                    continue
                
                question_text = heading_el.inner_text().strip()
                # Remove red asterisk if present
                clean_q = re.sub(r"[\*\n]+", " ", question_text).strip()
                
                # Check for standard field match first
                matched_val = answer_standard_field(clean_q)
                
                # Check for input element inside this item
                text_input = item.locator('input[type="text"], input[type="email"], input[type="tel"]').first
                textarea_input = item.locator('textarea').first
                radio_options = item.locator('div[role="radio"]').all()

                if text_input.is_visible(timeout=500):
                    if matched_val:
                        text_input.fill(matched_val)
                        result["fields_filled"].append({"question": clean_q, "answer": matched_val})
                    else:
                        # Open-ended short answer
                        llm_answer = answer_open_ended_question(clean_q, role_title=role_context)
                        text_input.fill(llm_answer)
                        result["open_ended_answered"].append({"question": clean_q, "answer": llm_answer})

                elif textarea_input.is_visible(timeout=500):
                    if matched_val:
                        textarea_input.fill(matched_val)
                        result["fields_filled"].append({"question": clean_q, "answer": matched_val})
                    else:
                        # Open-ended long answer
                        llm_answer = answer_open_ended_question(clean_q, role_title=role_context)
                        textarea_input.fill(llm_answer)
                        result["open_ended_answered"].append({"question": clean_q, "answer": llm_answer})

                elif radio_options:
                    # Radio group: get option labels
                    opt_labels = [opt.inner_text().strip() for opt in radio_options]
                    matched_opt = answer_standard_field(clean_q, options=opt_labels)
                    if matched_opt:
                        for opt_el in radio_options:
                            if matched_opt.lower() in opt_el.inner_text().lower():
                                opt_el.click()
                                result["fields_filled"].append({"question": clean_q, "answer": matched_opt})
                                break

            time.sleep(1)
            # Take verification screenshot
            screenshot_path = f"/Users/admin/linkedin-multiagent/data/gform_{int(time.time())}.png"
            page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path
            print(f"[GFormFiller] Form filled. Screenshot saved to {screenshot_path}")

            if auto_submit:
                submit_btn = page.locator('div[role="button"]:has-text("Submit"), span:has-text("Submit")').first
                if submit_btn.is_visible(timeout=2000):
                    submit_btn.click()
                    time.sleep(2)
                    result["submitted"] = True
                    print("[GFormFiller] Google Form submitted successfully.")

        except Exception as e:
            print(f"[GFormFiller] Form error: {e}")
            result["error"] = str(e)
        finally:
            try:
                page.close()
            except:
                pass

    return result
