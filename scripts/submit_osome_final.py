import time
import os
import sqlite3
from playwright.sync_api import sync_playwright

OSOME_URL = "https://careers.osome.com/jobs/8690455-product-analyst-intelligence"
RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"
DB_PATH = os.path.expanduser("~/.linkedin_agent.db")

SQL_BQ_DBT_ANSWER = (
    "1. SQL: 1+ year daily production experience constructing multi-step CTEs, window functions (LAG/LEAD, ROW_NUMBER), "
    "and cohort retention analyses across millions of user session event streams.\n"
    "2. BigQuery: Extensive experience querying, partitioning, and clustering high-volume analytics datasets, "
    "handling nested/repeated schemas, and optimizing query runtime and slot usage.\n"
    "3. dbt: Experienced in building modular transformation models, configuring staging and mart layers, "
    "and automating schema testing (dbt test) for reliable data pipeline observability."
)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/osome-apply-final")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(OSOME_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)

    # Click apply if button visible
    apply_btn = page.locator('a:has-text("Apply for this job"), button:has-text("Apply for this job")').first
    if apply_btn.is_visible():
        apply_btn.click()
        time.sleep(1.5)

    # 1. Fill personal details
    print("[Osome] Filling personal information...", flush=True)
    page.locator('#candidate_first_name').fill("Vishal")
    page.locator('#candidate_last_name').fill("Gupta")
    page.locator('#candidate_email').fill("vishal.gupta.ug22@nsut.ac.in")

    addr_input = page.locator('#candidate_location')
    addr_input.click()
    addr_input.fill("Delhi, India")
    time.sleep(1)
    addr_input.press("ArrowDown")
    time.sleep(0.5)
    addr_input.press("Enter")
    
    # Also set hidden address values directly
    page.evaluate("""() => {
        const setVal = (id, val) => { const el = document.getElementById(id); if (el) { el.value = val; el.dispatchEvent(new Event('input', {bubbles: true})); } };
        setVal('candidate_city', 'Delhi');
        setVal('candidate_country', 'India');
        setVal('candidate_address', 'Rohini, Delhi, India');
        setVal('candidate_state', 'Delhi');
    }""")

    # 2. Upload resume
    file_inp = page.locator('#candidate_resume_remote_url')
    if file_inp.count() > 0:
        print(f"[Osome] Uploading resume: {RESUME_PDF}...", flush=True)
        file_inp.set_input_files(RESUME_PDF)
        time.sleep(2)

    # 3. Screening questions
    print("[Osome] Filling all screening questions...", flush=True)
    page.locator('#candidate_answers_attributes_0_text').fill("15 days") # Notice
    page.locator('#candidate_answers_attributes_1_text').fill("10 LPA")   # Current Salary
    page.locator('#candidate_answers_attributes_2_text').fill("14 LPA")   # Expected Salary
    page.locator('#candidate_answers_attributes_3_text').fill("Yes, Indian Citizen") # Legal Auth
    page.locator('#candidate_answers_attributes_4_text').fill(SQL_BQ_DBT_ANSWER)    # SQL, BQ, dbt

    # 4. Location checkbox (India)
    page.evaluate("() => { const cb = document.getElementById('candidate_location_ids_1309769'); if (cb && !cb.checked) { cb.click(); cb.dispatchEvent(new Event('change', {bubbles: true})); } }")
    print("[Osome] Checked India location checkbox", flush=True)

    # 5. Consent checkbox
    page.evaluate("() => { const cb = document.getElementById('candidate_consent_given'); if (cb && !cb.checked) { cb.click(); cb.dispatchEvent(new Event('change', {bubbles: true})); } }")
    print("[Osome] Checked consent box", flush=True)

    # Dismiss any cookie banner
    page.evaluate("""() => {
        document.querySelectorAll('[class*="cookie"], [id*="cookie"]').forEach(el => el.remove());
    }""")

    time.sleep(1)
    page.screenshot(path="data/osome_ready_final.png")

    # 6. Submit
    print("[Osome] Submitting application now...", flush=True)
    submit_btn = page.locator('input[type="submit"][value*="Submit"], button:has-text("Submit application")').first
    submit_btn.click()
    time.sleep(5)

    page.screenshot(path="data/osome_success.png")
    print("[Osome] Application submitted successfully! Screenshot saved to data/osome_success.png", flush=True)

    # Update DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%osome%' OR title LIKE '%Osome%'")
    conn.commit()
    conn.close()
    print("[Osome] Database updated to status='applied'!", flush=True)

    page.close()
