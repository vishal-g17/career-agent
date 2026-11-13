import sys
import os
import time
from playwright.sync_api import sync_playwright

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/ats-filler"
RESUME_PDF = "/Users/admin/linkedin-multiagent/data/resume.pdf"

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.on("response", lambda r: print(f"API [{r.status}]: {r.url}") if any(k in r.url for k in ["talent", "profile", "onboard"]) else None)
        try:
            print("[Flexiple] Navigating to profile onboarding...")
            page.goto("https://app.flexiple.com/onboarding?step=talent-profile", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # 1. Upload Resume PDF
            print("[Flexiple] Uploading Resume PDF...")
            file_input = page.locator("input[type=\"file\"]")
            file_input.set_input_files(RESUME_PDF)
            page.wait_for_timeout(3000)

            # 2. Phone Number
            print("[Flexiple] Filling Phone Number...")
            phone_input = page.locator("input[type=\"tel\"]")
            phone_input.fill("8368387245")
            page.wait_for_timeout(500)

            # 3. Location
            print("[Flexiple] Selecting Location: Delhi, India...")
            loc_input = page.locator("input[placeholder*=\"Search city\"]")
            loc_input.fill("Delhi")
            page.wait_for_timeout(1500)
            loc_option = page.locator("div:has-text(\"Delhi, India\")").last
            if loc_option.is_visible():
                loc_option.click()
            page.wait_for_timeout(1000)

            # 4. Profession
            print("[Flexiple] Selecting Profession: Business Analyst...")
            prof_input = page.locator("input[placeholder*=\"Search professions\"]")
            prof_input.fill("Business Analyst")
            page.wait_for_timeout(1500)
            prof_option = page.locator("div:has-text(\"Business Analyst\")").last
            if prof_option.is_visible():
                prof_option.click()
            page.wait_for_timeout(1000)

            # 5. Skills
            print("[Flexiple] Adding Skills...")
            skills = ["SQL", "Python", "Agile"]
            for skill in skills:
                try:
                    btn = page.locator("button:has-text(\"Select skills\"), button:has-text(\"Add skill\")").first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(800)
                    
                    search_box = page.locator("input[placeholder=\"Search...\"]")
                    if search_box.is_visible():
                        search_box.fill(skill)
                        page.wait_for_timeout(1000)
                        opt = page.locator(f"[aria-label=\"Suggestions\"] div").filter(has_text=skill).first
                        if opt.is_visible():
                            opt.click()
                            page.wait_for_timeout(500)
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(500)
                except Exception as se:
                    print(f"Skill error: {se}")

            # Ensure popover is closed
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            # 6. Experience (Years = 1, Months = 0)
            print("[Flexiple] Setting Experience: 1 yr 0 mo...")
            exp_yrs = page.locator("input[placeholder*=\"e.g. 5\"]")
            exp_yrs.click()
            exp_yrs.fill("1")
            
            exp_mos = page.locator("input[placeholder*=\"e.g. 6\"]")
            exp_mos.click()
            exp_mos.fill("0")

            # 7. Current Pay (1200000 INR)
            print("[Flexiple] Setting Current Pay: 1200000 INR...")
            pay_input = page.locator("input[placeholder=\"0\"]")
            if pay_input.is_visible():
                pay_input.click()
                pay_input.fill("1200000")

            # 8. Notice Period (15 days)
            print("[Flexiple] Setting Notice Period: 15 days...")
            notice_input = page.locator("input[placeholder*=\"e.g. 30\"]")
            if notice_input.is_visible():
                notice_input.click()
                notice_input.fill("15")

            page.wait_for_timeout(1000)
            page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/flexiple_ready_submit.png")
            print("[Flexiple] Ready to submit. Screenshot taken.")

            # 9. Click Complete Signup
            print("[Flexiple] Clicking Complete signup...")
            submit_btn = page.locator("button:has-text(\"Complete signup\")")
            print("Submit button disabled?", submit_btn.is_disabled())
            submit_btn.click()
            
            # Wait for navigation or response
            page.wait_for_timeout(6000)
            print("[Flexiple] Post-submit URL:", page.url)
            print("[Flexiple] Post-submit Title:", page.title())
            page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/flexiple_final_submitted.png")

        finally:
            page.close()

if __name__ == "__main__":
    main()
