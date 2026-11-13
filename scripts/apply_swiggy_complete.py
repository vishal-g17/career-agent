import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf9y9u8AXVSI-aQsGblijjiiYh6VoKp_dq5ahmIu6KJzk-BOg/viewform"
ENDPOINT = "http://127.0.0.1:9230/default/swiggy-complete"
RESUME_PATH = "/Users/admin/linkedin-multiagent/data/resume.pdf"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(ENDPOINT)
    context = browser.contexts[0]
    page = context.new_page()
    
    print(f"Opening Swiggy Form: {FORM_URL}")
    page.goto(FORM_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2500)
    
    containers = page.locator('div[jsmodel="CP1oW"]').all()
    print(f"Found {len(containers)} question containers.")
    
    # 0. Name
    print("1. Filling Name: Vishal Gupta")
    containers[0].locator('input[type="text"]').first.fill("Vishal Gupta")
    page.wait_for_timeout(200)

    # 1. Email
    print("2. Filling Email: vishal90301@gmail.com")
    containers[1].locator('input[type="email"]').first.fill("vishal90301@gmail.com")
    page.wait_for_timeout(200)

    # 2. Phone
    print("3. Filling Phone: 8368387245")
    containers[2].locator('input[type="text"]').first.fill("8368387245")
    page.wait_for_timeout(200)

    # 3. Position (Checkbox)
    print("4. Selecting Business Analyst checkbox...")
    ba_cb = containers[3].locator('div[role="checkbox"]').first
    if ba_cb.get_attribute("aria-checked") != "true":
        containers[3].locator('span:text-is("Business Analyst"), label:has-text("Business Analyst")').first.click(force=True)
        page.wait_for_timeout(300)
    if ba_cb.get_attribute("aria-checked") != "true":
        ba_cb.click(force=True)
        page.wait_for_timeout(300)
    print("   Business Analyst checked:", ba_cb.get_attribute("aria-checked"))

    # 4. Current Company
    print("5. Filling Current Company: Magicpin")
    containers[4].locator('input[type="text"]').first.fill("Magicpin")
    page.wait_for_timeout(200)

    # 5. Experience (Radio)
    print("6. Selecting Experience: 1-3 years...")
    containers[5].locator('span:text-is("1-3"), div[role="radio"][data-value="1-3"]').first.click(force=True)
    page.wait_for_timeout(200)

    # 6. Current CTC
    print("7. Filling Current CTC: 12 LPA (10 LPA Fixed)")
    containers[6].locator('input[type="text"]').first.fill("12 LPA (10 LPA Fixed)")
    page.wait_for_timeout(200)

    # 7. Expected CTC
    print("8. Filling Expected CTC: 15 - 18 LPA")
    containers[7].locator('input[type="text"]').first.fill("15 - 18 LPA")
    page.wait_for_timeout(200)

    # 8. Bangalore (Checkbox)
    print("9. Selecting Currently in Bangalore: No...")
    blr_no_cb = containers[8].locator('div[role="checkbox"]').last
    if blr_no_cb.get_attribute("aria-checked") != "true":
        containers[8].locator('span:text-is("No"), label:has-text("No")').first.click(force=True)
        page.wait_for_timeout(300)
    if blr_no_cb.get_attribute("aria-checked") != "true":
        blr_no_cb.click(force=True)
        page.wait_for_timeout(300)
    print("   Bangalore No checked:", blr_no_cb.get_attribute("aria-checked"))

    # 9. Current Designation
    print("10. Filling Designation: Tech Product Analyst")
    containers[9].locator('input[type="text"]').first.fill("Tech Product Analyst")
    page.wait_for_timeout(200)

    # 10. Grad Year
    print("11. Filling Grad Year: 2026")
    containers[10].locator('input[type="text"]').first.fill("2026")
    page.wait_for_timeout(200)

    # 11. Gender (Checkbox)
    print("12. Selecting Gender: Male...")
    male_cb = containers[11].locator('div[role="checkbox"]').first
    if male_cb.get_attribute("aria-checked") != "true":
        containers[11].locator('span:text-is("Male"), label:has-text("Male")').first.click(force=True)
        page.wait_for_timeout(300)
    if male_cb.get_attribute("aria-checked") != "true":
        male_cb.click(force=True)
        page.wait_for_timeout(300)
    print("   Gender Male checked:", male_cb.get_attribute("aria-checked"))

    # 12. Resume upload check
    print("13. Checking resume attachment...")
    attached = page.locator('div[aria-label*="resume.pdf"], span:has-text("resume.pdf")').count() > 0
    if attached:
        print("   ✅ resume.pdf is ALREADY attached!")
    else:
        print("   Uploading resume.pdf via Google Picker...")
        add_btn = containers[12].locator('div[role="button"]:has-text("Add File"), div[role="button"]:has-text("Add file")').first
        if add_btn.is_visible():
            add_btn.click()
            page.wait_for_timeout(3000)
            picker_frames = [f for f in page.frames if "picker" in f.url]
            if picker_frames:
                picker_frames[0].locator('input[type="file"]').first.set_input_files(RESUME_PATH)
                print("   File set, waiting 5s for upload...")
                page.wait_for_timeout(5000)

    # Verify no alerts before submitting
    alerts = page.locator('div[role="alert"]:visible').all_inner_texts()
    print("Visible alerts before submit:", alerts)

    # Screenshot before submit
    ready_img = "/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_all_filled_verified.png"
    page.screenshot(path=ready_img, full_page=True)
    print(f"Saved filled verification screenshot to {ready_img}")

    # Click Submit
    print("\nClicking Submit button...")
    submit_btn = page.locator('div[role="button"]:has-text("Submit"), span:text-is("Submit")').last
    submit_btn.click(force=True)
    page.wait_for_timeout(5000)

    print("Post-submit URL:", page.url)
    body_snippet = page.inner_text("body")[:400]
    print("Body snippet:\n", body_snippet)

    # Post-submit screenshot
    success_img = "/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_submitted_response.png"
    page.screenshot(path=success_img)
    print(f"Saved submitted response screenshot to {success_img}")

    if "response has been recorded" in body_snippet.lower() or "submit another response" in body_snippet.lower():
        print("🎉 SUCCESS! Swiggy Application officially submitted!")
    else:
        print("Checking if further action needed.")

    page.close()
