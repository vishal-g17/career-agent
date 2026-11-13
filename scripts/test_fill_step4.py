import time
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/test-fill")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)
    page.locator('button:has-text("Easy Apply")').first.click()
    time.sleep(2)
    modal = page.locator('div.jobs-easy-apply-modal, dialog').first

    # Steps 1 to 3
    for _ in range(3):
        modal.locator('button:has-text("Next")').first.click()
        time.sleep(1.5)

    print("Now on Step 4! Filling using native Playwright locators...")
    
    # 1. Desired salary
    salary_el = modal.locator('input[id*="3b"], input[aria-label*="desired salary" i], label:has-text("desired salary") + input, label:has-text("desired salary") ~ div input').first
    if salary_el.count() == 0:
        # Fallback to finding by label text
        salary_el = modal.get_by_label("What is your desired salary?", exact=False).first
    print("Found salary input:", salary_el.count())
    salary_el.fill("1200000")

    # 2. LinkedIn profile
    try:
        modal.get_by_label("LinkedIn Profile", exact=False).fill("https://www.linkedin.com/in/vishal-gupta-product/")
    except Exception as e:
        print("LinkedIn profile fill error:", e)

    # 3. Radios:
    # Authorized: Yes
    modal.locator('fieldset:has-text("authorized to work") input[type="radio"]').first.click()
    # Sponsorship: No
    modal.locator('fieldset:has-text("require Precision Medicine Group to commence") input[type="radio"]').nth(1).click()
    # Worked for company: No
    modal.locator('fieldset:has-text("ever worked for Precision") input[type="radio"]').nth(1).click()
    # Interviewed: No
    modal.locator('fieldset:has-text("ever interviewed with Precision") input[type="radio"]').nth(1).click()
    # Certify: Yes
    modal.locator('fieldset:has-text("certify that the information") input[type="radio"]').first.click()

    # 4. Selects:
    # Non-compete: No
    modal.locator('select:has-text("Non-Compete"), select[id*="42"]').first.select_option(label="No")
    # Work authorization: Citizen
    modal.locator('select:has-text("work authorization"), select[id*="44"]').first.select_option(label="Citizen")
    # Legal Country: India
    modal.locator('select[id*="4e"], select:has-text("Country")').first.select_option(label="India")

    # 5. Text inputs:
    modal.locator('input[id*="46"]').fill("N/A - Citizen")
    modal.locator('input[id*="4c"]').fill("Sector 3, Rohini")
    modal.locator('input[id*="4d"]').fill("Delhi")

    # 6. Textareas:
    SQL_ANSWER = (
        "At my product analytics role, I analyzed high drop-offs in our checkout & activation funnel. "
        "I constructed multi-step funnel telemetry queries using SQL CTEs and window functions (LAG/LEAD, ROW_NUMBER) "
        "across millions of session events to identify that users dropped off by 28% at payment method selection due to latency. "
        "By aggregating cohort retention metrics and segmenting by device and payment gateway, we proved the bottleneck to engineering. "
        "Fixing the gateway retry logic increased checkout conversion by 14% and recovered recurring MRR."
    )

    HEALTHCARE_ANSWER = (
        "I have analyzed patient engagement telemetry, appointment booking workflows, and health/wellness transactional event streams. "
        "My focus was optimizing conversion funnels, tracking user behavior cohorts, and building automated data pipelines in SQL/PostgreSQL "
        "to monitor retention and SLA compliance. I am experienced in working with large-scale relational schemas, handling compliance standards, "
        "and translating complex health event streams into clear executive dashboards."
    )

    modal.locator('textarea[id*="4g"], textarea:has-text("SQL")').first.fill(SQL_ANSWER)
    modal.locator('textarea[id*="4h"], textarea:has-text("healthcare")').first.fill(HEALTHCARE_ANSWER)

    time.sleep(1)
    print("Clicking Next/Review now...")
    next_or_review = modal.locator('button:has-text("Review"), button:has-text("Next")').first
    print("Button text:", next_or_review.inner_text())
    next_or_review.click()
    time.sleep(3)

    # Inspect current state
    title = modal.locator('h3, h2, h1').first.inner_text() if modal.locator('h3, h2, h1').count() > 0 else 'Unknown'
    btn_texts = modal.locator('button').all_inner_texts()
    print("New Modal Title:", title)
    print("New Modal Buttons:", btn_texts)

    page.screenshot(path="data/step4_after_native_fill.png")
    print("Saved screenshot to data/step4_after_native_fill.png")
    page.close()
