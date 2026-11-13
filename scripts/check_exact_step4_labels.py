import time
import json
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-precision-who")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)
    page.locator('button:has-text("Easy Apply")').first.click()
    time.sleep(2)
    modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]').first

    for _ in range(3):
        modal.locator('button:has-text("Next")').first.click()
        time.sleep(1.5)

    # Let's inspect all inputs and their labels on Step 4
    info = page.evaluate("""() => {
        const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
        const inputs = Array.from(modal.querySelectorAll('input:not([type="radio"]):not([type="hidden"]), textarea, select'));
        return inputs.map(i => {
            const formEl = i.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, div[data-test-form-element]');
            const lbl = formEl ? formEl.querySelector('label')?.innerText : null;
            return {
                id: i.id,
                tag: i.tagName,
                type: i.type,
                label: lbl || i.getAttribute('aria-label') || i.placeholder,
                val: i.value,
                required: i.required || i.getAttribute('aria-required')
            };
        });
    }""")
    print(json.dumps(info, indent=2))
    page.close()
