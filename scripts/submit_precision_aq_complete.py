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
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/precision-apply-complete")
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

            print("[PrecisionAQ] Modal opened successfully. Iterating steps...", flush=True)

            for step_idx in range(1, 15):
                time.sleep(1.5)
                title = modal.locator('h3, h2, h1').first.inner_text() if modal.locator('h3, h2, h1').count() > 0 else f"Step {step_idx}"
                print(f"\n--- [PrecisionAQ] Wizard Step {step_idx}: {title} ---", flush=True)

                # Check if Submit button is visible
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

                # Universal Form Filler via React descriptor setters
                fill_js = f"""() => {{
                    const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
                    if (!modal) return {{ error: 'no_modal' }};
                    const logs = [];

                    function setReactValue(el, val) {{
                        const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) {{
                            setter.call(el, val);
                        }} else {{
                            el.value = val;
                        }}
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}

                    // 1. Text & Textarea inputs
                    const inputs = modal.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="radio"]):not([type="checkbox"]), textarea');
                    inputs.forEach(inp => {{
                        let label = inp.getAttribute('aria-label') || inp.placeholder || '';
                        if (!label && inp.id) {{
                            const lblEl = modal.querySelector('label[for="' + inp.id + '"]');
                            if (lblEl) label = lblEl.innerText;
                        }}
                        if (!label) {{
                            const lblEl = inp.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, div')?.querySelector('label');
                            if (lblEl) label = lblEl.innerText;
                        }}
                        const clean = (label || '').toLowerCase();
                        const inpId = (inp.id || '').toLowerCase();

                        let ans = null;
                        if (clean.includes('salary') || clean.includes('desired') || clean.includes('ctc') || inpId.includes('3b')) {{
                            ans = '1200000';
                        }} else if (clean.includes('sql') || clean.includes('complex') || inpId.includes('4g')) {{
                            ans = {json.dumps(SQL_ANSWER)};
                        }} else if (clean.includes('healthcare') || clean.includes('claims') || clean.includes('patient') || inpId.includes('4h')) {{
                            ans = {json.dumps(HEALTHCARE_ANSWER)};
                        }} else if (clean.includes('linkedin') || inpId.includes('3a')) {{
                            ans = 'https://www.linkedin.com/in/vishal-gupta-product/';
                        }} else if (clean.includes('street') || clean.includes('address 1') || inpId.includes('4c')) {{
                            ans = 'Sector 3, Rohini';
                        }} else if (clean.includes('city') || clean.includes('location') || inpId.includes('4d')) {{
                            ans = 'Delhi';
                        }} else if (clean.includes('postal') || clean.includes('zip')) {{
                            ans = '110085';
                        }} else if (clean.includes('other') || inpId.includes('46')) {{
                            ans = 'N/A - Citizen';
                        }} else if (clean.includes('if yes, when') || clean.includes('which company') || inpId.includes('3r') || inpId.includes('41')) {{
                            ans = 'N/A';
                        }} else if (clean.includes('notice')) {{
                            ans = '15 days';
                        }} else if (clean.includes('year') || clean.includes('how many')) {{
                            ans = '1';
                        }} else if (!inp.value || inp.value === 'Yes') {{
                            if (inp.tagName === 'TEXTAREA') ans = {json.dumps(SQL_ANSWER)};
                            else if (clean.includes('phone') || clean.includes('mobile')) {{}} // keep phone
                            else ans = '1';
                        }}

                        if (ans !== null && inp.value !== ans) {{
                            setReactValue(inp, ans);
                            logs.push('Set input [' + clean.substring(0, 25) + '] -> ' + ans.substring(0, 15));
                        }}
                    }});

                    // 2. Selects
                    const selects = modal.querySelectorAll('select');
                    selects.forEach(sel => {{
                        let label = sel.getAttribute('aria-label') || '';
                        if (!label && sel.id) {{
                            const lblEl = modal.querySelector('label[for="' + sel.id + '"]');
                            if (lblEl) label = lblEl.innerText;
                        }}
                        if (!label) {{
                            const lblEl = sel.closest('div')?.querySelector('label');
                            if (lblEl) label = lblEl.innerText;
                        }}
                        const clean = (label || '').toLowerCase();

                        let targetIdx = -1;
                        for (let i = 0; i < sel.options.length; i++) {{
                            const optText = sel.options[i].text.toLowerCase();
                            if (clean.includes('non-compete') || clean.includes('non-solicit')) {{
                                if (optText === 'no') {{ targetIdx = i; break; }}
                            }} else if (clean.includes('work authorization') || clean.includes('type of valid')) {{
                                if (optText.includes('citizen')) {{ targetIdx = i; break; }}
                            }} else if (clean.includes('country') || clean.includes('legal address')) {{
                                if (optText === 'india') {{ targetIdx = i; break; }}
                            }} else if (clean.includes('worked') || clean.includes('employed')) {{
                                if (optText.includes('no')) {{ targetIdx = i; break; }}
                            }} else if (optText.includes('india (+91)') || optText.includes('united arab emirates (+971)')) {{
                                targetIdx = i; break;
                            }} else if (optText.includes('vishal90301@gmail.com')) {{
                                targetIdx = i; break;
                            }} else if (optText.includes('yes') || optText.includes('15 days') || optText.includes('1')) {{
                                targetIdx = i; break;
                            }}
                        }}

                        if (targetIdx !== -1 && sel.selectedIndex !== targetIdx) {{
                            sel.selectedIndex = targetIdx;
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            logs.push('Select [' + clean.substring(0, 25) + '] -> ' + sel.options[targetIdx].text);
                        }}
                    }});

                    // 3. Fieldsets & Radios
                    const fieldsets = modal.querySelectorAll('fieldset');
                    fieldsets.forEach(fs => {{
                        const legend = (fs.querySelector('legend')?.innerText || fs.closest('div')?.querySelector('span, label, p')?.innerText || '').toLowerCase();
                        const radios = fs.querySelectorAll('input[type="radio"]');
                        if (radios.length === 0) return;

                        let targetVal = 'yes';
                        if (legend.includes('sponsor') || legend.includes('immigration')) {{
                            targetVal = 'no';
                        }} else if (legend.includes('ever worked') || legend.includes('previously worked')) {{
                            targetVal = 'no';
                        }} else if (legend.includes('ever interviewed') || legend.includes('previously interviewed')) {{
                            targetVal = 'no';
                        }} else if (legend.includes('authorized to work')) {{
                            targetVal = 'yes';
                        }} else if (legend.includes('certify') || legend.includes('complete and accurate')) {{
                            targetVal = 'yes';
                        }}

                        radios.forEach(r => {{
                            let rText = '';
                            let container = r;
                            for (let c = 0; c < 5; c++) {{
                                if (!container.parentElement) break;
                                container = container.parentElement;
                                if (container.innerText && container.innerText.trim().length > 0 && container.innerText.trim().length < 30) {{
                                    rText = container.innerText.trim().toLowerCase();
                                    break;
                                }}
                            }}
                            if ((rText === targetVal || rText.startsWith(targetVal)) && !r.checked) {{
                                r.click();
                                r.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                logs.push('Radio (' + targetVal + '): ' + legend.substring(0, 25));
                            }}
                        }});
                    }});

                    return {{ logs }};
                }}"""

                fill_res = page.evaluate(fill_js)
                print(f"[PrecisionAQ] Filled: {fill_res.get('logs')}", flush=True)

                # Ensure salary input is also filled directly via Playwright just in case
                try:
                    sal = modal.locator('input[aria-label*="desired salary" i], input[id*="3b"]').first
                    if sal.is_visible(timeout=500):
                        if not sal.input_value():
                            sal.fill("1200000")
                            print("[PrecisionAQ] Backup native filled salary -> 1200000", flush=True)
                except:
                    pass

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
