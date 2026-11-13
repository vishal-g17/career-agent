import sys
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/contact-scraper-test"

def test_scrape_contact(username: str):
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()
        try:
            url = f"https://www.linkedin.com/in/{username}/overlay/contact-info/"
            print(f"Navigating to {url}...")
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            # Check email
            email_link = page.locator('a[href^="mailto:"]').first
            if email_link.is_visible(timeout=4000):
                email = email_link.inner_text().strip()
                print(f"✅ Found Email for {username}: {email}")
                return email
            else:
                # Check all text inside contact info modal
                modal = page.locator('div[role="dialog"], .artdeco-modal').first
                if modal.is_visible():
                    txt = modal.inner_text()
                    print(f"Modal text for {username}:\n{txt}")
                else:
                    print(f"No contact info modal visible for {username}")
                return ""
        finally:
            page.close()
            browser.close()

if __name__ == "__main__":
    test_uname = sys.argv[1] if len(sys.argv) > 1 else "vedpp"
    test_scrape_contact(test_uname)
