from playwright.sync_api import sync_playwright

def request_archive():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/test-download")
        page = b.contexts[0].new_page()
        page.goto("https://www.linkedin.com/mypreferences/d/download-my-data", wait_until="commit", timeout=25000)
        page.wait_for_timeout(3000)
        
        # Select first radio option
        radio = page.locator('input[type="radio"]').first
        radio.click(force=True)
        page.wait_for_timeout(1000)
        
        btn = page.locator('button:has-text("Request archive")').first
        print("Button enabled:", btn.is_enabled())
        if btn.is_enabled():
            btn.click()
            page.wait_for_timeout(2500)
            print("Clicked Request archive!")
            print("Post-click text:", page.locator('main').inner_text()[:400].replace("\n", " "))
            
        page.close()

if __name__ == "__main__":
    request_archive()
