import time
import os
from playwright.sync_api import sync_playwright

url = "https://forms.gle/XgmDcfhztFAuB2218"
endpoint = "http://127.0.0.1:9230/default/gform-tpm-submit"
pdf_path = "/Users/admin/linkedin-multiagent/data/resume.pdf"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.contexts[0]
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    
    items = page.locator('div[role="listitem"]').all()
    print(f"Total list items: {len(items)}")

    field_map = {
        "name": "Vishal Gupta",
        "email": "vishal90301@gmail.com",
        "mobile": "+91 8368387245",
        "experience": "1 year",
        "current salary (fixed)": "10 LPA",
        "current salary (variable)": "2 LPA",
        "impressive thing": "Architected automated data and reporting pipelines in Python and Playwright that eliminated 4 hours of daily manual workflows, and optimized merchant onboarding funnels reducing drop-offs from 12% to 4% at a high-growth startup.",
        "expected salary (fixed)": "15 LPA",
        "expected salary (variable)": "2 LPA",
        "how soon": "Immediate / 15 Days (Serving notice period)",
        "linkedin": "https://www.linkedin.com/in/vshal-g007/"
    }

    for idx, item in enumerate(items):
        heading = item.locator('div[role="heading"]').first
        h_text = heading.inner_text().strip().lower() if heading.count() > 0 else ""
        print(f"\nProcessing item {idx}: {h_text[:40]}")

        # Check for resume upload
        if "resume" in h_text:
            add_file_btn = item.locator('div[role="button"]:has-text("Add file")').first
            if add_file_btn.is_visible():
                print("Clicking Add file button...")
                add_file_btn.click()
                page.wait_for_timeout(3000)
                try:
                    picker = page.frame_locator('iframe[src*="picker"]')
                    file_input = picker.locator('input[type="file"]')
                    file_input.set_input_files(pdf_path)
                    print(f"Attached resume PDF: {pdf_path}")
                    page.wait_for_timeout(4000)
                except Exception as fe:
                    print(f"Picker error: {fe}")
            continue

        # Check inputs
        text_input = item.locator('input[type="text"], input[type="email"], input[type="tel"]').first
        textarea_input = item.locator('textarea').first

        target_val = None
        for key, val in field_map.items():
            if key in h_text:
                target_val = val
                break

        if target_val:
            if text_input.is_visible():
                text_input.fill(target_val)
                print(f"Filled text input: {target_val[:30]}...")
            elif textarea_input.is_visible():
                textarea_input.fill(target_val)
                print(f"Filled textarea: {target_val[:30]}...")
        else:
            print(f"No match for heading: {h_text}")

    page.wait_for_timeout(2000)

    # Submit
    submit_btn = page.locator('div[role="button"]:has-text("Submit"), span:has-text("Submit")').first
    if submit_btn.is_visible():
        print("Clicking Submit button...")
        submit_btn.click()
        page.wait_for_timeout(5000)
        print("Confirmation text:")
        print(page.locator('body').inner_text()[:300])

    page.close()
