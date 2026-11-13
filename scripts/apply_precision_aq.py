import time
import os
import re
import sqlite3
from playwright.sync_api import sync_playwright

DB_PATH = os.path.expanduser("~/.linkedin_agent.db")
PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"
RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"
if not os.path.exists(RESUME_PDF):
    RESUME_PDF = "/Users/admin/linkedin-multiagent/data/resume.pdf"

print(f"[Init] Using Resume PDF: {RESUME_PDF}")

def apply_precision_aq():
    print("\n" + "="*60)
    print(">>> APPLYING TO PRECISION AQ VIA LINKEDIN EASY APPLY")
    print("="*60)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/precision-apply")
        context = browser.contexts[0]
        page = context.new_page()
        try:
            print(f"[PrecisionAQ] Navigating to {PRECISION_AQ_URL}...")
            page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # Close any floating chat overlays
            for cb in page.locator('aside button:has-text("Close"), button[aria-label*="Close conversation"]').all():
                try:
                    cb.click()
                except:
                    pass

            easy_apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button:has-text("Easy Apply")').first
            if not easy_apply_btn.is_visible(timeout=4000):
                print("[PrecisionAQ] Easy Apply button not visible. Checking if already applied...")
                if page.locator('span:has-text("Applied"), div:has-text("Applied")').count() > 0:
                    print("[PrecisionAQ] Already applied on LinkedIn!")
                    return True
                return False

            print("[PrecisionAQ] Clicking 'Easy Apply'...")
            easy_apply_btn.click(force=True)
            time.sleep(2)

            modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]:has-text("Apply to"), dialog').first
            if not modal.is_visible(timeout=5000):
                print("[PrecisionAQ] Modal did not open.")
                return False

            print("[PrecisionAQ] Modal opened successfully!")

            for step in range(12):
                time.sleep(1.5)
                print(f"\n--- [PrecisionAQ] Step {step + 1} ---")

                # Fast JS examination and autofill inside modal
                script = """() => {
                    const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
                    if (!modal) return { success: false, reason: 'no_modal' };

                    const handled = [];
                    // 1. Phone number
                    const phone = modal.querySelector('input[id*="phoneNumber"], input[type="tel"]');
                    if (phone && (!phone.value || phone.value.length < 5)) {
                        phone.value = '+918368387245';
                        phone.dispatchEvent(new Event('input', { bubbles: true }));
                        phone.dispatchEvent(new Event('change', { bubbles: true }));
                        handled.push('phone');
                    }

                    // 2. Text / Textarea
                    const inputs = modal.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="radio"]):not([type="checkbox"]), textarea');
                    inputs.forEach(inp => {
                        let label = inp.getAttribute('aria-label') || inp.placeholder || '';
                        if (!label) {
                            const lblEl = inp.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, div')?.querySelector('label');
                            if (lblEl) label = lblEl.innerText;
                        }
                        label = (label || '').toLowerCase();
                        if (!inp.value) {
                            let ans = '';
                            if (label.includes('year') || label.includes('experience') || label.includes('how many')) ans = '1';
                            else if (label.includes('notice')) ans = '15 days';
                            else if (label.includes('salary') || label.includes('ctc') || label.includes('compensation')) ans = 'Negotiable';
                            else if (label.includes('city') || label.includes('location')) ans = 'Delhi, India';
                            else if (label.includes('linkedin')) ans = 'https://www.linkedin.com/in/vishal-gupta-product/';
                            else if (label.includes('website') || label.includes('portfolio')) ans = 'https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing';
                            else ans = 'Yes';

                            inp.value = ans;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                            handled.push({ field: label, val: ans });
                        }
                    });

                    // 3. Select Dropdowns
                    const selects = modal.querySelectorAll('select');
                    selects.forEach(sel => {
                        let label = sel.getAttribute('aria-label') || '';
                        if (!label) {
                            const lblEl = sel.closest('div')?.querySelector('label');
                            if (lblEl) label = lblEl.innerText;
                        }
                        if (sel.selectedIndex <= 0 && sel.options.length > 1) {
                            let found = false;
                            for (let i = 0; i < sel.options.length; i++) {
                                const opt = sel.options[i].text.toLowerCase();
                                if (opt.includes('yes') || opt.includes('1') || opt.includes('immediate') || opt.includes('15 days')) {
                                    sel.selectedIndex = i;
                                    found = true;
                                    break;
                                }
                            }
                            if (!found && sel.options.length > 1) sel.selectedIndex = 1;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            handled.push({ select: label, selected: sel.options[sel.selectedIndex].text });
                        }
                    });

                    // 4. Radio buttons (Yes/No)
                    const radios = modal.querySelectorAll('input[type="radio"]');
                    radios.forEach(r => {
                        const rLbl = (r.closest('label')?.innerText || r.parentElement?.innerText || '').toLowerCase();
                        if (rLbl.includes('yes') || rLbl.includes('authorized') || rLbl.includes('agree')) {
                            if (!r.checked) {
                                r.click();
                                handled.push({ radio: rLbl.trim() });
                            }
                        }
                    });

                    // 5. Checkboxes (terms / agreement)
                    const checkboxes = modal.querySelectorAll('input[type="checkbox"]');
                    checkboxes.forEach(cb => {
                        if (!cb.checked) {
                            cb.click();
                            handled.push('checkbox');
                        }
                    });

                    return { success: true, handled };
                }"""
                res = page.evaluate(script)
                print(f"[PrecisionAQ] Handled fields: {res.get('handled')}")

                # Check if file upload is present
                try:
                    file_input = modal.locator('input[type="file"]').first
                    if file_input.count() > 0:
                        file_input.set_input_files(RESUME_PDF)
                        print(f"[PrecisionAQ] Uploaded resume PDF")
                        time.sleep(1)
                except Exception as fe:
                    pass

                # Action buttons
                submit_btn = modal.locator('button:has-text("Submit application"), button:has-text("Submit")').first
                review_btn = modal.locator('button:has-text("Review")').first
                next_btn = modal.locator('button:has-text("Next")').first

                if submit_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Found Submit button! Submitting application...")
                    submit_btn.click()
                    time.sleep(3)
                    try:
                        page.screenshot(path="/Users/admin/linkedin-multiagent/data/precision_aq_submitted.png", timeout=5000)
                    except:
                        pass
                    print("[PrecisionAQ] Successfully submitted application to Precision AQ!")

                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
                    conn.commit()
                    conn.close()
                    return True
                elif review_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Clicking 'Review'...")
                    review_btn.click()
                elif next_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Clicking 'Next'...")
                    next_btn.click()
                else:
                    print("[PrecisionAQ] Checking if already completed...")
                    if modal.locator('button:has-text("Done"), button:has-text("Dismiss")').count() > 0:
                        print("[PrecisionAQ] Application finished (Done/Dismiss visible)!")
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
                        conn.commit()
                        conn.close()
                        return True
                    break

            return False
        except Exception as e:
            print(f"[PrecisionAQ] Exception: {e}")
            return False
        finally:
            page.close()

if __name__ == "__main__":
    success = apply_precision_aq()
    print(f"\n>>> Final Precision AQ Result: {success}")
