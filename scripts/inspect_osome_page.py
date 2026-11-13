import time
from playwright.sync_api import sync_playwright

OSOME_URL = "https://in.linkedin.com/jobs/view/product-analyst-intelligence-at-osome-4489456767"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/osome-inspect")
    context = browser.contexts[0]
    page = context.new_page()
    try:
        print(f"[OsomeInspect] Navigating to {OSOME_URL}...")
        page.goto(OSOME_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        print("[OsomeInspect] Checking apply button...")
        apply_btn = page.locator('button.jobs-apply-button, a.jobs-apply-button, button:has-text("Apply")').first
        if apply_btn.is_visible(timeout=5000):
            print(f"[OsomeInspect] Found apply button: {apply_btn.inner_text().strip()}")
            
            # Catch new page/tab or redirection
            with context.expect_page(timeout=10000) as new_page_info:
                apply_btn.click(force=True)
            
            new_page = new_page_info.value
            new_page.wait_for_load_state("domcontentloaded", timeout=20000)
            time.sleep(3)
            print(f"[OsomeInspect] Redirected to ATS URL: {new_page.url}")
            print(f"[OsomeInspect] Destination Page Title: {new_page.title()}")
            new_page.close()
        else:
            print("[OsomeInspect] No apply button found. Current URL:", page.url)
    except Exception as e:
        print(f"[OsomeInspect] Error: {e}")
    finally:
        page.close()
