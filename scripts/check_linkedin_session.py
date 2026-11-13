from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/check-login-state", timeout=10000)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        
        print("Page URL:", page.url)
        print("Page Title:", page.title())
        
        # Check login elements
        is_login = "login" in page.url or "checkpoint" in page.url or page.locator('input#username, input#session_key, button:has-text("Sign in")').count() > 0
        print("Is login or checkpoint visible?", is_login)
        
        # Take full page screenshot
        screenshot_path = "/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/linkedin_login_check.png"
        page.screenshot(path=screenshot_path)
        print("Screenshot saved to:", screenshot_path)
    except Exception as e:
        print("Error checking session:", e)
