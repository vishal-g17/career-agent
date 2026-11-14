import fcntl
import json
import os
import re
import sys
import time
import urllib.parse as _up
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

from .. import config  # loads .env natively
from ..db import database
from ..engine.llm import generate_text
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


def _frame_has_captcha(frame) -> bool:
    try:
        return bool(
            frame.evaluate(
                """() => {
                    const bodyText = (document.body && document.body.innerText || '').toLowerCase();
                    if (
                        bodyText.includes('verify you are human') ||
                        bodyText.includes("confirm you're a human") ||
                        bodyText.includes('confirm you are a human') ||
                        bodyText.includes('checking if the site connection is secure') ||
                        document.querySelector('#challenge-running, #cf-challenge-running')
                    ) {
                        return true;
                    }
                    // Visible text/image CAPTCHA inputs (e.g. Keka Careers image captcha)
                    const capInputs = Array.from(document.querySelectorAll('input#captcha, input[name*="captcha" i], input[placeholder*="captcha" i]'));
                    for (const ci of capInputs) {
                        const r = ci.getBoundingClientRect();
                        const st = window.getComputedStyle(ci);
                        if (st.display !== 'none' && st.visibility !== 'hidden' && r.width > 20 && r.height > 12) {
                            return true;
                        }
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

                        if (
                            src.includes('challenges.cloudflare.com') ||
                            src.includes('turnstile') ||
                            src.includes('captcha-delivery.com')
                        ) {
                            return true;
                        }
                        if (src.includes('recaptcha')) {
                            if (src.includes('bframe') && rect.width > 150 && rect.height > 150) {
                                return true;
                            }
                            if (src.includes('anchor') && !src.includes('size=invisible')) {
                                return true;
                            }
                        }
                        if (src.includes('hcaptcha')) {
                            if (src.includes('frame=challenge') && rect.width > 150 && rect.height > 150) {
                                return true;
                            }
                            if (src.includes('frame=checkbox') && !src.includes('invisible') && rect.width > 100 && rect.height > 45) {
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


def _has_interactive_captcha(page) -> bool:
    """
    Detects active, interactive anti-bot / CAPTCHA / human-verification challenges
    across main page and embedded ATS iframes (e.g. iCIMS / Keka / Greenhouse),
    while ignoring passive invisible score badges.
    Strictly follows safety guidelines: never interacts with or bypasses CAPTCHAs.
    """
    try:
        if _frame_has_captcha(page.main_frame):
            return True
        for f in page.frames:
            f_url = (f.url or "").lower()
            if "hcaptcha.com" in f_url and "frame=challenge" in f_url:
                try:
                    el = f.frame_element()
                    box = el.bounding_box()
                    if box and box.get("width", 0) > 150 and box.get("height", 0) > 150:
                        return True
                except Exception:
                    pass
            if f != page.main_frame and ("icims.com" in f_url or "keka.com" in f_url or "greenhouse.io" in f_url):
                if _frame_has_captcha(f):
                    return True
        return False
    except Exception:
        return False


def _extract_question_context(el) -> str:
    """
    Extracts full label + ancestor question prompt for inputs, textareas, selects,
    comboboxes, and radios across Ashby, Lever, Greenhouse, Keka, Workday, and generic HTML forms.
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
                        'fieldset, [role="group"], [role="radiogroup"], .question, [class*="question" i]'
                    );
                    if (container) {
                        const qEl = container.querySelector(
                            '.text, legend, label, [class*="label" i], [class*="title" i], [class*="prompt" i]'
                        );
                        if (qEl && qEl.innerText) {
                            parts.push(qEl.innerText.trim());
                        } else if (container.innerText) {
                            parts.push(container.innerText.trim().slice(0, 260));
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


def _pick_option_with_llm(question_text: str, option_labels: List[str], role_title: str = "Product Analyst") -> Optional[str]:
    """
    Uses Gemini to select the single most accurate option for analytical / domain MCQs
    or custom screening dropdowns (e.g. SQL/Sheets functions, funnel case questions).
    """
    valid_opts = [o for o in option_labels if o and not o.strip().lower().startswith(("select", "--", "choose"))]
    if not valid_opts:
        return None
    numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(valid_opts))
    prompt = (
        f"Role: {role_title}\n"
        "Candidate Profile: Vishal Gupta — B.Tech Mathematics & Computing (NSUT Delhi), 1+ year experience in Product Analytics, SQL, Python, Sheets/Excel, D2C & quick-commerce startups; based in Delhi NCR / Gurugram and open to relocating anywhere in India (Bengaluru, Mumbai, Hyderabad, Pune); current compensation ~12 LPA, expected compensation 15-18 LPA, notice period 15 days.\n\n"
        f"Screening Question: {question_text}\n\n"
        f"Options:\n{numbered}\n\n"
        "Reply ONLY with the exact option number (e.g. 2) that is the most accurate and technically correct answer."
    )
    try:
        ans = generate_text(
            prompt=prompt,
            system_instruction="You are an expert product analyst and hiring assessment solver. Return only the integer index of the single best option.",
            temperature=0.0,
        )
        m = re.search(r"\b(\d+)\b", ans or "")
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(valid_opts):
                return valid_opts[idx]
    except Exception as e:
        print(f"[ATSFiller] LLM option picker fallback notice: {e}")
    return None


def _decide_yes_no_or_option(question_text: str, option_labels: List[str], role_title: str = "Product Analyst") -> Optional[str]:
    """
    Given a question prompt and candidate option labels, picks the best matching option
    for Vishal Gupta (1+ yr exp, 12 LPA current, 15-18 LPA expected, Delhi NCR based & open to relocate, 15-day notice).
    Falls back to LLM MCQ reasoning for domain/analytical questions instead of picking index 0 blindly.
    """
    q = question_text.lower()
    if not option_labels:
        return None

    # 1. Current vs Expected Compensation / CTC / Salary ranges
    if any(k in q for k in ["compensation", "ctc", "salary", "remuneration", "package"]):
        is_expected = any(k in q for k in ["expected", "desired", "ask", "target"])
        prefs = (
            ["15-20", "15 - 20", "14-18", "15-18", "12-18", "15-25", "10-20", "15+", "15 lpa", "16"]
            if is_expected
            else ["10-15", "10 - 15", "10-12", "8-12", "10-14", "12 lpa", "10 lpa", "5-10"]
        )
        for p_tok in prefs:
            for opt in option_labels:
                if p_tok in opt.lower():
                    return opt

    # 2. Experience ranges (prefer 1-2 / 1-3 / 0-2 years first, then 0-1)
    if any(k in q for k in ["experience", "years", "exp"]) and not any(x in q for x in ["export", "expenses", "expected"]):
        for p_tok in ["1-2", "1 - 2", "1-3", "1 - 3", "0-2", "0 - 2", "1 to 3", "1 to 2", "1+", "1 year", "0-1", "0 - 1"]:
            for opt in option_labels:
                if p_tok in opt.lower():
                    return opt

    # 3. Notice period / Joining availability
    if any(k in q for k in ["notice", "how soon", "join", "availability"]):
        for p_tok in ["immediate", "0-15", "15", "0-30", "0 - 30", "less than 30", "< 30", "1 month", "30 days"]:
            for opt in option_labels:
                if p_tok in opt.lower():
                    return opt

    # 4. Residing in non-NCR city with relocation sub-options (e.g. "Are you residing in Bengaluru?")
    non_ncr_cities = ["bengaluru", "bangalore", "mumbai", "hyderabad", "pune", "chennai"]
    if any(c in q for c in non_ncr_cities) and any(w in q for w in ["resid", "currently in", "living in", "based in", "located in"]):
        for opt in option_labels:
            ol = opt.lower()
            if ("relocat" in ol or "willing" in ol) and "not open" not in ol and "unwilling" not in ol:
                return opt

    # 5. How did you hear / learn about this job
    if any(k in q for k in ["how did you learn", "how did you hear", "where did you hear", "source"]):
        for pref in ["linkedin", "company website", "careers page", "job board", "online", "other"]:
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

    # 6. Questions where NO is the right answer
    if any(
        k in q
        for k in [
            "sponsorship",
            "require visa",
            "employment visa",
            "visa",
            "previously worked",
            "ever worked",
            "ever been employed",
            "employed by",
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

    # 7. Questions where YES is the right answer
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
            "gurugram",
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

    # 8. Gender / EEO defaults if required
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

    # 9. Analytical / Domain MCQ or Custom Screening Question -> Ask LLM!
    llm_pick = _pick_option_with_llm(question_text, option_labels, role_title=role_title)
    if llm_pick:
        return llm_pick

    return None


def fill_ats_application(
    url: str,
    role_title: str = "Associate Product Manager",
    auto_submit: bool = True,
    shared_browser: Any = None,
    existing_page: Any = None,
    leave_open: bool = False,
) -> Dict[str, Any]:
    """
    Autofills external company career pages hosted on Greenhouse, Ashby, Lever,
    Keka, Workday, Taleo, BambooHR, or direct corporate career forms.
    Strictly verifies final submission + confirmation receipt and NEVER bypasses CAPTCHAs
    or human-verification challenges.
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

    from contextlib import nullcontext

    owns_browser = shared_browser is None and existing_page is None
    pw_cm = sync_playwright() if owns_browser else nullcontext(None)
    lock_fh = None

    with pw_cm as p_play:
        target_page = None
        context = None
        browser = shared_browser
        try:
            if owns_browser and sys.platform != "darwin":
                try:
                    lock_fh = open("/tmp/cloud_chromium.lock", "w")
                    fcntl.flock(lock_fh, fcntl.LOCK_EX)
                except Exception:
                    pass

            if existing_page is not None:
                page = existing_page
                target_page = page
            else:
                if owns_browser:
                    launch_args = [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ]
                    if sys.platform != "darwin":
                        launch_args.extend(
                            [
                                "--disable-gpu",
                                "--disable-software-rasterizer",
                                "--disable-extensions",
                                "--disable-background-networking",
                                "--renderer-process-limit=1",
                                "--js-flags=--max-old-space-size=192",
                                "--blink-settings=imagesEnabled=false",
                            ]
                        )
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

            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1800)

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

            try:
                early_body = (page.locator("body").inner_text() or "").strip().lower()
                if (
                    "sorry, but we can't find that page" in early_body
                    or "job you are looking for is no longer open" in early_body
                    or "position has been filled" in early_body
                    or "no longer accepting applications" in early_body
                    or "the job you requested was not found" in early_body
                    or "the page you requested was not found" in early_body
                ):
                    result["error"] = "expired"
                    return result
            except Exception:
                pass

            if _has_interactive_captcha(page) and not leave_open:
                print("[ATSFiller] Active CAPTCHA / anti-bot gate detected; skipping manual verification portal.")
                result["error"] = "captcha_gate_requires_manual_apply"
                return result

            if "greenhouse.io" in dest_url or "gh_jid=" in dest_url:
                result["ats_type"] = "greenhouse"
            elif "lever.co" in dest_url:
                result["ats_type"] = "lever"
            elif "ashbyhq.com" in dest_url:
                result["ats_type"] = "ashby"
            elif "keka.com" in dest_url:
                result["ats_type"] = "keka"
            elif "icims.com" in dest_url:
                result["ats_type"] = "icims"
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
                    'a#link-apply, a.apply, '
                    'button:has-text("Apply for this Job"), a:has-text("Apply for this Job"), '
                    'button:has-text("Apply Now"), a:has-text("Apply Now"), '
                    'a:has-text("Apply for this job"), a:has-text("APPLY"), button:has-text("APPLY"), '
                    'a[aria-label^="Apply"], button[aria-label^="Apply"]'
                ).all():
                    try:
                        if trig.is_visible() and trig.is_enabled():
                            href = trig.get_attribute("href")
                            if href and href.startswith("http"):
                                page.goto(href, wait_until="domcontentloaded", timeout=40000)
                                page.wait_for_timeout(3500)
                                result["ats_url"] = page.url
                                if "icims.com" in page.url.lower():
                                    result["ats_type"] = "icims"
                            else:
                                trig.click()
                                page.wait_for_timeout(2800)
                                result["ats_url"] = page.url
                            break
                    except Exception:
                        pass

            # Re-check CAPTCHA after following Apply transition (e.g. Jibe -> iCIMS or Keka)
            try:
                early_body = (page.locator("body").inner_text() or "").strip().lower()
                if (
                    "sorry, but we can't find that page" in early_body
                    or "job you are looking for is no longer open" in early_body
                    or "position has been filled" in early_body
                    or "no longer accepting applications" in early_body
                    or "the job you requested was not found" in early_body
                    or "the page you requested was not found" in early_body
                ):
                    result["error"] = "expired"
                    return result
            except Exception:
                pass

            if _has_interactive_captcha(page) and not leave_open:
                print("[ATSFiller] CAPTCHA gate detected on application form; refusing to bypass.")
                result["error"] = "captcha_gate_requires_manual_apply"
                return result

            # Handle iCIMS embedded login/email step if present (stops immediately if hCaptcha opens)
            if "icims.com" in page.url.lower():
                ic_frame = next(
                    (f for f in page.frames if f != page.main_frame and "icims.com" in (f.url or "").lower()),
                    None,
                )
                if ic_frame is not None:
                    try:
                        em_inp = ic_frame.locator("input#email, input[name='css_loginName']").first
                        if em_inp.count() > 0 and em_inp.is_visible():
                            em_inp.fill(p.get("email", "vishal90301@gmail.com"))
                            gdpr_chk = ic_frame.locator("input#accept_gdpr").first
                            if gdpr_chk.count() > 0:
                                gdpr_chk.check(force=True)
                            if not leave_open:
                                nxt_ic = ic_frame.locator("input#enterEmailSubmitButton").first
                                if nxt_ic.count() > 0:
                                    nxt_ic.click()
                                    page.wait_for_timeout(4500)
                                    if _has_interactive_captcha(page):
                                        print("[ATSFiller] iCIMS triggered interactive hCaptcha challenge; stopping cleanly.")
                                        result["error"] = "captcha_gate_requires_manual_apply"
                                        return result
                    except Exception:
                        pass

            # --- MULTI-STEP FORM FILLING LOOP ---
            max_steps = 5
            for step_num in range(1, max_steps + 1):
                print(f"[ATSFiller] Processing form step {step_num}...")

                # 1. UPLOAD RESUME FILE (strictly input[type="file"] so readonly filename text inputs never match)
                if not result["resume_uploaded"]:
                    preferred_file_selectors = [
                        'input[type="file"]#resume',
                        'input[type="file"]#_systemfield_resume',
                        'input[type="file"]#resume-upload-input',
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
                            cb.scroll_into_view_if_needed(timeout=2000)
                            cb.click(timeout=2500, force=True)
                            matched_loc = False
                            for loc_q in ("Gurugram, Haryana, India", "New Delhi, Delhi, India", "Bengaluru, Karnataka, India"):
                                cb.fill("")
                                cb.press_sequentially(loc_q, delay=45)
                                page.wait_for_timeout(1800)
                                vis_opts = [o for o in page.locator('[role="option"]').all() if o.is_visible()]
                                india_opt = next(
                                    (o for o in vis_opts if "india" in (o.inner_text() or "").lower()),
                                    vis_opts[0] if vis_opts else None,
                                )
                                if india_opt is not None:
                                    opt_txt = (india_opt.inner_text() or "").strip()
                                    if "no options" not in opt_txt.lower():
                                        india_opt.click(timeout=2000)
                                        result["fields_filled"].append({"field": "candidate-location", "value": opt_txt[:40]})
                                        matched_loc = True
                                        break
                            if not matched_loc:
                                page.keyboard.press("Escape")
                            page.wait_for_timeout(250)
                            continue

                        if cb_id.startswith("school"):
                            cb.scroll_into_view_if_needed(timeout=2000)
                            cb.click(timeout=2500, force=True)
                            for sq in ("Netaji Subhas", "University of Delhi", "Other"):
                                cb.fill(sq)
                                page.wait_for_timeout(1200)
                                vis_s = [
                                    o for o in page.locator('[role="option"]').all()
                                    if o.is_visible() and "no options" not in (o.inner_text() or "").lower()
                                ]
                                if vis_s:
                                    stxt = (vis_s[0].inner_text() or "").strip()
                                    vis_s[0].click(timeout=2000)
                                    result["fields_filled"].append({"field": cb_id, "value": stxt[:40]})
                                    break
                            page.wait_for_timeout(200)
                            continue

                        if cb_id.startswith("degree"):
                            cb.scroll_into_view_if_needed(timeout=2000)
                            cb.click(timeout=2500, force=True)
                            page.wait_for_timeout(600)
                            vis_d = [o for o in page.locator('[role="option"]').all() if o.is_visible()]
                            if not vis_d:
                                cb.fill("Bachelor")
                                page.wait_for_timeout(700)
                                vis_d = [o for o in page.locator('[role="option"]').all() if o.is_visible()]
                            deg_opt = next(
                                (
                                    o for o in vis_d
                                    if any(k in (o.inner_text() or "").lower() for k in ["bachelor", "b.tech", "b.s", "b.e", "undergraduate"])
                                ),
                                vis_d[0] if vis_d else None,
                            )
                            if deg_opt is not None:
                                dtxt = (deg_opt.inner_text() or "").strip()
                                deg_opt.click(timeout=2000)
                                result["fields_filled"].append({"field": cb_id, "value": dtxt[:40]})
                            else:
                                page.keyboard.press("Escape")
                            page.wait_for_timeout(200)
                            continue

                        if cb_id.startswith("discipline"):
                            cb.scroll_into_view_if_needed(timeout=2000)
                            cb.click(timeout=2500, force=True)
                            cb.fill("Computer Science")
                            page.wait_for_timeout(900)
                            vis_disc = [
                                o for o in page.locator('[role="option"]').all()
                                if o.is_visible() and "no options" not in (o.inner_text() or "").lower()
                            ]
                            if vis_disc:
                                disctxt = (vis_disc[0].inner_text() or "").strip()
                                vis_disc[0].click(timeout=2000)
                                result["fields_filled"].append({"field": cb_id, "value": disctxt[:40]})
                            else:
                                page.keyboard.press("Escape")
                            page.wait_for_timeout(200)
                            continue

                        if cb_id.startswith("start-month") or cb_id.startswith("end-month"):
                            target_m = "august" if cb_id.startswith("start-month") else "may"
                            cb.scroll_into_view_if_needed(timeout=2000)
                            cb.click(timeout=2500, force=True)
                            page.wait_for_timeout(450)
                            vis_m = [o for o in page.locator('[role="option"]').all() if o.is_visible()]
                            m_opt = next((o for o in vis_m if target_m in (o.inner_text() or "").lower()), vis_m[0] if vis_m else None)
                            if m_opt is not None:
                                mtxt = (m_opt.inner_text() or "").strip()
                                m_opt.click(timeout=2000)
                                result["fields_filled"].append({"field": cb_id, "value": mtxt[:20]})
                            else:
                                page.keyboard.press("Escape")
                            page.wait_for_timeout(200)
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
                        pick = _decide_yes_no_or_option(cb_ctx_l, opt_texts, role_title=role_title)
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
                        if id_attr.startswith("iti-") or id_attr.startswith("security-input") or "captcha" in id_attr or "captcha" in name_attr:
                            continue

                        existing_val = (inp.input_value() or "").strip()
                        field_identifier = _extract_question_context(inp).lower()
                        if "captcha" in field_identifier:
                            continue

                        # Clear bogus resume-autofill middle name if resume parser mispopulated it
                        if existing_val and ("middlename" in name_attr or "middle name" in field_identifier):
                            try:
                                inp.fill("")
                            except Exception:
                                pass
                            continue
                        if existing_val:
                            continue

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

                        # Greenhouse / Keka phone input uses local 10-digit number after Country = India (+91)
                        if (result["ats_type"] in ("greenhouse", "keka")) and (
                            id_attr == "phone" or "mobilephone.number" in name_attr or "phone" in field_identifier
                        ):
                            raw_phone = re.sub(r"^\+?91", "", p.get("phone", "8368387245")).strip()
                            inp.fill(raw_phone)
                            result["fields_filled"].append({"field": "phone", "value": raw_phone})
                            continue

                        if id_attr.startswith("start-year") or ("start" in field_identifier and "year" in field_identifier):
                            inp.fill("2022")
                            result["fields_filled"].append({"field": id_attr or "start-year", "value": "2022"})
                            continue
                        if id_attr.startswith("end-year") or ("end" in field_identifier and "year" in field_identifier):
                            inp.fill("2026")
                            result["fields_filled"].append({"field": id_attr or "end-year", "value": "2026"})
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

                # 5c. HANDLE MULTI-OPTION CHECKBOX GROUPS & CONSENT CHECKBOXES
                chk_groups: Dict[str, List[Any]] = {}
                single_chks: List[Any] = []
                for chk in page.locator('input[type="checkbox"]').all():
                    try:
                        cname = (chk.get_attribute("name") or "").strip()
                        if cname and ("[]" in cname or cname.startswith("question_")):
                            chk_groups.setdefault(cname, []).append(chk)
                        else:
                            single_chks.append(chk)
                    except Exception:
                        continue

                for cname, c_list in chk_groups.items():
                    try:
                        if any(c.is_checked() for c in c_list):
                            continue
                        q_ctx = _extract_question_context(c_list[0])
                        opt_map = []
                        for c_el in c_list:
                            lbl_txt = (
                                c_el.evaluate(
                                    """(node) => {
                                        if (node.labels && node.labels[0]) return node.labels[0].innerText.trim();
                                        const p = node.closest('label');
                                        if (p) return p.innerText.trim();
                                        return node.getAttribute('value') || '';
                                    }"""
                                )
                                or ""
                            ).strip()
                            if lbl_txt:
                                opt_map.append((lbl_txt, c_el))
                        labels_only = [lbl for lbl, _ in opt_map]
                        chosen_lbl = _decide_yes_no_or_option(q_ctx, labels_only, role_title=role_title)
                        if not chosen_lbl and labels_only:
                            chosen_lbl = labels_only[0]
                        if chosen_lbl:
                            for lbl, c_el in opt_map:
                                if lbl == chosen_lbl:
                                    c_el.check(force=True)
                                    result["fields_filled"].append({"field": q_ctx[:45], "value": chosen_lbl[:40]})
                                    break
                    except Exception:
                        continue

                for chk in single_chks:
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
                        if any(k in s_ctx for k in ["mobile", "countrycode", "phone", "dial"]):
                            chosen_opt = next((o for o in all_opts if "+91" in o or "india" in o.lower()), None)
                        elif any(k in s_ctx for k in ["country", "location", "where are you based", "reside"]):
                            chosen_opt = (
                                next((o for o in all_opts if o.lower() == "india"), None)
                                or next((o for o in all_opts if "gurugram" in o.lower() or "gurgaon" in o.lower() or "delhi" in o.lower()), None)
                                or next((o for o in all_opts if "india" in o.lower()), None)
                            )
                        if not chosen_opt:
                            chosen_opt = _decide_yes_no_or_option(s_ctx, all_opts, role_title=role_title)
                        if not chosen_opt:
                            for opt in all_opts:
                                ol = opt.lower()
                                if ol in ("india", "delhi", "gurugram", "gurgaon", "b.tech", "bachelor's degree", "full-time", "full time"):
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

                        post_url = page.url.lower()
                        try:
                            post_text = page.locator("body").inner_text().lower()
                        except Exception:
                            post_text = ""

                        # Strict compliance: if Greenhouse or ATS shows a human-confirmation security code gate,
                        # report it honestly without attempting to bypass 'confirm you're a human'.
                        sec_inp0 = page.locator("input#security-input-0, input[id^='security-input-']").first
                        if (
                            (sec_inp0.count() > 0 and sec_inp0.is_visible())
                            or "verification code was sent to" in post_text
                            or ("security code" in post_text and "8-character" in post_text)
                        ):
                            print("[ATSFiller] ATS requested candidate human-verification email code; stopping cleanly for user input.")
                            result["confirmed"] = False
                            result["error"] = "human_verification_code_required_in_email"
                            break

                        if _has_interactive_captcha(page):
                            print("[ATSFiller] Interactive CAPTCHA challenge appeared after Submit; refusing to bypass or mark applied.")
                            result["confirmed"] = False
                            result["error"] = "captcha_gate_requires_manual_apply"
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
            if not leave_open:
                if target_page and existing_page is None:
                    try:
                        target_page.close()
                    except Exception:
                        pass
                if context:
                    try:
                        context.close()
                    except Exception:
                        pass
                if owns_browser and browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
            if lock_fh is not None:
                try:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)
                    lock_fh.close()
                except Exception:
                    pass

    return result
