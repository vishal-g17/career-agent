import time
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-osome-form")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://careers.osome.com/jobs/8690455-product-analyst-intelligence", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    apply_btn = page.locator('a:has-text("Apply for this job"), button:has-text("Apply for this job")').first
    apply_btn.click()
    time.sleep(2)
    print("URL after click:", page.url)

    script = """() => {
        const inputs = Array.from(document.querySelectorAll('input, textarea, select')).map(i => {
            let lbl = i.id ? document.querySelector('label[for="' + i.id + '"]') : null;
            if (!lbl) lbl = i.closest('label');
            return {
                tag: i.tagName,
                type: i.type,
                name: i.name,
                id: i.id,
                label: lbl ? lbl.innerText.trim() : (i.placeholder || i.getAttribute('aria-label')),
                required: i.required
            };
        });
        const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]')).map(b => b.innerText || b.value);
        return { inputs, buttons };
    }"""
    form_data = page.evaluate(script)
    print(json.dumps(form_data, indent=2))
    page.screenshot(path="data/osome_form.png")
    page.close()
