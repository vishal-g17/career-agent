import time
import json
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"
RESUME_PDF = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-precision")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)

    # Click Easy Apply
    easy_apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply"), button:has-text("Easy Apply")').first
    if not easy_apply_btn.is_visible(timeout=5000):
        print("Easy Apply button not visible! Maybe already applied?")
        exit(0)
    
    easy_apply_btn.click()
    time.sleep(2)

    modal = page.locator('dialog:has-text("Apply to"), div.jobs-easy-apply-modal, div[role="dialog"]').first

    # Advance through steps 1, 2, 3
    for step in range(3):
        print(f"Advancing step {step+1}...")
        try:
            file_input = modal.locator('input[type="file"]').first
            if file_input.count() > 0:
                file_input.set_input_files(RESUME_PDF)
        except:
            pass
        next_btn = modal.locator('button:has-text("Next")').first
        if next_btn.is_visible():
            next_btn.click()
            time.sleep(2)

    print("Now on Step 4! Let's inspect all fields:")
    dump_script = """() => {
        const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
        if (!modal) return { error: 'no modal' };

        const items = [];
        const formEls = modal.querySelectorAll('.fb-dash-form-element, .jobs-easy-apply-form-element, fieldset, .artdeco-inline-feedback--error');
        
        // Let's get all labels and their input types/values/options
        const fieldsets = modal.querySelectorAll('fieldset');
        const fieldsetInfo = [];
        fieldsets.forEach(fs => {
            const legend = fs.querySelector('legend')?.innerText || '';
            const radios = Array.from(fs.querySelectorAll('input[type="radio"]')).map(r => ({
                id: r.id,
                value: r.value,
                checked: r.checked,
                label: fs.querySelector(`label[for="${r.id}"]`)?.innerText || r.parentElement?.innerText || ''
            }));
            fieldsetInfo.push({ legend, radios });
        });

        const inputs = Array.from(modal.querySelectorAll('input, select, textarea')).map(el => {
            const labelEl = el.id ? modal.querySelector(`label[for="${el.id}"]`) : el.closest('label');
            return {
                tag: el.tagName,
                type: el.type,
                id: el.id,
                name: el.name,
                label: labelEl?.innerText || el.getAttribute('aria-label') || el.placeholder || '',
                value: el.value,
                required: el.required || el.getAttribute('aria-required'),
                options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => ({ text: o.text, value: o.value, selected: o.selected })) : null
            };
        });

        const errors = Array.from(modal.querySelectorAll('.artdeco-inline-feedback--error, [data-test-form-element-error-messages]')).map(e => e.innerText);

        return { fieldsetInfo, inputs, errors, modalTitle: modal.querySelector('h3, h2, h1')?.innerText };
    }"""
    
    # First, let's click Next to see what errors pop up!
    next_btn = modal.locator('button:has-text("Next"), button:has-text("Review")').first
    print("Clicking Next on Step 4 to trigger validation...")
    next_btn.click()
    time.sleep(2)

    data = page.evaluate(dump_script)
    print(json.dumps(data, indent=2))
    
    page.screenshot(path="data/step4_inspection.png")
    print("Saved screenshot to data/step4_inspection.png")
    page.close()
