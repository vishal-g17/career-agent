import time
from typing import Dict, Any, Optional
from playwright.sync_api import sync_playwright

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/linkedin-publisher"

def publish_post_to_linkedin(content: str) -> Dict[str, Any]:
    """
    Connects to Ridealong Chrome session over CDP and publishes content to LinkedIn.
    Uses real authenticated browser session with zero bot-detection risk.
    """
    print("[Publisher] Connecting to Ridealong Chrome via CDP...")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        try:
            print("[Publisher] Opening LinkedIn feed...")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # Check if logged in
            if "login" in page.url or "checkpoint" in page.url:
                raise RuntimeError("LinkedIn session expired or requires verification. Open Ridealong to check.")

            print("[Publisher] Locating 'Start a post' input...")
            # Click the post trigger button
            post_trigger_selectors = [
                "button.share-box-feed-entry__trigger",
                "button:has-text('Start a post')",
                ".share-box-feed-entry__trigger"
            ]

            clicked = False
            for sel in post_trigger_selectors:
                try:
                    if page.is_visible(sel, timeout=3000):
                        page.click(sel)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # Fallback: click directly on the share box container
                page.click("div.share-box-feed-entry__top-bar", timeout=5000)

            time.sleep(2)

            print("[Publisher] Typing post content...")
            # Locate the text editor inside the modal
            editor_sel = "div[role='textbox'][contenteditable='true']"
            page.wait_for_selector(editor_sel, timeout=10000)
            page.click(editor_sel)
            
            # Fill content into editor
            page.keyboard.insert_text(content)
            time.sleep(2)

            print("[Publisher] Clicking 'Post' button...")
            post_btn_selector = "button.share-actions__primary-action, button:has-text('Post')"
            page.wait_for_selector(post_btn_selector, timeout=10000)
            
            # Click the post button to publish
            page.click(post_btn_selector)
            time.sleep(5)

            print("[Publisher] Post published successfully!")
            return {
                "status": "success",
                "message": "Post published live to LinkedIn via Ridealong",
                "timestamp": time.time()
            }

        except Exception as e:
            print(f"[Publisher] Publishing failed: {e}")
            raise
        finally:
            page.close()

if __name__ == "__main__":
    print("Testing CDP publisher readiness...")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        print("CDP Connection successful! Page title:", browser.contexts[0].pages[0].title() if browser.contexts[0].pages else "Ready")
