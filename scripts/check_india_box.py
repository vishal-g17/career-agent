import time
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-osome-boxes")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://careers.osome.com/jobs/8690455-product-analyst-intelligence", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    apply_btn = page.locator('a:has-text("Apply for this job"), button:has-text("Apply for this job")').first
    if apply_btn.is_visible():
        apply_btn.click()
        time.sleep(2)

    script = """() => {
        const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]')).map(c => ({
            id: c.id,
            name: c.name,
            value: c.value,
            label: c.closest('label') ? c.closest('label').innerText.trim() : (c.parentElement ? c.parentElement.innerText.trim() : '')
        }));
        const allInputs = Array.from(document.querySelectorAll('input, select, textarea')).map(i => ({
            tag: i.tagName,
            type: i.type,
            name: i.name,
            id: i.id,
            placeholder: i.placeholder
        }));
        return { checkboxes, allInputs };
    }"""
    data = page.evaluate(script)
    print(json.dumps(data, indent=2))
    page.close()
