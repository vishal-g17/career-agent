import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf9y9u8AXVSI-aQsGblijjiiYh6VoKp_dq5ahmIu6KJzk-BOg/viewform"
ENDPOINT = "http://127.0.0.1:9230/default/swiggy-final-runner"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(ENDPOINT)
    context = browser.contexts[0]
    page = context.new_page()
    
    print(f"Opening Swiggy Form: {FORM_URL}")
    page.goto(FORM_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    
    # 1. Click Business Analyst checkbox
    print("Checking 'Business Analyst'...")
    # Find checkbox for Business Analyst
    ba_el = page.locator('span:text-is("Business Analyst"), div[data-value="Business Analyst"]').first
    ba_el.click(force=True)
    page.wait_for_timeout(500)
    
    # Check if aria-checked is true
    ba_checkbox = page.locator('div[role="checkbox"][aria-label="Business Analyst"], label:has-text("Business Analyst") div[role="checkbox"]').first
    print("Business Analyst checked state:", ba_checkbox.get_attribute("aria-checked"))
    if ba_checkbox.get_attribute("aria-checked") != "true":
        print("Clicking checkbox directly...")
        ba_checkbox.click(force=True)
        page.wait_for_timeout(500)
        print("After second click:", ba_checkbox.get_attribute("aria-checked"))

    # 2. Click 'No' for Bangalore checkbox
    print("Checking 'No' for Bangalore question...")
    # The Bangalore question container
    blr_container = page.locator('div[jsmodel="CP1oW"]:has-text("Bangalore")').first
    no_el = blr_container.locator('span:text-is("No"), label:has-text("No")').first
    no_el.click(force=True)
    page.wait_for_timeout(500)
    no_cb = blr_container.locator('div[role="checkbox"]').last
    print("Bangalore 'No' checked state:", no_cb.get_attribute("aria-checked"))
    if no_cb.get_attribute("aria-checked") != "true":
        no_cb.click(force=True)
        page.wait_for_timeout(500)
        print("After second click:", no_cb.get_attribute("aria-checked"))

    # 3. Click 'Male' for Gender question
    print("Checking 'Male' for Gender question...")
    gender_container = page.locator('div[jsmodel="CP1oW"]:has-text("Gender")').first
    male_el = gender_container.locator('span:text-is("Male"), label:has-text("Male")').first
    male_el.click(force=True)
    page.wait_for_timeout(500)
    male_cb = gender_container.locator('div[role="checkbox"]').first
    print("Gender 'Male' checked state:", male_cb.get_attribute("aria-checked"))
    if male_cb.get_attribute("aria-checked") != "true":
        male_cb.click(force=True)
        page.wait_for_timeout(500)
        print("After second click:", male_cb.get_attribute("aria-checked"))

    # Take screenshot before submitting
    page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_ready_to_submit.png", full_page=True)
    print("Saved ready-to-submit screenshot.")

    # 4. Click Submit
    print("Submitting form...")
    submit_btn = page.locator('div[role="button"]:has-text("Submit"), span:text-is("Submit")').last
    submit_btn.click(force=True)
    page.wait_for_timeout(5000)

    print("Post-submit URL:", page.url)
    page_text = page.inner_text("body")
    print("Page text snippet:", page_text[:300])

    # Take confirmation screenshot
    page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_final_success.png")
    print("Saved final confirmation screenshot to swiggy_final_success.png")

    page.close()
