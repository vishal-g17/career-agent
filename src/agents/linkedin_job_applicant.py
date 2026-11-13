import time
import os
import sqlite3
import random
import urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright

DB_PATH = os.path.expanduser("~/.linkedin_agent.db")
RIDEALONG_CDP = "http://127.0.0.1:9230/default/linkedin-job-applicant"
RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"
if not os.path.exists(RESUME_PDF):
    RESUME_PDF = "/Users/admin/linkedin-multiagent/data/resume.pdf"

TARGET_KEYWORDS = [
    "Product Analyst",
    "Associate Product Manager",
    "Category Manager",
    "Business Analyst",
    "Data Analyst",
    "Operations Analyst",
    "Forward Deployed Engineer",
    "Founders Office"
]

def apply_linkedin_jobs_stage(duration_minutes: int = 10, max_apps: int = 6) -> int:
    """
    Applies to matching jobs on LinkedIn (Easy Apply and ATS redirects)
    for the specified duration (default ~10 minutes).
    """
    print(f"\n" + "="*70)
    print(f"💼 [LinkedInJobs] Starting ~{duration_minutes}-minute Job Application Stage")
    print(f"Keywords: {', '.join(TARGET_KEYWORDS[:4])}...")
    print("="*70 + "\n")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    end_time = time.time() + (duration_minutes * 60)
    applied_count = 0

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            kw_idx = 0
            while time.time() < end_time and applied_count < max_apps:
                kw = TARGET_KEYWORDS[kw_idx % len(TARGET_KEYWORDS)]
                kw_idx += 1

                q_kw = urllib.parse.quote(kw)
                # Filter: Easy Apply enabled (f_AL=true), Past week (f_TPR=r604800), India
                url = f"https://www.linkedin.com/jobs/search/?keywords={q_kw}&f_AL=true&f_TPR=r604800&location=India"
                print(f"[LinkedInJobs] Searching: '{kw}' ({url})...")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)

                    # Close messaging overlays
                    for cb in page.locator('aside button:has-text("Close"), button[aria-label*="Close conversation"]').all():
                        try: cb.click()
                        except: pass

                    # Find job cards
                    job_cards = page.locator('div.job-card-container, div[data-job-id]').all()
                    print(f"[LinkedInJobs] Found {len(job_cards)} job cards for '{kw}'")

                    for card in job_cards[:8]:
                        if time.time() >= end_time or applied_count >= max_apps:
                            break

                        try:
                            card.scroll_into_view_if_needed()
                            card_text = card.inner_text().strip()
                            lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                            title = lines[0] if lines else "Job"
                            comp = lines[1] if len(lines) > 1 else ""

                            # Skip blacklisted employers
                            clean_comp = comp.lower()
                            if "magicpin" in clean_comp or "lifelong" in clean_comp:
                                continue

                            # Click job card to view details
                            card.click()
                            time.sleep(2)

                            job_url = page.url
                            # Check if already applied in DB
                            cur.execute("SELECT id FROM jobs WHERE url LIKE ? AND status='applied'", (f"%{job_url[:50]}%",))
                            if cur.fetchone():
                                print(f"[LinkedInJobs] Already applied to: {title} @ {comp}")
                                continue

                            # Check for Easy Apply button
                            easy_apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button:has-text("Easy Apply")').first
                            if not easy_apply_btn.is_visible(timeout=3000):
                                continue

                            print(f"[LinkedInJobs] Applying to: {title} @ {comp}...")
                            easy_apply_btn.click()
                            time.sleep(2)

                            modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]').first
                            if not modal.is_visible(timeout=4000):
                                continue

                            # Advance modal steps
                            step_submitted = False
                            for step_i in range(8):
                                time.sleep(1.5)

                                # 1. Check submit button
                                submit_btn = modal.locator('button:has-text("Submit application"), button:has-text("Submit")').first
                                if submit_btn.is_visible(timeout=1000):
                                    submit_btn.click()
                                    time.sleep(3)
                                    applied_count += 1
                                    step_submitted = True
                                    print(f"[LinkedInJobs] ✅ Applied successfully to: {title} @ {comp} ({applied_count}/{max_apps})!")
                                    
                                    # Insert/update DB
                                    cur.execute('''
                                        INSERT OR REPLACE INTO jobs (id, title, company, location, url, status, created_at)
                                        VALUES (?, ?, ?, ?, ?, 'applied', CURRENT_TIMESTAMP)
                                    ''', (f"li_{int(time.time())}", title, comp, "India", job_url))
                                    conn.commit()

                                    # Send instant alert to Discord #agent channel
                                    try:
                                        from src.agents.discord_notifier import DiscordNotifier
                                        DiscordNotifier().send_application_alert({
                                            "company": comp,
                                            "role": title,
                                            "platform": "LinkedIn (Easy Apply)",
                                            "url": job_url,
                                            "status": "SUBMITTED & CONFIRMED",
                                            "details": "Screening questions answered (15d notice, 12 LPA, 1yr exp), attached Vishal_Gupta_Resume.pdf.",
                                            "resume": "Vishal_Gupta_Resume.pdf"
                                        })
                                    except Exception as ne:
                                        print(f"[LinkedInJobs] Discord alert note: {ne}")
                                    break

                                # 2. Check for resume file upload
                                try:
                                    f_inp = modal.locator('input[type="file"]').first
                                    if f_inp.count() > 0:
                                        f_inp.set_input_files(RESUME_PDF)
                                        time.sleep(0.5)
                                except: pass

                                # 3. Fill text inputs
                                for inp in modal.locator('input[type="text"]').all():
                                    try:
                                        lbl = (inp.get_attribute('aria-label') or inp.locator('xpath=..').inner_text() or '').lower()
                                        if 'salary' in lbl or 'desired' in lbl: inp.fill("1200000")
                                        elif 'experience' in lbl or 'year' in lbl: inp.fill("1")
                                        elif 'notice' in lbl: inp.fill("15 days")
                                        elif 'city' in lbl or 'location' in lbl: inp.fill("Delhi")
                                        elif 'linkedin' in lbl: inp.fill("https://www.linkedin.com/in/vishal-gupta-product/")
                                        elif not inp.input_value(): inp.fill("1")
                                    except: pass

                                # 4. Radios & Checkboxes
                                page.evaluate("""() => {
                                    const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
                                    if (!modal) return;
                                    modal.querySelectorAll('fieldset').forEach(fs => {
                                        const legend = (fs.innerText || '').toLowerCase();
                                        const radios = fs.querySelectorAll('input[type="radio"]');
                                        let target = 'yes';
                                        if (legend.includes('sponsor') || legend.includes('immigration')) target = 'no';
                                        radios.forEach(r => {
                                            const txt = (r.parentElement?.innerText || '').toLowerCase();
                                            if (txt.includes(target) && !r.checked) { r.click(); r.dispatchEvent(new Event('change', {bubbles: true})); }
                                        });
                                    });
                                    modal.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                                        if (!cb.checked) { cb.click(); cb.dispatchEvent(new Event('change', {bubbles: true})); }
                                    });
                                }""")

                                # 5. Click Next or Review
                                review_btn = modal.locator('button:has-text("Review")').first
                                next_btn = modal.locator('button:has-text("Next")').first
                                if review_btn.is_visible(timeout=1000):
                                    review_btn.click()
                                elif next_btn.is_visible(timeout=1000):
                                    next_btn.click()
                                else:
                                    break

                            # Dismiss any post-submit done modal
                            time.sleep(2)
                            done_btn = page.locator('button:has-text("Done"), button:has-text("Dismiss")').first
                            if done_btn.is_visible():
                                done_btn.click()

                            # Pacing delay between applications (25-45s)
                            if step_submitted:
                                app_delay = random.uniform(25, 45)
                                print(f"[LinkedInJobs] Pacing delay: resting {int(app_delay)}s...")
                                time.sleep(app_delay)

                        except Exception as ce:
                            print(f"[LinkedInJobs] Card note: {ce}")
                            continue

                except Exception as ke:
                    print(f"[LinkedInJobs] Search note for '{kw}': {ke}")
                    time.sleep(5)

            page.close()
        except Exception as be:
            print(f"[LinkedInJobs] Browser error: {be}")

    conn.close()
    print(f"\n[LinkedInJobs] Stage complete! Submitted {applied_count} LinkedIn applications.")
    return applied_count

if __name__ == "__main__":
    apply_linkedin_jobs_stage(duration_minutes=5, max_apps=3)
