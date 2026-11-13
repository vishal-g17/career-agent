import os
import time
import re
from typing import Dict, Any, List, Optional
from playwright.sync_api import sync_playwright
from .form_answerer import answer_standard_field, answer_open_ended_question, load_candidate_profile
from ..db import database

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/ats-filler"

def fill_ats_application(url: str, role_title: str = "Associate Product Manager", auto_submit: bool = True) -> Dict[str, Any]:
    """
    Autofills company career pages hosted on common ATS platforms:
    Greenhouse, Lever, Ashby, Workday, Taleo, or direct corporate career forms.
    Follows portal redirects (Indeed, Naukri, LinkedIn) if provided,
    uploads Vishal's PDF resume, fills candidate details, answers screening questions,
    and submits autonomously.
    """
    result = {
        "url": url,
        "ats_url": url,
        "ats_type": "generic",
        "fields_filled": [],
        "resume_uploaded": False,
        "submitted": False,
        "confirmed": False,
        "screenshot_path": None,
        "error": None
    }

    profile = load_candidate_profile()
    p = profile.get("personal", {})
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent.parent
    possible_paths = [
        os.environ.get("RESUME_PATH"),
        str(Path.home() / "career-agent" / "Vishal_Gupta_Resume.pdf"),
        str(Path.home() / "career-agent" / "data" / "resume.pdf"),
        str(project_root / "Vishal_Gupta_Resume.pdf"),
        str(project_root / "data" / "resume.pdf"),
        "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf",
        p.get("resume_local_pdf_path")
    ]
    pdf_path = next((path for path in possible_paths if path and os.path.exists(path)), str(project_root / "data" / "resume.pdf"))

    print(f"\n[ATSFiller] Processing application for: {role_title} ({url})")

    with sync_playwright() as p_play:
        target_page = None
        browser = None
        is_local_browser = False
        try:
            try:
                browser = p_play.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=3000)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
            except Exception:
                browser = p_play.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}
                )
                is_local_browser = True

            page = context.new_page()
            target_page = page

            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # --- Check if URL is a portal redirect page that needs to be clicked ---
            current_url = page.url
            if "indeed.com" in current_url:
                print("[ATSFiller] Indeed job detected. Following company site redirect...")
                apply_link = page.locator('#applyButtonLinkContainer a, a[href*="applystart"], a:has-text("Apply on company site"), button:has-text("Apply on company site")').first
                if apply_link.is_visible(timeout=4000):
                    href = apply_link.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            href = "https://in.indeed.com" + href
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3500)
            elif "naukri.com" in current_url:
                print("[ATSFiller] Naukri job detected. Following company site redirect...")
                # Check if expired
                if "expJD=true" in page.url or "/job-listings" not in page.url:
                    result["error"] = "expired"
                    return result

                comp_btn = page.locator(
                    '#company-site-button, button.company-site-button, '
                    'button:has-text("Apply on company site"), a:has-text("Apply on company site")'
                ).first
                if comp_btn.is_visible(timeout=3000):
                    href = comp_btn.get_attribute("href")
                    if href and href.startswith("http") and "naukri.com" not in href:
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)
                    else:
                        try:
                            with context.expect_page(timeout=8000) as new_page_info:
                                comp_btn.click()
                            page.close()
                            page = new_page_info.value
                            target_page = page
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(3000)
                        except Exception:
                            pass
            elif "linkedin.com" in current_url:
                print("[ATSFiller] LinkedIn external apply detected...")
                import urllib.parse
                apply_elem = page.locator('a:has-text("Apply"), button:has-text("Apply"), .jobs-apply-button').first
                if apply_elem.is_visible(timeout=4000):
                    href = apply_elem.get_attribute("href")
                    if href and "safety/go" in href:
                        parsed = urllib.parse.urlparse(href)
                        real_target = urllib.parse.parse_qs(parsed.query).get("url", [None])[0]
                        if real_target:
                            print(f"[ATSFiller] Extracted direct external company URL: {real_target}")
                            page.goto(real_target, wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_timeout(3500)
                    elif href and href.startswith("http") and "linkedin.com" not in href:
                        print(f"[ATSFiller] Following direct company URL: {href}")
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3500)
                    else:
                        try:
                            with context.expect_page(timeout=8000) as new_page_info:
                                apply_elem.click()
                            page.close()
                            page = new_page_info.value
                            target_page = page
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(3000)
                        except:
                            pass

            dest_url = page.url
            result["ats_url"] = dest_url
            print(f"[ATSFiller] Active destination URL: {dest_url}")

            # STRICT GUARD: Never fill inputs on job boards themselves if redirect failed
            import urllib.parse as _up
            dest_host = (_up.urlparse(dest_url).netloc or "").lower()
            if any(portal_host in dest_host for portal_host in ["naukri.com", "indeed.com", "linkedin.com"]):
                print(f"[ATSFiller] Redirect remained on job portal ({dest_host}); refusing to mark applied.")
                result["error"] = f"portal_redirect_not_followed:{dest_host}"
                return result

            # Stop cleanly if gated by CAPTCHA / anti-bot challenge
            captcha_frame = page.locator(
                'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
                'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'
            ).first
            if captcha_frame.count() > 0 and captcha_frame.is_visible():
                print("[ATSFiller] Anti-bot / CAPTCHA gate detected; skipping manual verification portal.")
                result["error"] = "captcha_gate_requires_manual_apply"
                return result

            # Detect ATS platform
            if "greenhouse.io" in dest_url:
                result["ats_type"] = "greenhouse"
            elif "lever.co" in dest_url:
                result["ats_type"] = "lever"
            elif "ashbyhq.com" in dest_url:
                result["ats_type"] = "ashby"
            elif "myworkdayjobs.com" in dest_url or "workday" in dest_url:
                result["ats_type"] = "workday"
            elif "taleo.net" in dest_url or "oraclecloud" in dest_url or "oracle" in dest_url:
                result["ats_type"] = "taleo"
            elif "bamboohr.com" in dest_url:
                result["ats_type"] = "bamboohr"
            elif "smartrecruiters.com" in dest_url:
                result["ats_type"] = "smartrecruiters"
            print(f"[ATSFiller] Detected ATS Platform: {result['ats_type']}")

            # --- ATS PLATFORM GATEWAY & LOGIN HANDLING ---
            if result["ats_type"] == "taleo":
                print("[ATSFiller] Executing Taleo / Oracle Cloud application gateway...")
                # 1. Click Taleo Apply button if on requisition overview
                taleo_apply = page.locator('input[id*="APPLY_ON_REQ"], input[value="Apply"], a:has-text("Apply"), button:has-text("Apply")').first
                if taleo_apply.is_visible(timeout=3000):
                    try:
                        taleo_apply.click()
                        page.wait_for_timeout(3500)
                    except:
                        pass
                
                # 2. Check for Taleo Guest Apply vs Login
                guest_btn = page.locator('input[value*="Apply as Guest" i], input[id*="guestapply"], button:has-text("Apply as Guest")').first
                if guest_btn.is_visible(timeout=2500):
                    print("[ATSFiller] Taleo: Applying as Guest...")
                    guest_btn.click()
                    page.wait_for_timeout(3000)
                    email_inp = page.locator('input[id*="email" i], input[type="text"][name*="email" i], input[type="email"]').first
                    if email_inp.is_visible(timeout=2000):
                        email_inp.fill(p.get("email", "vishal90301@gmail.com"))
                    cont_btn = page.locator('input[id*="niceCommandButton_continue"], input[value*="Continue" i], button:has-text("Continue")').first
                    if cont_btn.is_visible(timeout=2000):
                        cont_btn.click()
                        page.wait_for_timeout(3500)
                elif page.locator('input[id*="login-name"], input[id*="username"], input[type="text"][name*="user" i]').first.is_visible(timeout=2000):
                    print("[ATSFiller] Taleo: Signing in with candidate credentials...")
                    u_inp = page.locator('input[id*="login-name"], input[id*="username"], input[type="text"][name*="user" i]').first
                    u_inp.fill(p.get("email", "vishal90301@gmail.com"))
                    pw_inp = page.locator('input[id*="login-password"], input[type="password"]').first
                    if pw_inp.is_visible():
                        pw_inp.fill("VishalJobs@2026!")
                    sign_cmd = page.locator('input[value="Sign In"], button:has-text("Sign In")').first
                    if sign_cmd.is_visible():
                        sign_cmd.click()
                        page.wait_for_timeout(3500)

            elif result["ats_type"] == "workday":
                print("[ATSFiller] Executing Workday application gateway...")
                # 1. Click initial Workday Apply button
                wd_apply = page.locator('a[data-automation-id="applyButton"], button[data-automation-id="applyButton"], a:has-text("Apply"), button:has-text("Apply")').first
                if wd_apply.is_visible(timeout=3000):
                    try:
                        wd_apply.click()
                        page.wait_for_timeout(3000)
                    except:
                        pass

                # 2. Prefer Autofill with Resume
                wd_autofill = page.locator('a[data-automation-id="autofillWithResume"], button[data-automation-id="autofillWithResume"], a:has-text("Autofill with Resume")').first
                wd_manual = page.locator('a[data-automation-id="applyManually"], button[data-automation-id="applyManually"], a:has-text("Apply Manually")').first
                if wd_autofill.is_visible(timeout=2500):
                    wd_autofill.click()
                    page.wait_for_timeout(3000)
                elif wd_manual.is_visible(timeout=2000):
                    wd_manual.click()
                    page.wait_for_timeout(3000)

                # 3. Workday Sign In / Create Account modal
                create_acc_btn = page.locator('button[data-automation-id="createAccountLink"], a[data-automation-id="createAccountLink"], button:has-text("Create Account")').first
                if create_acc_btn.is_visible(timeout=2000):
                    try:
                        create_acc_btn.click()
                        page.wait_for_timeout(2000)
                    except:
                        pass

                wd_email = page.locator('input[data-automation-id="email"], input[type="email"]').first
                wd_pass = page.locator('input[data-automation-id="password"], input[type="password"]').first
                if wd_email.is_visible(timeout=2000) and wd_pass.is_visible(timeout=2000):
                    print("[ATSFiller] Workday: Filling candidate credentials...")
                    wd_email.fill(p.get("email", "vishal90301@gmail.com"))
                    wd_pass.fill("VishalJobs@2026!")
                    wd_vpass = page.locator('input[data-automation-id="verifyPassword"]').first
                    if wd_vpass.is_visible(timeout=1000):
                        wd_vpass.fill("VishalJobs@2026!")
                    wd_chk = page.locator('input[data-automation-id="createAccountCheckbox"], input[type="checkbox"]').first
                    if wd_chk.is_visible(timeout=1000) and not wd_chk.is_checked():
                        wd_chk.check(force=True)
                    wd_sub = page.locator('button[data-automation-id="createAccountSubmitButton"], button[data-automation-id="signInSubmitButton"], button:has-text("Create Account"), button:has-text("Sign In")').first
                    if wd_sub.is_visible(timeout=2000):
                        wd_sub.click()
                        page.wait_for_timeout(4000)

            # Standard trigger for Greenhouse, Lever, Ashby, BambooHR
            else:
                apply_triggers = page.locator('button:has-text("Apply for this Job"), button:has-text("Apply Now"), a:has-text("Apply Now"), button:has-text("Apply"), a:has-text("Apply for this job")').all()
                for trig in apply_triggers:
                    if trig.is_visible() and trig.is_enabled():
                        try:
                            trig.click()
                            page.wait_for_timeout(2000)
                            break
                        except:
                            pass

            # Dismiss cookie banners if present
            cookie_btn = page.locator('button:has-text("Accept All"), button:has-text("Accept all"), button:has-text("Agree"), button#onetrust-accept-btn-handler').first
            if cookie_btn.is_visible(timeout=1000):
                try:
                    cookie_btn.click(timeout=1000)
                except:
                    pass

            # --- MULTI-STEP FORM FILLING LOOP (Handles 1-page and multi-page flows) ---
            max_steps = 6
            for step_num in range(1, max_steps + 1):
                print(f"[ATSFiller] Processing form step {step_num}...")

                # 1. UPLOAD RESUME FILE
                if not result["resume_uploaded"]:
                    file_inputs = page.locator('input[type="file"]').all()
                    for fi in file_inputs:
                        try:
                            fi.set_input_files(pdf_path)
                            result["resume_uploaded"] = True
                            print(f"[ATSFiller] Resume uploaded successfully: {pdf_path}")
                            page.wait_for_timeout(1500)
                            break
                        except Exception as e:
                            print(f"[ATSFiller] File upload attempt: {e}")

                # 2. FILL STANDARD TEXT INPUTS
                input_elements = page.locator('input[type="text"], input[type="email"], input[type="tel"], input[type="url"], input:not([type])').all()
                for inp in input_elements:
                    try:
                        if not inp.is_visible() or inp.input_value():
                            continue
                        placeholder = inp.get_attribute("placeholder") or ""
                        name_attr = inp.get_attribute("name") or ""
                        id_attr = inp.get_attribute("id") or ""
                        aria_label = inp.get_attribute("aria-label") or ""
                        
                        label_text = ""
                        if id_attr:
                            lbl = page.locator(f'label[for="{id_attr}"]').first
                            if lbl.is_visible():
                                label_text = lbl.inner_text().strip()

                        field_identifier = f"{placeholder} {name_attr} {id_attr} {aria_label} {label_text}".lower()
                        
                        # STRICT RULE: Skip portfolio / website / referral fields
                        if any(w in field_identifier for w in ["portfolio", "website", "blog", "refer", "referral", "referred"]):
                            continue

                        matched_val = answer_standard_field(field_identifier)
                        if matched_val:
                            matched_val = str(matched_val).replace("Magicpin", "a quick-commerce startup").replace("magicpin", "a quick-commerce startup")
                            matched_val = matched_val.replace("Lifelong", "a consumer D2C brand").replace("lifelong", "a consumer D2C brand")
                            inp.fill(str(matched_val))
                            result["fields_filled"].append({"field": field_identifier[:40], "value": matched_val})
                    except Exception:
                        continue

                # 3. FILL TEXTAREAS (Cover letter / Screening questions)
                textareas = page.locator('textarea').all()
                for ta in textareas:
                    try:
                        if not ta.is_visible() or ta.input_value():
                            continue
                        lbl_text = ""
                        ta_id = ta.get_attribute("id") or ""
                        if ta_id:
                            lbl = page.locator(f'label[for="{ta_id}"]').first
                            if lbl.is_visible():
                                lbl_text = lbl.inner_text().strip()
                        ident = f"{ta.get_attribute('name') or ''} {ta.get_attribute('placeholder') or ''} {lbl_text}".lower()

                        if any(w in ident for w in ["cover letter", "note", "why do you want", "summary", "additional"]):
                            pitch = (
                                "B.Tech in Mathematics & Computing (CSE) from NSUT Delhi with 1 year full-time experience across product strategy, "
                                "SQL analytics, and high-velocity workflow automation. Available to join immediately within a 15-day notice period."
                            )
                            ta.fill(pitch)
                            result["fields_filled"].append({"field": ident[:40], "value": "Cover letter pitch"})
                        else:
                            ans = answer_open_ended_question(lbl_text or ident)
                            ta.fill(ans)
                            result["fields_filled"].append({"field": ident[:40], "value": ans[:30]})
                    except Exception:
                        continue

                # 4. HANDLE RADIO BUTTONS & CHECKBOXES
                try:
                    for r_no in page.locator('input[type="radio"][value="false"], input[type="radio"][value*="no" i], input[type="radio"][id*="sponsorship" i]').all():
                        r_no.check(force=True)
                except:
                    pass

                radios = page.locator('input[type="radio"], input[type="checkbox"]').all()
                for r in radios:
                    try:
                        r_id = r.get_attribute("id") or ""
                        r_val = (r.get_attribute("value") or "").lower()
                        lbl = page.locator(f'label[for="{r_id}"]').first if r_id else None
                        lbl_text = lbl.inner_text().lower() if (lbl and lbl.is_visible()) else ""
                        
                        # Work authorization -> YES
                        if "authorized to work" in lbl_text or ("legally" in lbl_text and "work" in lbl_text):
                            if not r.is_checked() and ("yes" in lbl_text or r_val in ["yes", "true", "1"]):
                                r.check(force=True)
                        # Visa sponsorship required -> NO
                        elif "sponsorship" in lbl_text or "require visa" in lbl_text or "visa" in lbl_text:
                            if not r.is_checked() and ("no" in lbl_text or r_val in ["no", "false", "0"]):
                                r.check(force=True)
                        # Immediate joiner / relocation / available -> YES
                        elif "immediate" in lbl_text or "relocate" in lbl_text or "available" in lbl_text:
                            if not r.is_checked() and ("yes" in lbl_text or r_val in ["yes", "true", "1"]):
                                r.check(force=True)
                    except Exception:
                        continue

                # 5. HANDLE SELECT DROPDOWNS
                for sel in page.locator('select').all():
                    try:
                        if not sel.is_visible():
                            continue
                        options = [sel.locator(f'option:nth-child({idx+1})').inner_text().strip() for idx in range(min(15, sel.locator('option').count()))]
                        for opt in options:
                            opt_l = opt.lower()
                            if any(k in opt_l for k in ["india", "delhi", "b.tech", "full-time", "full time"]):
                                sel.select_option(label=opt)
                                break
                            elif any(k in opt_l for k in ["immediate", "15 days", "15"]):
                                sel.select_option(label=opt)
                                break
                            elif opt_l in ["yes", "authorized"]:
                                sel.select_option(label=opt)
                                break
                    except:
                        pass

                # Check if multi-page "Next" or "Save and Continue" exists
                next_btn = page.locator(
                    'button[data-automation-id="bottom-navigation-next-button"], input[id*="SaveAndContinue"], '
                    'button:has-text("Save and Continue"), button:has-text("Save & Continue"), button:has-text("Next"), '
                    'input[value*="Save and Continue" i], input[value*="Next" i]'
                ).first

                # Check if final Submit button is visible right now
                submit_btn = page.locator(
                    'button[data-automation-id="bottom-navigation-submit-button"], button[type="submit"], input[type="submit"], '
                    'button:has-text("Submit Application"), button:has-text("Submit application"), button:has-text("Submit"), input[value*="Submit" i]'
                ).first

                if next_btn.is_visible(timeout=1500) and not submit_btn.is_visible(timeout=1000):
                    print(f"[ATSFiller] Advancing to next step...")
                    next_btn.click()
                    page.wait_for_timeout(3500)
                else:
                    break

            # 6. SUBMIT FORM IF REQUESTED & STRICTLY VERIFY RECEIPT
            data_dir = project_root / "data"
            os.makedirs(data_dir, exist_ok=True)

            if auto_submit and (result["resume_uploaded"] or len(result["fields_filled"]) >= 2):
                modal = page.locator('.modal.show, div[role="dialog"], dialog, #careerAddModal').first
                scope = modal if modal.is_visible(timeout=1000) else page

                pre_submit_url = page.url.lower()
                try:
                    pre_submit_text = page.locator('body').inner_text().lower()
                except Exception:
                    pre_submit_text = ""

                # Find genuine application submit buttons (never navbar search / login buttons)
                candidate_btns = scope.locator(
                    'button[data-automation-id="bottom-navigation-submit-button"], '
                    'button:has-text("Submit Application"), button:has-text("Submit application"), '
                    'button:has-text("Send Application"), button:has-text("Complete Application"), '
                    'button:has-text("Submit"), input[type="submit"], button[type="submit"]'
                ).all()

                forbidden_btn_words = ("search", "sign in", "log in", "login", "register", "create account", "filter", "subscribe", "cookie")
                allowed_btn_words = ("submit", "send application", "complete application", "apply for this job", "finish")
                chosen_submit = None
                for btn in candidate_btns:
                    try:
                        if not btn.is_visible() or not btn.is_enabled():
                            continue
                        btn_label = ((btn.inner_text() or "") + " " + (btn.get_attribute("value") or "") + " " + (btn.get_attribute("aria-label") or "")).strip().lower()
                        if any(fw in btn_label for fw in forbidden_btn_words):
                            continue
                        if any(aw in btn_label for aw in allowed_btn_words):
                            chosen_submit = btn
                            break
                    except Exception:
                        continue

                if chosen_submit is not None:
                    chosen_submit.click(force=True)
                    page.wait_for_timeout(5000)
                    result["submitted"] = True
                    print(f"[ATSFiller] Clicked Submit for {role_title}; verifying confirmation...")

                    post_url = page.url.lower()
                    try:
                        post_text = page.locator('body').inner_text().lower()
                    except Exception:
                        post_text = ""

                    # Check for validation errors blocking submission
                    error_phrases = [
                        "this field is required",
                        "please fill out this field",
                        "please complete all required",
                        "cannot be blank",
                        "please correct the errors",
                        "invalid email",
                        "invalid phone",
                    ]
                    has_new_validation_error = any(
                        ep in post_text and ep not in pre_submit_text for ep in error_phrases
                    )
                    try:
                        invalid_count = scope.locator('[aria-invalid="true"]:visible').count()
                    except Exception:
                        invalid_count = 0

                    confirmation_phrases = [
                        "thank you for applying",
                        "thanks for applying",
                        "application has been submitted",
                        "application submitted",
                        "your application was submitted",
                        "received your application",
                        "application has been received",
                        "we have received your application",
                        "your application has been sent",
                        "successfully submitted your application",
                        "successfully applied",
                    ]
                    has_new_confirmation_text = any(
                        cp in post_text and cp not in pre_submit_text for cp in confirmation_phrases
                    )
                    url_confirmed = (
                        post_url != pre_submit_url
                        and any(u in post_url for u in ["/confirmation", "/thank-you", "/thankyou", "/submitted", "application-submitted", "applied=true", "success"])
                    )

                    if not has_new_validation_error and invalid_count == 0 and (has_new_confirmation_text or url_confirmed):
                        result["confirmed"] = True
                        print(f"[ATSFiller] Submission CONFIRMED on page for {role_title}!")
                    else:
                        result["confirmed"] = False
                        result["error"] = "unconfirmed_after_submit"
                        print(
                            f"[ATSFiller] Submit clicked for {role_title}, but NO confirmation receipt detected "
                            f"(validation_err={has_new_validation_error}, invalid_fields={invalid_count}). Not marking applied."
                        )
                else:
                    result["error"] = "no_valid_submit_button"
                    print(f"[ATSFiller] No valid application submit button found for {role_title}.")
            else:
                result["error"] = "insufficient_form_fields_or_login_wall"

            # 7. TAKE POST-ACTION VERIFICATION SCREENSHOT
            screenshot_path = str(data_dir / f"ats_{int(time.time())}.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                result["screenshot_path"] = screenshot_path
                print(f"[ATSFiller] Screenshot saved: {screenshot_path}")
            except Exception:
                pass

        except Exception as e:
            print(f"[ATSFiller] Exception during ATS fill: {e}")
            result["error"] = str(e)
        finally:
            if target_page:
                try:
                    target_page.close()
                except:
                    pass
            if browser and is_local_browser:
                try:
                    browser.close()
                except:
                    pass

    return result
