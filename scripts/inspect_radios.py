import time
import json
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-precision-rl")
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
        const radios = document.querySelectorAll('input[type="radio"]');
        return Array.from(radios).map(r => {
            const labelEl = document.querySelector(`label[for="${r.id}"]`);
            return {
                id: r.id,
                name: r.name,
                value: r.value,
                labelText: labelEl ? labelEl.innerText : 'NO_FOR_LABEL',
                parentText: r.parentElement ? r.parentElement.innerText : '',
                grandParentText: r.parentElement && r.parentElement.parentElement ? r.parentElement.parentElement.innerText : ''
            };
        });
    }"""
    res = page.evaluate(script)
    print(json.dumps(res, indent=2))
    page.close()
