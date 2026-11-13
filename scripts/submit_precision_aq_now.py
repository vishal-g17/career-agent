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
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/precision-apply")
        print("[PrecisionAQ] Connected to browser cleanly!", flush=True)

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"[PrecisionAQ] Navigating to {PRECISION_AQ_URL}...", flush=True)
            page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
            time.sleep(3)

            # Close overlays
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

            modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]:has-text("Apply to"), dialog').first
            if not modal.is_visible(timeout=6000):
                print("[PrecisionAQ] Modal not visible.", flush=True)
                return False

            print("[PrecisionAQ] Modal opened successfully. Navigating steps...", flush=True)

            for step in range(12):
                time.sleep(1.5)
                print(f"\n--- [PrecisionAQ] Wizard Step {step + 1} ---", flush=True)

                submit_btn = modal.locator('button:has-text("Submit application"), button:has-text("Submit")').first
                review_btn = modal.locator('button:has-text("Review")').first
                next_btn = modal.locator('button:has-text("Next")').first

                # Check file upload
                try:
                    file_input = modal.locator('input[type="file"]').first
                    if file_input.count() > 0:
                        file_input.set_input_files(RESUME_PDF)
                        print(f"[PrecisionAQ] Set resume file: {RESUME_PDF}", flush=True)
                        time.sleep(0.5)
                except:
                    pass

                # Smart JS element answering
                script = f"""() => {{
                    const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
                    if (!modal) return {{ error: 'no_modal' }};

                    const filled = [];

                    // 1. Text & Textarea fields
                    const inputs = modal.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="radio"]):not([type="checkbox"]), textarea');
                    inputs.forEach(inp => {{
                        let label = inp.getAttribute('aria-label') || inp.placeholder || '';
                        if (!label) {{
                            const lblEl = inp.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, div')?.querySelector('label');
                            if (lblEl) label = lblEl.innerText;
                        }}
                        const clean = (label || '').toLowerCase();

                        let ans = null;
                        if (clean.includes('sql') || clean.includes('complex')) {{
                            ans = {json.dumps(SQL_ANSWER)};
                        }} else if (clean.includes('healthcare') || clean.includes('claims') || clean.includes('patient')) {{
                            ans = {json.dumps(HEALTHCARE_ANSWER)};
                        }} else if (clean.includes('salary') || clean.includes('ctc') || clean.includes('compensation') || clean.includes('desired')) {{
                            ans = 'Negotiable';
                        }} else if (clean.includes('linkedin')) {{
                            ans = 'https://www.linkedin.com/in/vishal-gupta-product/';
                        }} else if (clean.includes('city') || clean.includes('location')) {{
                            ans = 'Delhi, India';
                        }} else if (clean.includes('street') || clean.includes('address 1')) {{
                            ans = 'Sector 3, Rohini';
                        }} else if (clean.includes('postal') || clean.includes('zip')) {{
                            ans = '110085';
                        }} else if (clean.includes('notice')) {{
                            ans = '15 days';
                        }} else if (clean.includes('visa') || clean.includes('expiration')) {{
                            ans = 'N/A - Indian Citizen';
                        }} else if (clean.includes('if yes, when') || clean.includes('which company')) {{
                            ans = 'No prior employment with Precision AQ';
                        }} else if (clean.includes('year') || clean.includes('how many')) {{
                            ans = '1';
                        }} else if (!inp.value || inp.value === 'Yes' || inp.value === '1') {{
                            if (inp.tagName.toLowerCase() === 'textarea') ans = {json.dumps(SQL_ANSWER)};
                            else ans = 'Yes';
                        }}

                        if (ans) {{
                            inp.value = ans;
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            filled.push({{ field: clean.substring(0, 30), val: ans.substring(0, 20) }});
                        }}
                    }});

                    // 2. Select dropdowns
                    const selects = modal.querySelectorAll('select');
                    selects.forEach(sel => {{
                        let label = sel.getAttribute('aria-label') || '';
                        if (!label) {{
                            const lblEl = sel.closest('div')?.querySelector('label');
                            if (lblEl) label = lblEl.innerText;
                        }}
                        const clean = (label || '').toLowerCase();

                        let targetIndex = -1;
                        for (let i = 0; i < sel.options.length; i++) {{
                            const opt = sel.options[i].text.toLowerCase();
                            if (clean.includes('country') || clean.includes('legal address')) {{
                                if (opt === 'india' || opt.includes('india')) {{ targetIndex = i; break; }}
                            }} else if (clean.includes('work') || clean.includes('authorized') || clean.includes('sponsorship')) {{
                                if (opt.includes('yes') || opt.includes('authorized') || opt.includes('citizen')) {{ targetIndex = i; break; }}
                            }} else if (clean.includes('worked') || clean.includes('previously employed')) {{
                                if (opt.includes('no')) {{ targetIndex = i; break; }}
                            }} else if (opt.includes('yes') || opt.includes('immediate') || opt.includes('15 days') || opt.includes('1')) {{
                                targetIndex = i;
                                break;
                            }}
                        }}

                        if (targetIndex !== -1) {{
                            sel.selectedIndex = targetIndex;
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            filled.push({{ select: clean.substring(0, 30), chosen: sel.options[targetIndex].text }});
                        }}
                    }});

                    // 3. Radios
                    const radios = modal.querySelectorAll('input[type="radio"]');
                    radios.forEach(r => {{
                        const rLbl = (r.closest('label')?.innerText || r.parentElement?.innerText || '').toLowerCase();
                        if (!r.checked && (rLbl.includes('yes') || rLbl.includes('authorized') || rLbl.includes('agree'))) {{
                            r.click();
                            filled.push({{ radio: rLbl.trim() }});
                        }}
                    }});

                    // 4. Checkboxes
                    const checkboxes = modal.querySelectorAll('input[type="checkbox"]');
                    checkboxes.forEach(cb => {{
                        if (!cb.checked) {{
                            cb.click();
                            filled.push('checkbox');
                        }}
                    }});

                    return {{ filled }};
                }}"""
                res = page.evaluate(script)
                print(f"[PrecisionAQ] Handled: {res.get('filled')}", flush=True)

                if submit_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] >>> Found Submit button! SUBMITTING APPLICATION NOW...", flush=True)
                    submit_btn.click()
                    time.sleep(3)
                    print("[PrecisionAQ] >>> SUCCESS: Application submitted to Precision AQ!", flush=True)
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
                    conn.commit()
                    conn.close()
                    return True
                elif review_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Clicking 'Review'...", flush=True)
                    review_btn.click()
                elif next_btn.is_visible(timeout=1000):
                    print("[PrecisionAQ] Clicking 'Next'...", flush=True)
                    next_btn.click()
                else:
                    if modal.locator('button:has-text("Done"), button:has-text("Dismiss")').count() > 0:
                        print("[PrecisionAQ] Done modal visible, application complete!", flush=True)
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
                        conn.commit()
                        conn.close()
                        return True
                    break

            return False
        except Exception as e:
            print(f"[PrecisionAQ] Exception during application: {e}", flush=True)
            return False
        finally:
            page.close()

if __name__ == "__main__":
    success = apply()
    print(f"\n[PrecisionAQ] Final Result: {success}", flush=True)
