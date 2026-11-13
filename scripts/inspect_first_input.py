import time
import json
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-precision-errs")
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

    script = """() => {
        const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
        
        // Find all inputs and their labels
        const inputs = Array.from(modal.querySelectorAll('input:not([type="radio"]):not([type="checkbox"])'));
        return inputs.map(i => {
            const container = i.closest('.fb-dash-form-element, .jobs-easy-apply-form-element') || i.parentElement.parentElement;
            return {
                id: i.id,
                value: i.value,
                required: i.required,
                ariaRequired: i.getAttribute('aria-required'),
                placeholder: i.placeholder,
                containerText: container ? container.innerText.trim() : ''
            };
        });
    }"""
    res = page.evaluate(script)
    print(json.dumps(res, indent=2))
    page.close()
