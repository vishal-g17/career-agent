import time
import os
import json
import sqlite3
from playwright.sync_api import sync_playwright

DB_PATH = os.path.expanduser("~/.linkedin_agent.db")
PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"
RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"
if not os.path.exists(RESUME_PDF):
    RESUME_PDF = "/Users/admin/linkedin-multiagent/data/resume.pdf"

print(f"[Init] Using Resume PDF: {RESUME_PDF}", flush=True)

SQL_ANSWER = (
    "At my product analytics role, I analyzed high drop-offs in our checkout & activation funnel. "
    "I constructed multi-step funnel telemetry queries using SQL CTEs and window functions (LAG/LEAD, ROW_NUMBER) "
    "across millions of session events to identify that users dropped off by 28% at payment method selection due to latency. "
    "By aggregating cohort retention metrics and segmenting by device and payment gateway, we proved the bottleneck to engineering. "
    "Fixing the gateway retry logic increased checkout conversion by 14% and recovered recurring MRR."
)

HEALTHCARE_ANSWER = (
    "I have analyzed patient engagement telemetry, appointment booking workflows, and health/wellness transactional event streams. "
    "My focus was optimizing conversion funnels, tracking user behavior cohorts, and building automated data pipelines in SQL/PostgreSQL "
    "to monitor retention and SLA compliance. I am experienced in working with large-scale relational schemas, handling compliance standards, "
    "and translating complex health event streams into clear executive dashboards."
)

