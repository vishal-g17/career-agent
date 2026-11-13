from playwright.sync_api import sync_playwright
import time

ENDPOINT = "http://127.0.0.1:9230/default/ritika-connect"
RITIKA_URL = "https://www.linkedin.com/in/ritika-tyagi-579256146/"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(ENDPOINT)
    context = browser.contexts[0]
    page = context.new_page()
    
    print(f"Opening Ritika Tyagi profile: {RITIKA_URL}")
    page.goto(RITIKA_URL, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(2500)
    
    # Dismiss chat overlays
    page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')
    
    # Find Connect button
    connect_btn = page.locator('main button:has-text("Connect")').first
    if not connect_btn.is_visible():
        more_btn = page.locator('main button:has-text("More")').first
        if more_btn.is_visible():
            more_btn.click()
            page.wait_for_timeout(800)
            connect_btn = page.locator('div[role="menuitem"]:has-text("Connect"), span:text-is("Connect")').first

    if connect_btn.is_visible():
        print("Clicking Connect button...")
        connect_btn.click()
        page.wait_for_timeout(1500)
        
        # Check for 'Add a note'
        add_note = page.locator('button:has-text("Add a note")').first
        if add_note.is_visible():
            add_note.click()
            page.wait_for_timeout(800)
            note_box = page.locator('textarea[name="message"], textarea#custom-message, textarea').first
            note_text = (
                "Hi Ritika, saw your post regarding the Business Analyst opening at Swiggy! "
                "I bring 1 yr exp in product analytics, SQL telemetry & conversion funnels, and just applied via the form. "
                "Would love to connect!"
            )
            note_box.click()
            note_box.press_sequentially(note_text, delay=20)
            page.wait_for_timeout(600)
            
            send_btn = page.locator('button[aria-label*="Send"], button:has-text("Send")').last
            print("Send button enabled:", send_btn.is_enabled())
            if send_btn.is_enabled():
                send_btn.click()
                page.wait_for_timeout(2000)
                print("🎉 Successfully sent personalized connection request to Ritika Tyagi!")
        else:
            send_btn = page.locator('button:has-text("Send")').first
            if send_btn.is_visible() and send_btn.is_enabled():
                send_btn.click()
                page.wait_for_timeout(1500)
                print("🎉 Successfully sent connection request to Ritika Tyagi!")
    else:
        print("Connect button not found or already pending.")

    page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/ritika_profile_status.png")
    page.close()
