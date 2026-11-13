import time
from playwright.sync_api import sync_playwright

url = "https://forms.gle/XgmDcfhztFAuB2218"
endpoint = "http://127.0.0.1:9230/default/keemakr-submit-all"
pdf_path = "/Users/admin/linkedin-multiagent/data/resume.pdf"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.contexts[0]
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    
    print("Page Title:", page.title())

    # 1. Name
    page.locator('div[role="listitem"]:has-text("Name") input').first.fill("Vishal Gupta")
    print("Filled Name")

    # 2. Email
    page.locator('div[role="listitem"]:has-text("Email") input').first.fill("vishal90301@gmail.com")
    print("Filled Email")

    # 3. Mobile
    page.locator('div[role="listitem"]:has-text("Mobile") input').first.fill("+91 8368387245")
    print("Filled Mobile")

    # 4. Experience (Radio: 0 to 4 years)
    exp_radio = page.locator('div[role="radio"]:has-text("0 to 4 years")').first
    if exp_radio.is_visible():
        exp_radio.click()
        print("Clicked 0 to 4 years")

    # 5. Current Salary (Fixed) - Numeric
    page.locator('div[role="listitem"]:has-text("Current Salary (Fixed)") input').first.fill("1000000")
    print("Filled Current Salary (Fixed)")

    # 6. Current Salary (Variable) - Numeric
    page.locator('div[role="listitem"]:has-text("Current Salary (Variable)") input').first.fill("200000")
    print("Filled Current Salary (Variable)")

    # 7. Impressive thing - Textarea (STRICT NO Magicpin/Lifelong)
    story = "Architected automated data and reporting pipelines in Python and Playwright that eliminated 4 hours of daily manual workflows, and optimized merchant onboarding funnels reducing drop-offs from 12% to 4% at a high-growth startup."
    textarea = page.locator('div[role="listitem"]:has-text("impressive thing") textarea').first
    if textarea.is_visible():
        textarea.fill(story)
        print("Filled impressive thing")

    # 8. Expected Salary (Fixed) - Numeric
    page.locator('div[role="listitem"]:has-text("Expected Salary (Fixed)") input').first.fill("1500000")
    print("Filled Expected Salary (Fixed)")

    # 9. Expected Salary (Variable) - Numeric
    page.locator('div[role="listitem"]:has-text("Expected Salary (Variable)") input').first.fill("200000")
    print("Filled Expected Salary (Variable)")

    # 10. How soon you can join
    page.locator('div[role="listitem"]:has-text("How soon") input').first.fill("Immediate / 15 Days (Serving notice period)")
    print("Filled How soon")

    # 11. Resume Upload
    # Check if already attached
    attached = page.locator('div[role="listitem"]:has-text("resume") div:has-text("resume.pdf")').first
    if attached.count() > 0 and attached.is_visible():
        print("Resume already attached!")
    else:
        add_file = page.locator('div[role="listitem"]:has-text("resume") div[role="button"]:has-text("Add file")').first
        if add_file.is_visible():
            print("Clicking Add file...")
            add_file.click()
            page.wait_for_timeout(3000)
            picker = page.frame_locator('iframe[src*="picker"]')
            picker.locator('input[type="file"]').set_input_files(pdf_path)
            print(f"Attached file: {pdf_path}")
            page.wait_for_timeout(4000)

    # 12. LinkedIn
    page.locator('div[role="listitem"]:has-text("linkedIn") input').first.fill("https://www.linkedin.com/in/vshal-g007/")
    print("Filled LinkedIn")

    page.wait_for_timeout(2000)

    # Submit
    submit_btn = page.locator('div[jsname="M2UYVd"]').first
    print("Clicking Submit button...")
    submit_btn.click()
    page.wait_for_timeout(5000)

    print("Post-submit URL:", page.url)
    print("Confirmation page text:")
    print(page.locator('body').inner_text()[:400])

    page.close()
