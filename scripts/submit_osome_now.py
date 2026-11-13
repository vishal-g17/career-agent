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

print(f"[Osome] Starting application to Osome (Product Analyst - Intelligence)...", flush=True)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/osome-apply-v2")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(OSOME_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)

    # Dismiss cookie banner
    try:
        page.evaluate("""() => {
            const btn = document.querySelector('button[id*="accept"], button:has-text("Accept all cookies")');
            if (btn) btn.click();
            const banner = document.querySelector('#hs-eu-cookie-confirmation, .cookie-banner, [aria-label*="cookie"]');
            if (banner) banner.remove();
        }""")
    except:
        pass

    # Click apply if button visible
    apply_btn = page.locator('a:has-text("Apply for this job"), button:has-text("Apply for this job")').first
    if apply_btn.is_visible():
        apply_btn.click()
        time.sleep(1.5)

    # Fill personal details
    print("[Osome] Filling candidate personal details...", flush=True)
    page.locator('input[name*="[first_name]"]').first.fill("Vishal")
    page.locator('input[name*="[last_name]"]').first.fill("Gupta")
    page.locator('input[name*="[email]"]').first.fill("vishal.gupta.ug22@nsut.ac.in")
    
    phone_inp = page.locator('input[name*="[phone]"]').first
    if phone_inp.is_visible():
        phone_inp.fill("+919030112345")

    loc_inp = page.locator('input[name*="[location]"]').first
    if loc_inp.is_visible():
        loc_inp.fill("Delhi, India")

    # Upload resume
    file_inp = page.locator('input[type="file"]').first
    if file_inp.count() > 0:
        print(f"[Osome] Uploading CV: {RESUME_PDF}...", flush=True)
        file_inp.set_input_files(RESUME_PDF)
        time.sleep(2)

    # Answering questions
    print("[Osome] Answering all screening questions...", flush=True)
    all_inputs = page.locator('input[name*="answers_attributes"], textarea[name*="answers_attributes"]').all()
    for inp in all_inputs:
        lbl_text = (inp.get_attribute("aria-label") or inp.get_attribute("placeholder") or "").lower()
        inp_id = inp.get_attribute("id") or ""
        lbl_el = page.locator(f'label[for="{inp_id}"]').first
        if lbl_el.count() > 0:
            lbl_text = lbl_el.inner_text().lower()

        if "notice" in lbl_text:
            inp.fill("15 days")
            print("Answered Notice Period -> 15 days", flush=True)
        elif "current" in lbl_text and "salary" in lbl_text:
            inp.fill("10 LPA")
            print("Answered Current Salary -> 10 LPA", flush=True)
        elif "expected" in lbl_text and "salary" in lbl_text:
            inp.fill("14 LPA")
            print("Answered Expected Salary -> 14 LPA", flush=True)
        elif "legal" in lbl_text or "authorized" in lbl_text or "eligible" in lbl_text:
            inp.fill("Yes, Indian Citizen")
            print("Answered Legal Auth -> Yes, Indian Citizen", flush=True)
        elif "sql" in lbl_text or "bigquery" in lbl_text or "dbt" in lbl_text or inp.get_attribute("tagName") == "TEXTAREA":
            inp.fill(SQL_BQ_DBT_ANSWER)
            print("Answered SQL/BigQuery/dbt -> Detailed technical response", flush=True)
        else:
            if not inp.input_value():
                inp.fill(SQL_BQ_DBT_ANSWER)
                print(f"Fallback filled question ({lbl_text[:20]}) -> SQL response", flush=True)

    # Check consent via JS
    page.evaluate("() => { const cb = document.getElementById('candidate_consent_given'); if (cb && !cb.checked) { cb.click(); cb.dispatchEvent(new Event('change', {bubbles: true})); } }")
    print("Checked consent box via JS", flush=True)

    # Remove any cookie overlays before screenshot/submit
    page.evaluate("""() => {
        document.querySelectorAll('[class*="cookie"], [id*="cookie"]').forEach(el => el.remove());
    }""")

    time.sleep(1)
    page.screenshot(path="data/osome_ready_to_submit.png")
    print("Pre-submission screenshot saved to data/osome_ready_to_submit.png", flush=True)

    # Submit application
    submit_btn = page.locator('input[type="submit"][value*="Submit"], button:has-text("Submit application")').first
    if submit_btn.is_visible():
        print("[Osome] Clicking 'Submit application'...", flush=True)
        submit_btn.click()
        time.sleep(5)
        page.screenshot(path="data/osome_submitted.png")
        print("[Osome] Submission complete! Screenshot saved to data/osome_submitted.png", flush=True)

        # Update DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE jobs SET status='applied' WHERE url LIKE '%osome%' OR title LIKE '%Osome%'")
        conn.commit()
        conn.close()
        print("[Osome] Database updated to status='applied'!", flush=True)
    else:
        print("[Osome] Submit button not visible.", flush=True)

    page.close()
