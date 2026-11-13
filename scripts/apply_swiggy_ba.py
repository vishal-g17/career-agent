import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/swiggy-applier"
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf9y9u8AXVSI-aQsGblijjiiYh6VoKp_dq5ahmIu6KJzk-BOg/viewform"
RESUME_PATH = "/Users/admin/linkedin-multiagent/data/resume.pdf"
CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"

def apply_swiggy_google_form():
    print("\n=======================================================")
    print("🚀 APPLYING TO SWIGGY BUSINESS ANALYST (GOOGLE FORM)")
    print("=======================================================\n")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        print(f"Navigating to {FORM_URL}...")
        page.goto(FORM_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)

        containers = page.locator('div[jsmodel="CP1oW"]').all()
        print(f"Found {len(containers)} question containers.")

        # 0. Name
        print("1. Filling Name: Vishal Gupta")
        containers[0].locator('input[type="text"]').first.fill("Vishal Gupta")
        page.wait_for_timeout(300)

        # 1. Email
        print("2. Filling Email: vishal90301@gmail.com")
        containers[1].locator('input[type="email"]').first.fill("vishal90301@gmail.com")
        page.wait_for_timeout(300)

        # 2. Phone number
        print("3. Filling Phone: 8368387245")
        containers[2].locator('input[type="text"]').first.fill("8368387245")
        page.wait_for_timeout(300)

        # 3. Which position(s) are you interested in?
        print("4. Selecting Position: Business Analyst")
        ba_cb = containers[3].locator('div[role="checkbox"]:has-text("Business Analyst")').first
        if not ba_cb.is_visible():
            ba_cb = containers[3].locator('div[role="checkbox"]').first
        ba_cb.click()
        page.wait_for_timeout(400)

        # 4. Current company?
        print("5. Filling Current Company: Magicpin")
        containers[4].locator('input[type="text"]').first.fill("Magicpin")
        page.wait_for_timeout(300)

        # 5. Total years of experience in Analytics?
        print("6. Selecting Experience: 1-3 years")
        exp_radio = containers[5].locator('div[role="radio"][data-value="1-3"]').first
        if exp_radio.is_visible():
            exp_radio.click()
        else:
            containers[5].locator('div[role="radio"]:has-text("1-3")').first.click()
        page.wait_for_timeout(400)

        # 6. Current CTC
        print("7. Filling Current CTC: 12 LPA (10 LPA Fixed)")
        containers[6].locator('input[type="text"]').first.fill("12 LPA (10 LPA Fixed)")
        page.wait_for_timeout(300)

        # 7. Expected CTC
        print("8. Filling Expected CTC: 15 - 18 LPA")
        containers[7].locator('input[type="text"]').first.fill("15 - 18 LPA")
        page.wait_for_timeout(300)

        # 8. Are you currently in Bangalore?
        print("9. Selecting Currently in Bangalore: No (Immediate joiner, ready to relocate)")
        blr_no = containers[8].locator('div[role="checkbox"]:has-text("No")').first
        if not blr_no.is_visible():
            blr_no = containers[8].locator('div[role="checkbox"]').last
        blr_no.click()
        page.wait_for_timeout(400)

        # 9. Current designation
        print("10. Filling Designation: Tech Product Analyst")
        containers[9].locator('input[type="text"]').first.fill("Tech Product Analyst")
        page.wait_for_timeout(300)

        # 10. Year of Completion of Highest Qualification
        print("11. Filling Graduation Year: 2026 (NSUT Delhi - B.Tech)")
        containers[10].locator('input[type="text"]').first.fill("2026")
        page.wait_for_timeout(300)

        # 11. Gender
        print("12. Selecting Gender: Male")
        gender_male = containers[11].locator('div[role="checkbox"]:has-text("Male")').first
        if not gender_male.is_visible():
            gender_male = containers[11].locator('div[role="checkbox"]').first
        gender_male.click()
        page.wait_for_timeout(400)

        # 12. Submit your resume
        print("13. Checking / Uploading resume.pdf...")
        resume_already_attached = page.locator('div[aria-label*="resume.pdf"], span:has-text("resume.pdf")').first.is_visible()
        if resume_already_attached:
            print("  ✅ resume.pdf is ALREADY attached and ready!")
        else:
            add_file_btn = page.locator('div[role="button"][aria-label*="Add File"], div[role="button"]:has-text("Add File"), div[role="button"]:has-text("Add file")').first
            if add_file_btn.is_visible():
                add_file_btn.click()
                page.wait_for_timeout(3500)
                picker_frames = [f for f in page.frames if "picker" in f.url]
                if picker_frames:
                    pf = picker_frames[0]
                    file_input = pf.locator('input[type="file"]').first
                    file_input.set_input_files(RESUME_PATH)
                    print("  Uploaded resume.pdf, waiting 5s for upload completion...")
                    page.wait_for_timeout(5000)
                else:
                    print("  Warning: Picker frame not found!")

        page.wait_for_timeout(1000)

        # Take pre-submit screenshot
        pre_screenshot = "/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_form_filled.png"
        page.screenshot(path=pre_screenshot, full_page=True)
        print(f"Pre-submit screenshot saved to: {pre_screenshot}")

        # Submit form
        print("\nClicking Submit button...")
        submit_btn = page.locator('div[role="button"]:has-text("Submit"), span:has-text("Submit")').last
        submit_btn.click()
        page.wait_for_timeout(5000)

        # Check post-submit confirmation
        post_screenshot = "/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/swiggy_form_confirmed.png"
        page.screenshot(path=post_screenshot)
        print(f"Post-submit screenshot saved to: {post_screenshot}")

        body_text = page.inner_text("body")
        if "response has been recorded" in body_text.lower() or "submit another response" in body_text.lower():
            print("🎉 SUCCESS! Swiggy Business Analyst application successfully submitted!")
        else:
            print("Form submission status check:\n", body_text[:400])

        page.close()

