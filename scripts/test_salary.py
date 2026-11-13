import time
import json
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/test-salary")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)
    page.locator('button:has-text("Easy Apply")').first.click()
    time.sleep(2)
    modal = page.locator('div.jobs-easy-apply-modal, dialog').first

    for _ in range(3):
        modal.locator('button:has-text("Next")').first.click()
        time.sleep(1.5)

    # Let's inspect the exact HTML of the salary input
    salary_html = page.evaluate("""() => {
        const inp = document.querySelector('input[id*="3b"]');
        if (!inp) return { error: 'not found' };
        return {
            id: inp.id,
            name: inp.name,
            type: inp.type,
            outerHTML: inp.outerHTML,
            parentHTML: inp.parentElement.outerHTML,
            grandParentHTML: inp.parentElement.parentElement.outerHTML
        };
    }""")
    print("Salary Input HTML:", json.dumps(salary_html, indent=2))

    # Now let's try typing into it using page.type or locator.fill
    inp_locator = modal.locator('input[id*="3b"]').first
    print("Trying inp_locator.fill('1200000')...")
    inp_locator.fill("1200000")
    time.sleep(1)
    val = inp_locator.input_value()
    print("Value after fill:", val)

    page.close()
