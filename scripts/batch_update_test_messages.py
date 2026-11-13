import time
from playwright.sync_api import sync_playwright

RECIPIENTS = [
    'nimisha-bansal-bb6579174',
    'shafiquegajdhar',
    'ekanshbansal10',
    'tejastendulkar',
    'himanshu-kishor-35b398176',
    'bkolleri',
    'gaurav-pal-23681288',
    'parassipani',
    'shiv-parashar'
]

CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"
PDF_PATH = "/Users/admin/linkedin-multiagent/data/resume.pdf"
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/fix-test-batch"

def fix_recipient(page, username: str):
    url = f"https://www.linkedin.com/messaging/compose/?recipient={username}"
    print(f"\n[FixBatch] Processing {username}...")
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(2500)

    # 1. Edit existing message if it contains portfolio link
    try:
        last_body = page.locator('.msg-s-event-listitem__body').last
        if last_body.is_visible():
            txt = last_body.inner_text()
            if 'vishalgupta.vercel.app' in txt:
                last_body.hover()
                page.wait_for_timeout(300)
                page.locator('.msg-s-event-listitem__options-trigger').last.click()
                page.wait_for_timeout(600)

                edit_item = page.locator('.artdeco-dropdown__item:has-text("Edit")').first
                if edit_item.is_visible():
                    edit_item.click()
                    page.wait_for_timeout(1000)

                    edit_box = page.locator('div[contenteditable="true"]').first
                    if edit_box.is_visible():
                        cur = edit_box.inner_text()
                        new_txt = cur.replace('https://vishalgupta.vercel.app/', CV_LINK).replace('share my portfolio', 'share my CV')
                        edit_box.fill(new_txt)
                        page.wait_for_timeout(400)
                        save_btn = page.locator('button:has-text("Save")').first
                        if save_btn.is_visible():
                            save_btn.click()
                            page.wait_for_timeout(1500)
                            print(f"[FixBatch] -> Edited message for {username} to CV link!")
    except Exception as e:
        print(f"[FixBatch] Warning during edit for {username}: {e}")

    # 2. Attach CV PDF as well
    try:
        file_input = page.locator('input.msg-form__attachment-upload-input[accept*=".pdf"], .msg-form input[type="file"]').last
        if file_input.count() > 0:
            file_input.set_input_files(PDF_PATH)
            page.wait_for_timeout(2000)
            
            # Send the attachment
            send_btn = page.locator('button.msg-form__send-button, button:has-text("Send")').first
            if send_btn.is_visible() and send_btn.is_enabled():
                send_btn.click()
                page.wait_for_timeout(2000)
                print(f"[FixBatch] -> Dispatched PDF CV attachment to {username}!")
    except Exception as fe:
        print(f"[FixBatch] PDF attach note for {username}: {fe}")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        for user in RECIPIENTS:
            try:
                fix_recipient(page, user)
                time.sleep(3)
            except Exception as e:
                print(f"[FixBatch] Error processing {user}: {e}")

        page.close()
    print("\n[FixBatch] Finished updating all test recipients!")

if __name__ == "__main__":
    main()