def reach_out_swiggy_stakeholders():
    print("\n=======================================================")
    print("🤝 REACHING OUT TO SWIGGY RECRUITER & INTERNAL POCs")
    print("=======================================================\n")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        context = browser.contexts[0]
        page = context.new_page()

        # 1. Ritika Tyagi (Talent Acquisition @ Swiggy - The Poster)
        ritika_url = "https://www.linkedin.com/in/ritika-tyagi-579256146/"
        print(f"1. Sending connection request to Recruiter: Ritika Tyagi...")
        try:
            page.goto(ritika_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2500)
            page.evaluate('() => document.querySelectorAll(".msg-overlay-container, .msg-overlay-list-bubble").forEach(el => el.remove())')

            connect_btn = page.locator('main button:has-text("Connect")').first
            if not connect_btn.is_visible():
                more_btn = page.locator('main button:has-text("More")').first
                if more_btn.is_visible():
                    more_btn.click()
                    page.wait_for_timeout(800)
                    connect_btn = page.locator('div[role="menuitem"]:has-text("Connect"), span:text-is("Connect")').first

            if connect_btn.is_visible():
                connect_btn.click()
                page.wait_for_timeout(1500)
                add_note = page.locator('button:has-text("Add a note")').first
                if add_note.is_visible():
                    add_note.click()
                    page.wait_for_timeout(600)
                    note_text = (
                        "Hi Ritika, saw your post regarding the Business Analyst opening at Swiggy! "
                        "I bring 1 yr exp in product analytics, SQL telemetry & conversion funnels, and just applied via the form. "
                        "Would love to connect!"
                    )[:200]
                    note_box = page.locator('textarea').first
                    note_box.fill(note_text)
                    page.wait_for_timeout(400)
                    send_btn = page.locator('button[aria-label*="Send"], button:has-text("Send")').last
                    send_btn.click()
                    page.wait_for_timeout(2000)
                    print("  ✅ Sent connection request + note to Ritika Tyagi!")
                else:
                    send_btn = page.locator('button:has-text("Send")').first
                    if send_btn.is_visible():
                        send_btn.click()
                        print("  ✅ Sent connection request to Ritika Tyagi!")
            else:
                print("  Note: Connect button not found on Ritika Tyagi's profile.")
        except Exception as e:
            print(f"  Error on Ritika Tyagi: {e}")

        # 2. Aditya Dubey (Swiggy | Instamart | Insights & Analytics - 1st Degree Connection)
        aditya_url = "https://www.linkedin.com/messaging/compose/?recipient=aditya-dubey-72665420a"
        print("\n2. Reaching out directly to 1st-Degree Connection: Aditya Dubey (Insights & Analytics @ Swiggy/Instamart)...")
        try:
            page.goto(aditya_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)

            composer = page.locator('div[role="textbox"], .msg-form__contenteditable').first
            if composer.is_visible():
                # Attach CV
                file_input = page.locator('input.msg-form__attachment-upload-input[accept*=".pdf"], .msg-form input[type="file"]').last
                try:
                    if file_input.count() > 0:
                        file_input.set_input_files(RESUME_PATH)
                        page.wait_for_timeout(2000)
                        print("  Attached CV PDF for Aditya")
                except:
                    pass

                msg = (
                    "Hey Aditya, hope you're doing well!\n\n"
                    "Saw Ritika Tyagi's update that Swiggy Analytics is expanding for Business Analysts in Bengaluru. "
                    "I bring 1 yr of full-time experience in product & analytics (SQL telemetry, merchant conversion, automated pipelines) and just submitted my application via the form.\n\n"
                    "Sharing my updated CV here (Drive link + attached PDF below). In case your team or peers have open slots, would deeply appreciate a referral or pointer to the hiring manager!\n"
                    f"CV: {CV_LINK}"
                )
                composer.click()
                page.keyboard.insert_text(msg)
                page.wait_for_timeout(800)
                send_btn = page.locator('button.msg-form__send-button, button:has-text("Send")').first
                if send_btn.is_visible() and send_btn.is_enabled():
                    send_btn.click()
                    page.wait_for_timeout(2000)
                    print("  ✅ Sent referral note + attached CV to Aditya Dubey!")
        except Exception as e:
            print(f"  Error reaching Aditya: {e}")

        page.close()

if __name__ == "__main__":
    apply_swiggy_google_form()
    reach_out_swiggy_stakeholders()