def apply():
    print("[PrecisionAQ] Connecting to ridealong CDP...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/precision-apply-v2")
        print("[PrecisionAQ] Connected cleanly!", flush=True)

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"[PrecisionAQ] Navigating to {PRECISION_AQ_URL}...", flush=True)
            page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
            time.sleep(3)

            # Close any overlay messages
            for cb in page.locator('aside button:has-text("Close"), button[aria-label*="Close conversation"]').all():
                try: cb.click()
                except: pass

            easy_apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button:has-text("Easy Apply")').first
            if not easy_apply_btn.is_visible(timeout=5000):
                if page.locator('span:has-text("Applied"), div:has-text("Applied")').count() > 0:
                    print("[PrecisionAQ] Already applied on LinkedIn!", flush=True)
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
                    conn.commit()
                    conn.close()
                    return True
                print("[PrecisionAQ] Easy Apply button not visible.", flush=True)
                return False

            print("[PrecisionAQ] Clicking 'Easy Apply'...", flush=True)
            easy_apply_btn.click(force=True)
            time.sleep(2)

            modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]').first
            if not modal.is_visible(timeout=6000):
                print("[PrecisionAQ] Modal not visible.", flush=True)
                return False

            for step_idx in range(1, 15):
                time.sleep(1.5)
                title = modal.locator('h3, h2, h1').first.inner_text() if modal.locator('h3, h2, h1').count() > 0 else f"Step {step_idx}"
                print(f"\n--- [PrecisionAQ] Wizard Step {step_idx}: {title} ---", flush=True)

                # Check if Submit button is directly visible
                submit_btn = modal.locator('button:has-text("Submit application"), button:has-text("Submit")').first
                if submit_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] >>> Found Submit button! SUBMITTING APPLICATION NOW...", flush=True)
                    submit_btn.click()
                    time.sleep(3)
                    page.screenshot(path="data/precision_aq_submitted.png")
                    print("[PrecisionAQ] >>> Application submitted! Screenshot saved to data/precision_aq_submitted.png", flush=True)
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
                    conn.commit()
                    conn.close()
                    return True

                # Resume file upload
                try:
                    file_input = modal.locator('input[type="file"]').first
                    if file_input.count() > 0:
                        file_input.set_input_files(RESUME_PDF)
                        print(f"[PrecisionAQ] Attached resume: {RESUME_PDF}", flush=True)
                        time.sleep(0.5)
                except:
                    pass

                # 1. Native Playwright filling for all text inputs & textareas
                inputs = modal.locator('input:not([type="hidden"]):not([type="file"]):not([type="radio"]):not([type="checkbox"]), textarea').all()
                for inp in inputs:
                    try:
                        lbl = inp.get_attribute('aria-label') or inp.get_attribute('placeholder') or ''
                        inp_id = inp.get_attribute('id') or ''
                        clean = lbl.lower()
                        
                        val_to_fill = None
                        if 'salary' in clean or 'desired' in clean or '3b' in inp_id:
                            val_to_fill = "1200000"
                        elif 'sql' in clean or 'complex' in clean or '4g' in inp_id:
                            val_to_fill = SQL_ANSWER
                        elif 'healthcare' in clean or 'claims' in clean or '4h' in inp_id:
                            val_to_fill = HEALTHCARE_ANSWER
                        elif 'linkedin' in clean or '3a' in inp_id:
                            val_to_fill = "https://www.linkedin.com/in/vishal-gupta-product/"
                        elif 'street' in clean or 'address 1' in clean or '4c' in inp_id:
                            val_to_fill = "Sector 3, Rohini"
                        elif 'city' in clean or '4d' in inp_id:
                            val_to_fill = "Delhi"
                        elif 'postal' in clean or 'zip' in clean:
                            val_to_fill = "110085"
                        elif 'other' in clean or '46' in inp_id:
                            val_to_fill = "N/A - Citizen"
                        elif 'if yes, when' in clean or '3r' in inp_id or '41' in inp_id:
                            val_to_fill = "N/A"
                        elif 'notice' in clean:
                            val_to_fill = "15 days"
                        elif 'year' in clean:
                            val_to_fill = "1"

                        if val_to_fill:
                            inp.fill(val_to_fill)
                            print(f"[PrecisionAQ] Native filled '{lbl[:25]}' -> '{val_to_fill[:20]}'", flush=True)
                    except Exception as ie:
                        pass

                # 2. JS evaluate for Select dropdowns & Radios
                fill_js = """() => {
                    const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
                    if (!modal) return;

                    // Selects
                    const selects = modal.querySelectorAll('select');
                    selects.forEach(sel => {
                        let label = sel.getAttribute('aria-label') || '';
                        if (!label && sel.id) {
                            const lblEl = modal.querySelector('label[for="' + sel.id + '"]');
                            if (lblEl) label = lblEl.innerText;
                        }
                        const clean = (label || '').toLowerCase();

                        let chosen = null;
                        for (let i = 0; i < sel.options.length; i++) {
                            const optText = sel.options[i].text.toLowerCase();
                            if (clean.includes('non-compete') || clean.includes('non-solicit')) {
                                if (optText === 'no') { chosen = i; break; }
                            } else if (clean.includes('work authorization') || clean.includes('type of valid')) {
                                if (optText.includes('citizen')) { chosen = i; break; }
                            } else if (clean.includes('country') || clean.includes('legal address')) {
                                if (optText === 'india') { chosen = i; break; }
                            } else if (clean.includes('worked') || clean.includes('employed')) {
                                if (optText.includes('no')) { chosen = i; break; }
                            }
                        }
                        if (chosen !== null) {
                            sel.selectedIndex = chosen;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    });

                    // Radios
                    const fieldsets = modal.querySelectorAll('fieldset');
                    fieldsets.forEach(fs => {
                        const legend = (fs.querySelector('legend')?.innerText || fs.closest('div')?.querySelector('span, label, p')?.innerText || '').toLowerCase();
                        const radios = fs.querySelectorAll('input[type="radio"]');
                        if (radios.length === 0) return;

                        let targetVal = 'yes';
                        if (legend.includes('sponsor') || legend.includes('immigration')) {
                            targetVal = 'no';
                        } else if (legend.includes('ever worked') || legend.includes('previously worked')) {
                            targetVal = 'no';
                        } else if (legend.includes('ever interviewed') || legend.includes('previously interviewed')) {
                            targetVal = 'no';
                        } else if (legend.includes('authorized to work')) {
                            targetVal = 'yes';
                        } else if (legend.includes('certify') || legend.includes('complete and accurate')) {
                            targetVal = 'yes';
                        }

                        radios.forEach(r => {
                            let rText = '';
                            let container = r;
                            for (let c = 0; c < 5; c++) {
                                if (!container.parentElement) break;
                                container = container.parentElement;
                                if (container.innerText && container.innerText.trim().length > 0 && container.innerText.trim().length < 30) {
                                    rText = container.innerText.trim().toLowerCase();
                                    break;
                                }}
                            if (rText === targetVal || rText.startsWith(targetVal)) {
                                r.click();
                                r.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        });
                    });
                }"""
                page.evaluate(fill_js)

                time.sleep(1)

                review_btn = modal.locator('button:has-text("Review")').first
                next_btn = modal.locator('button:has-text("Next")').first

                if review_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Clicking 'Review'...", flush=True)
                    review_btn.click()
                elif next_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Clicking 'Next'...", flush=True)
                    next_btn.click()
                else:
                    if submit_btn.is_visible(timeout=1000):
                        print("[PrecisionAQ] Clicking 'Submit'...", flush=True)
                        submit_btn.click()
                        time.sleep(3)
                        return True
                    print("[PrecisionAQ] Neither Review, Next, nor Submit found. Checking Done/Dismiss...", flush=True)
                    done_btn = modal.locator('button:has-text("Done"), button:has-text("Dismiss")').first
                    if done_btn.is_visible(timeout=1000):
                        print("[PrecisionAQ] Application finished successfully!", flush=True)
                        done_btn.click()
                        return True
                    time.sleep(2)

            return False
        except Exception as e:
            print(f"[PrecisionAQ] Error: {e}", flush=True)
            return False
        finally:
            page.close()

if __name__ == "__main__":
    ok = apply()
    print(f"\n[PrecisionAQ] Result: {ok}", flush=True)
