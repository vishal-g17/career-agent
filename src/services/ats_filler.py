import email as _email_pkg
import imaplib
import json
import os
import re
import time
import urllib.parse as _up
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

from .. import config  # loads .env natively
from ..db import database
from .form_answerer import (
    answer_open_ended_question,
    answer_standard_field,
    load_candidate_profile,
)


def _load_local_env() -> None:
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_local_env()


def _has_interactive_captcha(page) -> bool:
    """
    Detects active, interactive anti-bot / CAPTCHA challenges while ignoring
    passive invisible score badges (e.g. reCAPTCHA Enterprise size=invisible
    footer badge or hidden hCaptcha #frame=enclave telemetry frame).
    Strictly follows safety guidelines: never interacts with or bypasses CAPTCHAs.
    """
    try:
        return bool(
            page.evaluate(
                """() => {
                    const bodyText = (document.body && document.body.innerText || '').toLowerCase();
                    if (
                        bodyText.includes('verify you are human') ||
                        bodyText.includes('checking if the site connection is secure') ||
                        document.querySelector('#challenge-running, #cf-challenge-running')
                    ) {
                        return true;
                    }
                    const iframes = Array.from(document.querySelectorAll('iframe'));
                    for (const f of iframes) {
                        const src = (f.getAttribute('src') || '').toLowerCase();
                        if (!src) continue;
                        const rect = f.getBoundingClientRect();
                        const style = window.getComputedStyle(f);
                        const visible = (
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            parseFloat(style.opacity || '1') > 0.05 &&
                            rect.width > 40 &&
                            rect.height > 40
                        );
                        if (!visible) continue;

                        // Cloudflare Turnstile / Challenge or DataDome
                        if (
                            src.includes('challenges.cloudflare.com') ||
                            src.includes('turnstile') ||
                            src.includes('captcha-delivery.com')
                        ) {
                            return true;
                        }
                        // Google reCAPTCHA: ignore passive size=invisible badge unless bframe challenge popup is open
                        if (src.includes('recaptcha')) {
                            if (src.includes('bframe') && rect.width > 150 && rect.height > 150) {
                                return true;
                            }
                            if (src.includes('anchor') && !src.includes('size=invisible')) {
                                return true;
                            }
                        }
                        // hCaptcha: ignore passive #frame=enclave unless checkbox or challenge popup is visible
                        if (src.includes('hcaptcha')) {
                            if (src.includes('frame=challenge') && rect.width > 150 && rect.height > 150) {
                                return true;
                            }
                            if (src.includes('frame=checkbox') && rect.width > 100 && rect.height > 45) {
                                return true;
                            }
                        }
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _fetch_greenhouse_email_code(min_epoch: float, timeout_sec: int = 28) -> Optional[str]:
    """
    Reads the candidate's own Gmail inbox (vishal90301@gmail.com) via IMAP to
    retrieve Greenhouse's 8-character candidate email ownership verification code.
    """
    gmail_addr = (os.environ.get("GMAIL_ADDRESS") or "").strip()
    gmail_pass = (os.environ.get("GMAIL_APP_PASSWORD") or "").strip()
    if not gmail_addr or not gmail_pass:
        return None

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(4.0)
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
            mail.login(gmail_addr, gmail_pass)
            mail.select("INBOX")
            status, data = mail.search(None, '(FROM "greenhouse")')
            if status == "OK" and data and data[0]:
                msg_ids = data[0].split()[-6:]
                for mid in reversed(msg_ids):
                    st, msg_data = mail.fetch(mid, "(RFC822)")
                    if st != "OK" or not msg_data or not msg_data[0]:
                        continue
                    msg = _email_pkg.message_from_bytes(msg_data[0][1])
                    date_hdr = msg.get("Date")
                    if date_hdr:
                        try:
                            msg_ts = parsedate_to_datetime(date_hdr).timestamp()
                            if msg_ts < min_epoch:
                                continue
                        except Exception:
                            pass

                    subj_parts = decode_header(msg.get("Subject") or "")
                    subj = "".join(
                        (p.decode(enc or "utf-8", errors="ignore") if isinstance(p, bytes) else str(p))
                        for p, enc in subj_parts
                    )

                    body_txt = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            if ctype in ("text/plain", "text/html"):
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body_txt += "\n" + payload.decode("utf-8", errors="ignore")
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body_txt = payload.decode("utf-8", errors="ignore")

                    clean_txt = re.sub(r"<[^>]+>", " ", f"{subj}\n{body_txt}")
                    clean_txt = re.sub(r"\s+", " ", clean_txt)

                    # Match Greenhouse 8-char code right after 'paste this code' / 'security code' / 'application:'
                    m = re.search(
                        r"(?:paste this code|security code|verification code)[^A-Za-z0-9]{1,80}\b([A-Za-z0-9]{8})\b",
                        clean_txt,
                        re.IGNORECASE,
                    )
                    if not m:
                        # Fallback: look for standalone 8-char mixed alphanumeric token
                        for cand in re.findall(r"\b([A-Za-z0-9]{8})\b", clean_txt):
                            if any(c.isdigit() for c in cand) and any(c.isalpha() for c in cand):
                                m = re.match(r"([A-Za-z0-9]{8})", cand)
                                break
                    if m:
                        code = m.group(1)
                        mail.logout()
                        return code
            mail.logout()
        except Exception as e:
            print(f"[ATSFiller] IMAP code check notice: {e}")
    return None


def _extract_question_context(el) -> str:
    """
    Extracts full label + ancestor question prompt for inputs, textareas, selects,
    comboboxes, and radios across Ashby, Lever, Greenhouse, Workday, and generic HTML forms.
    """
    try:
        return (
            el.evaluate(
                """(node) => {
                    const parts = [];
                    if (node.getAttribute('placeholder')) parts.push(node.getAttribute('placeholder'));
                    if (node.getAttribute('name')) parts.push(node.getAttribute('name'));
                    if (node.getAttribute('id')) parts.push(node.getAttribute('id'));
                    if (node.getAttribute('aria-label')) parts.push(node.getAttribute('aria-label'));
                    const labelledBy = node.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        for (const lid of labelledBy.split(/\\s+/)) {
                            const lel = document.getElementById(lid);
                            if (lel && lel.innerText) parts.push(lel.innerText.trim());
                        }
                    }
                    if (node.labels && node.labels.length > 0) {
                        for (const l of node.labels) {
                            if (l.innerText) parts.push(l.innerText.trim());
                        }
                    }
                    const container = node.closest(
                        'li.application-question, .ashby-application-form-field-entry, ' +
                        '.select__container, .text-input-wrapper, .field, .form-group, ' +
                        'fieldset, [role="group"], [role="radiogroup"], .question'
                    );
                    if (container) {
                        const qEl = container.querySelector(
                            '.text, legend, label, [class*="label" i], [class*="title" i], [class*="prompt" i]'
                        );
                        if (qEl && qEl.innerText) {
                            parts.push(qEl.innerText.trim());
                        } else if (container.innerText) {
                            parts.push(container.innerText.trim().slice(0, 220));
                        }
                    }
                    return parts.join(' ').replace(/\\s+/g, ' ').trim();
                }"""
            )
            or ""
        )
    except Exception:
        return ""


def _scrub_employer_names(val: str) -> str:
    val = re.sub(r"(?i)magicpin", "a quick-commerce startup", str(val))
    val = re.sub(r"(?i)lifelong", "a consumer D2C brand", val)
    return val


def _decide_yes_no_or_option(question_text: str, option_labels: List[str]) -> Optional[str]:
    """
    Given a question prompt and candidate option labels, picks the best matching option
    for Vishal Gupta (1 yr exp, India work-authorized, no sponsorship needed, 15-day notice, NSUT B.Tech).
    """
    q = question_text.lower()
    if not option_labels:
        return None

    # Experience ranges
    if any(k in q for k in ["experience", "years", "exp"]) and "export" not in q and "expenses" not in q:
        for opt in option_labels:
            ol = opt.lower()
            if any(m in ol for m in ["0-2", "0 - 2", "1-3", "1 - 3", "1-2", "1 to 3", "1 to 2", "1+", "1 year", "2"]):
                return opt

    # Notice period
    if any(k in q for k in ["notice", "how soon", "join", "availability"]):
        for opt in option_labels:
            ol = opt.lower()
            if any(m in ol for m in ["immediate", "15", "0-15", "less than 30", "< 30", "1 month"]):
                return opt

    # How did you hear / learn about this job
    if any(k in q for k in ["how did you learn", "how did you hear", "where did you hear", "source"]):
        for pref in ["company website", "careers page", "linkedin", "job board", "online", "other"]:
            for opt in option_labels:
                if pref in opt.lower() and "employee" not in opt.lower() and "event" not in opt.lower():
                    return opt
        for opt in option_labels:
            if "employee" not in opt.lower() and "refer" not in opt.lower() and "select" not in opt.lower():
                return opt

    yes_opt = next((o for o in option_labels if o.strip().lower() in ("yes", "true", "y")), None)
    if yes_opt is None:
        yes_opt = next((o for o in option_labels if o.strip().lower().startswith("yes")), None)
    no_opt = next((o for o in option_labels if o.strip().lower() in ("no", "false", "n")), None)
    if no_opt is None:
        no_opt = next((o for o in option_labels if o.strip().lower().startswith("no")), None)

    # Questions where NO is the right answer
    if any(
        k in q
        for k in [
            "sponsorship",
            "require visa",
            "employment visa",
            "visa",
            "previously worked",
            "ever worked",
            "worked at",
            "worked for",
            "formerly employed",
            "deemed export",
            "export rule",
            "relatives",
            "family member",
            "government official",
            "non-compete",
            "criminal",
            "backlog",
            "stay up to date",
            "receive alerts",
        ]
    ):
        if no_opt is not None:
            return no_opt

    # Questions where YES is the right answer
    if any(
        k in q
        for k in [
            "authorized",
            "legally",
            "eligible to work",
            "relocat",
            "on-site",
            "onsite",
            "in-office",
            "hybrid",
            "bangalore",
            "bengaluru",
            "delhi",
            "gurgaon",
            "noida",
            "mumbai",
            "hyderabad",
            "immediate",
            "comfortable",
            "bachelor",
            "degree",
            "consent",
            "acknowledge",
            "agree",
            "privacy",
        ]
    ):
        if yes_opt is not None:
            return yes_opt

    # Gender / EEO defaults if required
    if "gender" in q:
        for opt in option_labels:
            ol = opt.strip().lower()
            if ol in ("male", "man", "prefer not to say", "i choose not to disclose", "decline to self-identify"):
                return opt
        for opt in option_labels:
            if any(w in opt.lower() for w in ["male", "man", "not to disclose", "decline", "prefer not"]):
                return opt

    if any(k in q for k in ["sexual orientation", "transgender", "lgbt", "veteran", "disability", "hispanic", "race", "ethnicity", "caste"]):
        for opt in option_labels:
            ol = opt.lower()
            if any(d in ol for d in ["choose not to disclose", "prefer not", "decline", "not a protected", "do not have", "i am not", "south asian", "asian"]):
                return opt

    return None


def fill_ats_application(
    url: str,
    role_title: str = "Associate Product Manager",
    auto_submit: bool = True,
) -> Dict[str, Any]:
    """
    Autofills external company career pages hosted on Greenhouse, Ashby, Lever,
    Workday, Taleo, BambooHR, or direct corporate career forms.
    Handles React-Select comboboxes and candidate email OTP verification on Greenhouse.
    Strictly verifies final submission + confirmation receipt with zero validation errors.
    """
    result: Dict[str, Any] = {
        "url": url,
        "ats_url": url,
        "ats_type": "generic",
        "fields_filled": [],
        "resume_uploaded": False,
        "submitted": False,
        "confirmed": False,
        "screenshot_path": None,
        "error": None,
    }

    profile = load_candidate_profile()
    p = profile.get("personal", {})
    project_root = Path(__file__).resolve().parent.parent.parent
    possible_paths = [
        os.environ.get("RESUME_PATH"),
        str(Path.home() / "career-agent" / "Vishal_Gupta_Resume.pdf"),
        str(Path.home() / "career-agent" / "data" / "resume.pdf"),
        str(project_root / "Vishal_Gupta_Resume.pdf"),
        str(project_root / "data" / "resume.pdf"),
        "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf",
        p.get("resume_local_pdf_path"),
    ]
    pdf_path = next(
        (path for path in possible_paths if path and os.path.exists(path)),
        str(project_root / "data" / "resume.pdf"),
    )

    print(f"\n[ATSFiller] Processing application for: {role_title} ({url})")

    with sync_playwright() as p_play:
        target_page = None
        browser = None
        try:
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
            try:
                browser = p_play.chromium.launch(headless=True, args=launch_args)
            except Exception:
                browser = p_play.chromium.launch(channel="chrome", headless=True, args=launch_args)

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )

            if "naukri.com" in url:
                for ck_path in (
                    project_root / "data" / "naukri_cookies.json",
                    Path.home() / "career-agent" / "data" / "naukri_cookies.json",
                ):
                    if ck_path.exists():
                        try:
                            cookies = json.loads(ck_path.read_text(encoding="utf-8"))
                            if isinstance(cookies, list) and cookies:
                                context.add_cookies(cookies)
                                break
                        except Exception:
                            pass

            page = context.new_page()
            target_page = page

            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # --- Follow portal redirects (Indeed, Naukri, LinkedIn) if needed ---
            current_url = page.url
            if "indeed.com" in current_url:
                print("[ATSFiller] Indeed job detected. Following company site redirect...")
                apply_link = page.locator(
                    '#applyButtonLinkContainer a, a[href*="applystart"], '
                    'a:has-text("Apply on company site"), button:has-text("Apply on company site")'
                ).first
                if apply_link.is_visible(timeout=4000):
                    href = apply_link.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            href = "https://in.indeed.com" + href
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3500)
            elif "naukri.com" in current_url:
                print("[ATSFiller] Naukri job detected. Following company site redirect...")
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
                apply_elem = page.locator('a:has-text("Apply"), button:has-text("Apply"), .jobs-apply-button').first
                if apply_elem.is_visible(timeout=4000):
                    href = apply_elem.get_attribute("href")
                    if href and "safety/go" in href:
                        parsed = _up.urlparse(href)
                        real_target = _up.parse_qs(parsed.query).get("url", [None])[0]
                        if real_target:
                            page.goto(real_target, wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_timeout(3500)
                    elif href and href.startswith("http") and "linkedin.com" not in href:
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3500)

            dest_url = page.url
            result["ats_url"] = dest_url
            print(f"[ATSFiller] Active destination URL: {dest_url}")

            dest_host = (_up.urlparse(dest_url).netloc or "").lower()
            if any(portal_host in dest_host for portal_host in ["naukri.com", "indeed.com", "linkedin.com"]):
                print(f"[ATSFiller] Redirect remained on job portal ({dest_host}); refusing to mark applied.")
                result["error"] = f"portal_redirect_not_followed:{dest_host}"
                return result

            lower_bundle = f"{dest_url} {role_title}".lower()
            if any(bad in lower_bundle for bad in ["magicpin", "lifelong"]):
                result["error"] = "blacklisted_employer"
                return result

            if _has_interactive_captcha(page):
                print("[ATSFiller] Active CAPTCHA / anti-bot gate detected; skipping manual verification portal.")
                result["error"] = "captcha_gate_requires_manual_apply"
                return result

            if "greenhouse.io" in dest_url or "gh_jid=" in dest_url:
                result["ats_type"] = "greenhouse"
            elif "lever.co" in dest_url:
                result["ats_type"] = "lever"
            elif "ashbyhq.com" in dest_url:
                result["ats_type"] = "ashby"
            elif "myworkdayjobs.com" in dest_url or "workday" in dest_url:
                result["ats_type"] = "workday"
            elif "taleo.net" in dest_url or "oraclecloud" in dest_url:
                result["ats_type"] = "taleo"
            elif "bamboohr.com" in dest_url:
                result["ats_type"] = "bamboohr"
            elif "smartrecruiters.com" in dest_url:
                result["ats_type"] = "smartrecruiters"
            print(f"[ATSFiller] Detected ATS Platform: {result['ats_type']}")

            cookie_btn = page.locator(
                'button:has-text("Accept All"), button:has-text("Accept all"), '
                'button:has-text("Agree"), button:has-text("dismiss"), '
                'button#onetrust-accept-btn-handler'
            ).first
            try:
                if cookie_btn.is_visible(timeout=1000):
                    cookie_btn.click(timeout=1000)
            except Exception:
                pass

            if result["ats_type"] == "ashby" and not dest_url.rstrip("/").endswith("/application"):
                app_tab = page.locator('a[href*="/application"], button:has-text("Apply for this Job"), a:has-text("Application")').first
                try:
                    if app_tab.is_visible(timeout=2000):
                        app_tab.click()
                        page.wait_for_timeout(2500)
                except Exception:
                    pass
            elif result["ats_type"] == "lever" and not dest_url.rstrip("/").endswith("/apply"):
                app_btn = page.locator('a.postings-btn[href*="/apply"], a:has-text("Apply for this job")').first
                try:
                    if app_btn.is_visible(timeout=2000):
                        app_btn.click()
                        page.wait_for_timeout(2500)
                except Exception:
                    pass
            elif page.locator('input[type="email"], input#email, input[type="file"], input[name="name"]').count() == 0:
                for trig in page.locator(
                    'button:has-text("Apply for this Job"), a:has-text("Apply for this Job"), '
                    'button:has-text("Apply Now"), a:has-text("Apply Now"), a:has-text("Apply for this job")'
                ).all():
                    try:
                        if trig.is_visible() and trig.is_enabled():
                            trig.click()
                            page.wait_for_timeout(2500)
                            break
                    except Exception:
                        pass

            # --- MULTI-STEP FORM FILLING LOOP ---
            max_steps = 5
            for step_num in range(1, max_steps + 1):
                print(f"[ATSFiller] Processing form step {step_num}...")

                # 1. UPLOAD RESUME FILE
                if not result["resume_uploaded"]:
                    preferred_file_selectors = [
                        "input#resume",
                        "input#_systemfield_resume",
                        "input#resume-upload-input",
                        'input[type="file"][name="resume"]',
                        'input[type="file"][id*="resume" i]',
                        'input[type="file"][name*="resume" i]',
                    ]
                    chosen_fi = None
                    for sel_str in preferred_file_selectors:
                        loc = page.locator(sel_str).first
                        if loc.count() > 0:
                            chosen_fi = loc
                            break
                    if chosen_fi is None:
                        all_files = page.locator('input[type="file"]').all()
                        if all_files:
                            chosen_fi = all_files[0]

                    if chosen_fi is not None:
                        try:
                            chosen_fi.set_input_files(pdf_path)
                            result["resume_uploaded"] = True
                            print(f"[ATSFiller] Resume uploaded successfully: {pdf_path}")
                            page.wait_for_timeout(3000)
                        except Exception as e:
                            print(f"[ATSFiller] File upload attempt error: {e}")

                # 2. HANDLE REACT-SELECT COMBOBOXES FIRST (Greenhouse input[role="combobox"])
                comboboxes = page.locator('input[role="combobox"]').all()
                for cb in comboboxes:
                    try:
                        if not cb.is_visible():
                            continue
                        cb_id = (cb.get_attribute("id") or "").lower()
                        cb_ctx = _extract_question_context(cb)
                        cb_ctx_l = cb_ctx.lower()
                        is_req_cb = bool(
                            cb.get_attribute("required") is not None
                            or cb.get_attribute("aria-required") == "true"
                            or "*" in cb_ctx
                        )

                        # Typeahead comboboxes: Country, Location (City), School, Degree, Discipline
                        if cb_id == "country" or cb_ctx_l.startswith("country country"):
                            cb.click(timeout=2500)
                            cb.fill("India")
                            page.wait_for_timeout(700)
                            matched_country = False
                            for c_opt in page.locator('[role="option"]').all():
                                if c_opt.is_visible():
                                    ct = (c_opt.inner_text() or "").strip()
                                    if ct.lower() == "india" or ct.lower().startswith("india +91") or ct.lower().startswith("india ("):
                                        c_opt.click(timeout=2000)
                                        result["fields_filled"].append({"field": "country", "value": ct})
                                        matched_country = True
                                        break
                            if not matched_country:
                                page.keyboard.press("Enter")
                            page.wait_for_timeout(250)
                            continue

                        if cb_id == "candidate-location" or "location (city)" in cb_ctx_l:
                            cb.click(timeout=2500)
                            cb.press_sequentially("Gurugram", delay=70)
                            page.wait_for_timeout(2000)
                            opt = page.locator('[role="option"]').first
                            if opt.count() > 0 and opt.is_visible():
                                opt_txt = (opt.inner_text() or "").strip()
                                opt.click(timeout=2000)
                                result["fields_filled"].append({"field": "candidate-location", "value": opt_txt[:40]})
                            else:
                                page.keyboard.press("Control+A")
                                page.keyboard.press("Meta+A")
                                page.keyboard.press("Backspace")
                                cb.press_sequentially("New Delhi", delay=70)
                                page.wait_for_timeout(2000)
                                opt2 = page.locator('[role="option"]').first
                                if opt2.count() > 0 and opt2.is_visible():
                                    opt_txt2 = (opt2.inner_text() or "").strip()
                                    opt2.click(timeout=2000)
                                    result["fields_filled"].append({"field": "candidate-location", "value": opt_txt2[:40]})
                                else:
                                    page.keyboard.press("ArrowDown")
                                    page.keyboard.press("Enter")
                            page.wait_for_timeout(300)
                            continue

                        if cb_id.startswith("school"):
                            cb.click(timeout=2500)
                            page.keyboard.type("Netaji Subhas")
                            page.wait_for_timeout(1000)
                            opt = page.locator('[role="option"]').first
                            if opt.count() > 0 and opt.is_visible():
                                opt.click(timeout=2000)
                            page.keyboard.press("Escape")
                            continue

                        if cb_id.startswith("degree"):
                            cb.click(timeout=2500)
                            page.wait_for_timeout(500)
                            opt = page.locator('[role="option"]:has-text("Bachelor")').first
                            if opt.count() > 0 and opt.is_visible():
                                opt.click(timeout=2000)
                            page.keyboard.press("Escape")
                            continue

                        if cb_id.startswith("discipline"):
                            cb.click(timeout=2500)
                            page.keyboard.type("Computer Science")
                            page.wait_for_timeout(600)
                            opt = page.locator('[role="option"]:has-text("Computer Science")').first
                            if opt.count() > 0 and opt.is_visible():
                                opt.click(timeout=2000)
                            page.keyboard.press("Escape")
                            continue

                        # General question combobox: click to open options menu
                        cb.scroll_into_view_if_needed(timeout=2000)
                        cb.click(timeout=2500, force=True)
                        page.wait_for_timeout(450)
                        opts = page.locator('[role="option"]').all()
                        opt_pairs = []
                        for o in opts:
                            try:
                                if o.is_visible():
                                    txt = (o.inner_text() or "").strip()
                                    if txt:
                                        opt_pairs.append((txt, o))
                            except Exception:
                                continue

                        if not opt_pairs:
                            page.keyboard.press("Escape")
                            continue

                        opt_texts = [t for t, _ in opt_pairs]
                        pick = _decide_yes_no_or_option(cb_ctx_l, opt_texts)
                        if not pick and is_req_cb:
                            no_cand = next((t for t in opt_texts if t.lower().startswith("no")), None)
                            if no_cand and any(w in cb_ctx_l for w in ["worked", "conflict", "government", "relative", "sponsorship", "visa", "export"]):
                                pick = no_cand
                            else:
                                pick = opt_texts[0]

                        if pick:
                            for txt, o_loc in opt_pairs:
                                if txt == pick:
                                    o_loc.click(timeout=2000)
                                    result["fields_filled"].append({"field": cb_ctx_l[:45], "value": pick[:40]})
                                    page.wait_for_timeout(200)
                                    break
                        page.keyboard.press("Escape")
                    except Exception:
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                        continue

                # 3. FILL STANDARD TEXT / EMAIL / TEL / URL / NUMBER INPUTS
                input_elements = page.locator(
                    'input[type="text"], input[type="email"], input[type="tel"], '
                    'input[type="url"], input[type="number"], input:not([type])'
                ).all()
                for inp in input_elements:
                    try:
                        if not inp.is_visible():
                            continue
                        if inp.get_attribute("role") == "combobox":
                            continue
                        id_attr = (inp.get_attribute("id") or "").lower()
                        name_attr = (inp.get_attribute("name") or "").lower()
                        placeholder = inp.get_attribute("placeholder") or ""
                        if id_attr.startswith("iti-") or id_attr.startswith("security-input"):
                            continue

                        existing_val = (inp.input_value() or "").strip()
                        if existing_val:
                            continue

                        field_identifier = _extract_question_context(inp).lower()
                        if not id_attr and not name_attr and not placeholder and not field_identifier:
                            continue
                        # Skip React-Select hidden dummy required inputs (empty id/name/placeholder inside .select__container)
                        if not id_attr and not name_attr and not placeholder:
                            in_react_select = inp.evaluate("el => !!el.closest('[class*=\"select\" i]')")
                            if in_react_select:
                                continue

                        is_required = bool(
                            inp.get_attribute("required") is not None
                            or inp.get_attribute("aria-required") == "true"
                            or "✱" in field_identifier
                            or "*" in field_identifier
                        )

                        if any(
                            w in field_identifier
                            for w in ["portfolio", "website", "twitter", "blog", "refer", "referral", "referred", "pronoun", "if you answered"]
                        ) and "github" not in field_identifier and "linkedin" not in field_identifier:
                            if not is_required:
                                continue

                        # Greenhouse phone input uses local 10-digit number after Country = India (+91)
                        if result["ats_type"] == "greenhouse" and (id_attr == "phone" or "phone" in field_identifier):
                            raw_phone = re.sub(r"^\+?91", "", p.get("phone", "8368387245")).strip()
                            inp.fill(raw_phone)
                            result["fields_filled"].append({"field": "phone", "value": raw_phone})
                            continue

                        matched_val = answer_standard_field(field_identifier)
                        if not matched_val and is_required:
                            matched_val = answer_open_ended_question(field_identifier or role_title, role_title=role_title)
                            if len(matched_val) > 180:
                                matched_val = matched_val[:180].rsplit(" ", 1)[0]

                        if matched_val:
                            matched_val = _scrub_employer_names(matched_val)
                            if id_attr == "location-input" or name_attr == "location":
                                inp.click()
                                inp.fill("Gurugram, Haryana, India")
                                page.wait_for_timeout(900)
                                loc_opt = page.locator('.dropdown-results li, .location-results div, [role="option"]').first
                                try:
                                    if loc_opt.is_visible(timeout=800):
                                        loc_opt.click()
                                except Exception:
                                    pass
                            else:
                                inp.fill(str(matched_val))
                            result["fields_filled"].append({"field": field_identifier[:50], "value": str(matched_val)[:40]})
                    except Exception:
                        continue

                # 4. FILL TEXTAREAS
                for ta in page.locator("textarea").all():
                    try:
                        if not ta.is_visible() or (ta.input_value() or "").strip():
                            continue
                        ta_name = (ta.get_attribute("name") or "").lower()
                        if "captcha" in ta_name:
                            continue

                        ident = _extract_question_context(ta)
                        ident_l = ident.lower()
                        is_req = bool(
                            ta.get_attribute("required") is not None
                            or ta.get_attribute("aria-required") == "true"
                            or "✱" in ident
                            or "*" in ident
                        )
                        if any(w in ident_l for w in ["cover letter", "additional information", "note", "why do you want", "summary"]):
                            if not is_req and "cover letter" in ident_l and result["ats_type"] == "greenhouse":
                                continue
                            pitch = (
                                "B.Tech in Mathematics & Computing (CSE) from NSUT Delhi with 1 year full-time experience "
                                "across product strategy, SQL analytics, and high-velocity workflow automation. "
                                "Available to join immediately within a 15-day notice period."
                            )
                            ta.fill(pitch)
                            result["fields_filled"].append({"field": ident_l[:45], "value": "Cover letter pitch"})
                        else:
                            ans = _scrub_employer_names(
                                answer_open_ended_question(ident or role_title, role_title=role_title)
                            )
                            ta.fill(ans)
                            result["fields_filled"].append({"field": ident_l[:45], "value": ans[:40]})
                    except Exception:
                        continue

                # 5. HANDLE RADIO GROUPS BY GROUP QUESTION CONTEXT
                radio_groups: Dict[str, List[Any]] = {}
                for r in page.locator('input[type="radio"]').all():
                    try:
                        gname = r.get_attribute("name") or r.get_attribute("id") or f"group_{len(radio_groups)}"
                        radio_groups.setdefault(gname, []).append(r)
                    except Exception:
                        continue

                for gname, r_list in radio_groups.items():
                    try:
                        if any(r.is_checked() for r in r_list):
                            continue
                        q_ctx = _extract_question_context(r_list[0])
                        opt_map = []
                        for r in r_list:
                            lbl_txt = (
                                r.evaluate(
                                    """(node) => {
                                        if (node.labels && node.labels[0]) return node.labels[0].innerText.trim();
                                        const p = node.closest('label');
                                        if (p) return p.innerText.trim();
                                        return node.getAttribute('value') || '';
                                    }"""
                                )
                                or ""
                            ).strip()
                            opt_map.append((lbl_txt, r))

                        labels_only = [lbl for lbl, _ in opt_map if lbl]
                        chosen_lbl = _decide_yes_no_or_option(q_ctx, labels_only)
                        if chosen_lbl:
                            for lbl, r in opt_map:
                                if lbl == chosen_lbl:
                                    r.check(force=True)
                                    result["fields_filled"].append({"field": q_ctx[:45], "value": chosen_lbl})
                                    break
                    except Exception:
                        continue

                # 5b. HANDLE ASHBY YES/NO TOGGLE BUTTONS
                for entry in page.locator('.ashby-application-form-field-entry, fieldset, [role="group"]').all():
                    try:
                        btns = entry.locator("button").all()
                        if len(btns) == 2:
                            b_texts = [(b.inner_text() or "").strip() for b in btns]
                            if set(t.lower() for t in b_texts) == {"yes", "no"}:
                                q_txt = (entry.inner_text() or "").strip()
                                pick = _decide_yes_no_or_option(q_txt, b_texts)
                                if pick:
                                    for b, bt in zip(btns, b_texts):
                                        if bt == pick:
                                            b.click()
                                            result["fields_filled"].append({"field": q_txt[:45], "value": pick})
                                            break
                    except Exception:
                        continue

                # 5c. HANDLE REQUIRED CONSENT / PRIVACY CHECKBOXES
                for chk in page.locator('input[type="checkbox"]').all():
                    try:
                        if chk.is_checked():
                            continue
                        c_ctx = _extract_question_context(chk).lower()
                        is_req_chk = chk.get_attribute("required") is not None or chk.get_attribute("aria-required") == "true"
                        if is_req_chk or any(k in c_ctx for k in ["consent", "privacy", "agree", "acknowledge", "terms", "gdpr"]):
                            chk.check(force=True)
                            result["fields_filled"].append({"field": c_ctx[:40], "value": "checked"})
                    except Exception:
                        continue

                # 6. HANDLE <SELECT> DROPDOWNS ACROSS ALL OPTIONS
                for sel in page.locator("select").all():
                    try:
                        if not sel.is_visible():
                            continue
                        s_ctx = _extract_question_context(sel).lower()
                        all_opts: List[str] = sel.evaluate(
                            "el => Array.from(el.options).map(o => o.text.trim())"
                        )
                        chosen_opt = None
                        if any(k in s_ctx for k in ["country", "location", "where are you based", "reside"]):
                            chosen_opt = next((o for o in all_opts if o.lower() == "india"), None) or next(
                                (o for o in all_opts if "india" in o.lower()), None
                            )
                        if not chosen_opt:
                            chosen_opt = _decide_yes_no_or_option(s_ctx, all_opts)
                        if not chosen_opt:
                            for opt in all_opts:
                                ol = opt.lower()
                                if ol in ("india", "delhi", "b.tech", "bachelor's degree", "full-time", "full time"):
                                    chosen_opt = opt
                                    break
                        if chosen_opt:
                            sel.select_option(label=chosen_opt)
                            result["fields_filled"].append({"field": s_ctx[:40], "value": chosen_opt})
                    except Exception:
                        pass

                next_btn = page.locator(
                    'button[data-automation-id="bottom-navigation-next-button"], input[id*="SaveAndContinue"], '
                    'button:has-text("Save and Continue"), button:has-text("Save & Continue"), button:has-text("Next"), '
                    'input[value*="Save and Continue" i], input[value*="Next" i]'
                ).first
                submit_btn = page.locator(
                    '#btn-submit, button[data-automation-id="bottom-navigation-submit-button"], '
                    'button:has-text("Submit Application"), button:has-text("Submit application"), '
                    'button:has-text("SUBMIT APPLICATION"), button[type="submit"], input[type="submit"]'
                ).first

                if next_btn.is_visible(timeout=1200) and not submit_btn.is_visible(timeout=800):
                    print("[ATSFiller] Advancing to next step...")
                    next_btn.click()
                    page.wait_for_timeout(3000)
                else:
                    break

            # 7. SUBMIT FORM & STRICTLY VERIFY RECEIPT (INCLUDING GREENHOUSE CANDIDATE EMAIL OTP)
            data_dir = project_root / "data"
            os.makedirs(data_dir, exist_ok=True)

            if auto_submit and (result["resume_uploaded"] or len(result["fields_filled"]) >= 2):
                modal = page.locator(".modal.show, #careerAddModal").first
                scope = (
                    modal
                    if (modal.is_visible(timeout=500) and modal.locator('input[type="email"], input[type="file"]').count() > 0)
                    else page
                )

                pre_submit_url = page.url.lower()
                try:
                    pre_submit_text = page.locator("body").inner_text().lower()
                except Exception:
                    pre_submit_text = ""

                def _find_submit_button():
                    candidate_btns = scope.locator(
                        '#btn-submit, '
                        'button[data-automation-id="bottom-navigation-submit-button"], '
                        'button:has-text("Submit Application"), button:has-text("Submit application"), '
                        'button:has-text("Send Application"), button:has-text("Complete Application"), '
                        'button:has-text("Submit"), input[type="submit"], button[type="submit"]'
                    ).all()
                    forbidden_words = (
                        "search", "sign in", "log in", "login", "register", "create account",
                        "filter", "subscribe", "cookie", "upload", "linkedin", "toggle",
                    )
                    allowed_words = ("submit", "send application", "complete application", "apply for this job", "finish")
                    for btn in candidate_btns:
                        try:
                            if not btn.is_visible() or not btn.is_enabled():
                                continue
                            lbl = (
                                (btn.inner_text() or "")
                                + " "
                                + (btn.get_attribute("value") or "")
                                + " "
                                + (btn.get_attribute("aria-label") or "")
                            ).strip().lower()
                            if any(fw in lbl for fw in forbidden_words):
                                continue
                            if any(aw in lbl for aw in allowed_words):
                                return btn
                        except Exception:
                            continue
                    return None

                chosen_submit = _find_submit_button()
                if chosen_submit is not None:
                    submit_epoch = time.time() - 10.0
                    chosen_submit.scroll_into_view_if_needed()
                    chosen_submit.click()
                    result["submitted"] = True
                    print(f"[ATSFiller] Clicked Submit for {role_title}; waiting for confirmation...")

                    email_otp_attempted = False
                    for wait_ms in (4000, 4500):
                        page.wait_for_timeout(wait_ms)

                        if _has_interactive_captcha(page):
                            print("[ATSFiller] Interactive CAPTCHA challenge appeared after Submit; refusing to bypass or mark applied.")
                            result["confirmed"] = False
                            result["error"] = "captcha_gate_requires_manual_apply"
                            break

                        post_url = page.url.lower()
                        try:
                            post_text = page.locator("body").inner_text().lower()
                        except Exception:
                            post_text = ""

                        # Handle Greenhouse candidate email ownership 8-character security code
                        sec_inp0 = page.locator("input#security-input-0, input[id^='security-input-']").first
                        if (
                            not email_otp_attempted
                            and (sec_inp0.count() > 0 and sec_inp0.is_visible())
                            or ("security code" in post_text and not email_otp_attempted)
                        ):
                            email_otp_attempted = True
                            print("[ATSFiller] Greenhouse requested candidate email verification code; checking vishal90301@gmail.com via IMAP...")
                            otp_code = _fetch_greenhouse_email_code(min_epoch=submit_epoch, timeout_sec=28)
                            if otp_code and len(otp_code) == 8:
                                print(f"[ATSFiller] Retrieved 8-char Greenhouse email code from inbox; completing submission...")
                                for idx_c, ch in enumerate(otp_code):
                                    cell = page.locator(f"input#security-input-{idx_c}").first
                                    if cell.count() > 0 and cell.is_visible():
                                        cell.fill(ch)
                                page.wait_for_timeout(600)
                                resub_btn = _find_submit_button()
                                if resub_btn is not None:
                                    resub_btn.click()
                                    page.wait_for_timeout(5500)
                                    post_url = page.url.lower()
                                    try:
                                        post_text = page.locator("body").inner_text().lower()
                                    except Exception:
                                        post_text = ""
                            else:
                                result["confirmed"] = False
                                result["error"] = "email_verification_code_timeout"
                                break

                        error_phrases = [
                            "this field is required",
                            "is required",
                            "please fill out this field",
                            "please complete all required",
                            "cannot be blank",
                            "please correct the errors",
                            "invalid email",
                            "invalid phone",
                            "missing required",
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
                            "thank you for submitting your application",
                            "application has been submitted",
                            "your application has been submitted",
                            "application submitted",
                            "your application was submitted",
                            "received your application",
                            "we've received your application",
                            "we have received your application",
                            "application has been received",
                            "your application has been sent",
                            "successfully submitted your application",
                            "successfully applied",
                        ]
                        has_new_confirmation_text = any(
                            cp in post_text and cp not in pre_submit_text for cp in confirmation_phrases
                        )
                        url_confirmed = post_url != pre_submit_url and any(
                            u in post_url
                            for u in [
                                "/confirmation",
                                "/thank-you",
                                "/thankyou",
                                "/submitted",
                                "application-submitted",
                                "applied=true",
                                "success",
                            ]
                        )

                        if not has_new_validation_error and invalid_count == 0 and (
                            has_new_confirmation_text or url_confirmed
                        ):
                            result["confirmed"] = True
                            result["error"] = None
                            print(f"[ATSFiller] Submission CONFIRMED on page for {role_title} ({page.url})!")
                            break
                    else:
                        if not result["confirmed"] and not result["error"]:
                            result["error"] = "unconfirmed_after_submit"
                            print(f"[ATSFiller] Submit clicked for {role_title}, but NO confirmation receipt detected. Not marking applied.")
                else:
                    result["error"] = "no_valid_submit_button"
                    print(f"[ATSFiller] No valid application submit button found for {role_title}.")
            else:
                result["error"] = "insufficient_form_fields_or_login_wall"

            # 8. TAKE POST-ACTION VERIFICATION SCREENSHOT
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
                except Exception:
                    pass
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    return result
