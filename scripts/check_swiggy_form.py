from playwright.sync_api import sync_playwright
import time

endpoint = "http://127.0.0.1:9230/default/swiggy-check"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.contexts[0]
    page = context.new_page()
    url = "https://docs.google.com/forms/d/e/1FAIpQLSf9y9u8AXVSI-aQsGblijjiiYh6VoKp_dq5ahmIu6KJzk-BOg/viewform"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)
    
    print("Page URL:", page.url)
    print("Page Title:", page.title())
    
    # Check for resume attachment
    has_resume = page.locator('div[aria-label*="resume.pdf"], span:has-text("resume.pdf")').count()
    print("Resume attached count:", has_resume)
    
    # Check all questions and if any has validation error
    errors = page.locator('div[role="alert"]').all_inner_texts()
    print("Active validation alerts:", errors)
    
    # Take screenshot
    page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_current_form_state.png", full_page=True)
    print("Saved screenshot to swiggy_current_form_state.png")
    page.close()
