import time
import os
import sqlite3
from playwright.sync_api import sync_playwright

DB_PATH = os.path.expanduser("~/.linkedin_agent.db")
PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"
RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"
if not os.path.exists(RESUME_PDF):
    RESUME_PDF = "/Users/admin/linkedin-multiagent/data/resume.pdf"

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

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/precision-apply-direct")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()

    print("[PrecisionAQ] Loading job page...", flush=True)
    page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)

    # Click Easy Apply
    easy_apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button:has-text("Easy Apply")').first
    if not easy_apply_btn.is_visible(timeout=4000):
        print("Easy Apply not visible, checking if applied...", flush=True)
        exit(0)
    
    easy_apply_btn.click()
    time.sleep(2)
    modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]').first

    # Steps 1 to 3
    for s in range(3):
        print(f"[PrecisionAQ] Passing step {s+1}...", flush=True)
        try:
            f_inp = modal.locator('input[type="file"]').first
            if f_inp.count() > 0:
                f_inp.set_input_files(RESUME_PDF)
        except: pass
        modal.locator('button:has-text("Next")').first.click()
        time.sleep(1.5)

    print("[PrecisionAQ] Now on Step 4 (Additional questions)...", flush=True)
    
    # 1. Fill EVERY single text input safely
    text_inputs = modal.locator('input[type="text"]').all()
    for idx, inp in enumerate(text_inputs):
        try:
            inp_id = inp.get_attribute('id') or ''
            lbl_el = modal.locator(f'label[for="{inp_id}"]').first
            lbl_text = (inp.get_attribute('aria-label') or (lbl_el.inner_text() if lbl_el.count() > 0 else '') or '').lower()
            
            if 'salary' in lbl_text or 'desired' in lbl_text or idx == 1:
                inp.fill("1200000")
                print(f"[PrecisionAQ] Input {idx} (Salary) -> 1200000", flush=True)
            elif 'linkedin' in lbl_text or idx == 0:
                inp.fill("https://www.linkedin.com/in/vishal-gupta-product/")
                print(f"[PrecisionAQ] Input {idx} (LinkedIn) -> URL", flush=True)
            elif 'street' in lbl_text or 'address 1' in lbl_text:
                inp.fill("Sector 3, Rohini")
                print(f"[PrecisionAQ] Input {idx} (Street) -> Sector 3, Rohini", flush=True)
            elif 'city' in lbl_text:
                inp.fill("Delhi")
                print(f"[PrecisionAQ] Input {idx} (City) -> Delhi", flush=True)
            elif 'other' in lbl_text or 'visa' in lbl_text:
                inp.fill("N/A - Citizen")
                print(f"[PrecisionAQ] Input {idx} (Other Visa) -> N/A - Citizen", flush=True)
            else:
                if not inp.input_value():
                    inp.fill("N/A")
                    print(f"[PrecisionAQ] Input {idx} ({lbl_text[:20]}) -> N/A", flush=True)
        except Exception as ie:
            print(f"[PrecisionAQ] Input error on {idx}: {ie}", flush=True)

    # 2. Textareas
    textareas = modal.locator('textarea').all()
    if len(textareas) > 0: textareas[0].fill(SQL_ANSWER)
    if len(textareas) > 1: textareas[1].fill(HEALTHCARE_ANSWER)
    print(f"[PrecisionAQ] Filled {len(textareas)} textareas (SQL & Healthcare)", flush=True)

    # 3. Select dropdowns by index & value
    all_selects = modal.locator('select').all()
    for s_idx, sel in enumerate(all_selects):
        try:
            if s_idx == 0: sel.select_option(value="163081473004") # No (Non-compete)
            elif s_idx == 1: sel.select_option(value="163081475004") # Citizen (Work auth)
            elif s_idx == 2: sel.select_option(value="163081496004") # India (Country)
        except Exception as se:
            print(f"[PrecisionAQ] Select error {s_idx}: {se}", flush=True)
    print(f"[PrecisionAQ] Selected all {len(all_selects)} dropdowns (No, Citizen, India)", flush=True)

    # 4. Radios via JS evaluate
    js_radios = """() => {
        const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
        const fieldsets = modal.querySelectorAll('fieldset');
        fieldsets.forEach(fs => {
            const legend = (fs.querySelector('legend')?.innerText || fs.closest('div')?.querySelector('span, label, p')?.innerText || '').toLowerCase();
            const radios = fs.querySelectorAll('input[type="radio"]');
            let targetVal = 'yes';
            if (legend.includes('sponsor') || legend.includes('immigration')) targetVal = 'no';
            else if (legend.includes('ever worked') || legend.includes('previously worked')) targetVal = 'no';
            else if (legend.includes('ever interviewed') || legend.includes('previously interviewed')) targetVal = 'no';
            else if (legend.includes('authorized to work')) targetVal = 'yes';
            else if (legend.includes('certify') || legend.includes('complete and accurate')) targetVal = 'yes';

            radios.forEach(r => {
                let rText = '';
                let container = r;
                for (let c = 0; c < 5; c++) {
                    if (!container.parentElement) break;
                    container = container.parentElement;
                    if (container.innerText && container.innerText.trim().length > 0 && container.innerText.trim().length < 30) {
                        rText = container.innerText.trim().toLowerCase();
                        break;
                    }
                }
                if (rText === targetVal || rText.startsWith(targetVal)) {
                    r.click();
                    r.dispatchEvent(new Event('change', {bubbles: true}));
                }
            });
        });
    }"""
    page.evaluate(js_radios)
    time.sleep(1)

    print("[PrecisionAQ] Clicking Next/Review on Step 4...", flush=True)
    next_btn = modal.locator('button:has-text("Review"), button:has-text("Next")').first
    next_btn.click()
    time.sleep(2.5)

    # Step 5: Consent checkbox
    print("[PrecisionAQ] Handling Step 5 (Consent checkbox)...", flush=True)
    page.evaluate("() => { const cb = document.querySelector('input[type=\"checkbox\"]'); if (cb && !cb.checked) { cb.click(); cb.dispatchEvent(new Event('change', {bubbles: true})); } }")
    print("[PrecisionAQ] Clicked Step 5 consent box via JS", flush=True)

    time.sleep(1)
    rev_btn = modal.locator('button:has-text("Review"), button:has-text("Next")').first
    if rev_btn.is_visible():
        print("[PrecisionAQ] Clicking Review button on Step 5...", flush=True)
        rev_btn.click()
        time.sleep(2.5)

    submit_btn = modal.locator('button:has-text("Submit application"), button:has-text("Submit")').first
    if submit_btn.is_visible(timeout=3000):
        print("[PrecisionAQ] >>> SUBMITTING APPLICATION TO PRECISION AQ! <<<", flush=True)
        submit_btn.click()
        time.sleep(4)
        page.screenshot(path="data/precision_aq_submitted_final.png")
        print("[PrecisionAQ] Submission complete! Screenshot saved to data/precision_aq_submitted_final.png", flush=True)

        # Update DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%4471378930%'")
        conn.commit()
        conn.close()
        print("[PrecisionAQ] Database updated to status='applied'!", flush=True)
    else:
        print("[PrecisionAQ] Submit button not visible. Taking screenshot...", flush=True)
        page.screenshot(path="data/precision_aq_debug.png")

    page.close()
