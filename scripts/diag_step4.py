import time
import json
from playwright.sync_api import sync_playwright

PRECISION_AQ_URL = "https://in.linkedin.com/jobs/view/product-analyst-at-precision-aq-4471378930"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/inspect-precision-diag")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(PRECISION_AQ_URL, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)
    page.locator('button:has-text("Easy Apply")').first.click()
    time.sleep(2)
    modal = page.locator('div.jobs-easy-apply-modal, dialog').first

    for s in range(3):
        modal.locator('button:has-text("Next")').first.click()
        time.sleep(1.5)

    # Now on Step 4. Let's run the fill logic from submit_precision_aq_final.py
    with open("/Users/admin/linkedin-multiagent/scripts/submit_precision_aq_final.py") as f:
        full_code = f.read()

    # Call the exact evaluate script
    from submit_precision_aq_final import SQL_ANSWER, HEALTHCARE_ANSWER
    fill_js = f"""() => {{
        const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
        if (!modal) return {{ error: 'no_modal' }};
        const logs = [];

        // 1. Fill Text / Textareas
        const inputs = modal.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="radio"]):not([type="checkbox"]), textarea');
        inputs.forEach(inp => {{
            let label = inp.getAttribute('aria-label') || inp.placeholder || '';
            if (!label && inp.id) {{
                const lblEl = modal.querySelector(`label[for="${{inp.id}}"]`);
                if (lblEl) label = lblEl.innerText;
            }}
            if (!label) {{
                const lblEl = inp.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, div')?.querySelector('label');
                if (lblEl) label = lblEl.innerText;
            }}
            const clean = (label || '').toLowerCase();

            let ans = null;
            if (clean.includes('sql') || clean.includes('complex')) {{
                ans = {json.dumps(SQL_ANSWER)};
            }} else if (clean.includes('healthcare') || clean.includes('claims') || clean.includes('patient')) {{
                ans = {json.dumps(HEALTHCARE_ANSWER)};
            }} else if (clean.includes('salary') || clean.includes('desired') || clean.includes('ctc')) {{
                ans = 'Negotiable';
            }} else if (clean.includes('linkedin')) {{
                ans = 'https://www.linkedin.com/in/vishal-gupta-product/';
            }} else if (clean.includes('street') || clean.includes('address 1')) {{
                ans = 'Sector 3, Rohini';
            }} else if (clean.includes('city') || clean.includes('location')) {{
                ans = 'Delhi';
            }} else if (clean.includes('postal') || clean.includes('zip')) {{
                ans = '110085';
            }} else if (clean.includes('other') && (clean.includes('visa') || clean.includes('expiration'))) {{
                ans = 'N/A - Citizen';
            }} else if (clean.includes('if yes, when') || clean.includes('which company')) {{
                ans = 'N/A';
            }} else if (clean.includes('notice')) {{
                ans = '15 days';
            }} else if (clean.includes('year') || clean.includes('how many')) {{
                ans = '1';
            }}

            if (ans !== null) {{
                inp.value = ans;
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                logs.push('Text: ' + clean.substring(0, 25) + ' -> ' + ans.substring(0, 15));
            }}
        }});

        // 2. Selects
        const selects = modal.querySelectorAll('select');
        selects.forEach(sel => {{
            let label = sel.getAttribute('aria-label') || '';
            if (!label && sel.id) {{
                const lblEl = modal.querySelector(`label[for="${{sel.id}}"]`);
                if (lblEl) label = lblEl.innerText;
            }}
            if (!label) {{
                const lblEl = sel.closest('div')?.querySelector('label');
                if (lblEl) label = lblEl.innerText;
            }}
            const clean = (label || '').toLowerCase();

            let chosen = null;
            for (let i = 0; i < sel.options.length; i++) {{
                const optText = sel.options[i].text.toLowerCase();
                if (clean.includes('non-compete') || clean.includes('non-solicit')) {{
                    if (optText === 'no') {{ chosen = i; break; }}
                }} else if (clean.includes('work authorization') || clean.includes('type of valid')) {{
                    if (optText.includes('citizen')) {{ chosen = i; break; }}
                }} else if (clean.includes('country') || clean.includes('legal address')) {{
                    if (optText === 'india') {{ chosen = i; break; }}
                }} else if (clean.includes('worked') || clean.includes('employed')) {{
                    if (optText.includes('no')) {{ chosen = i; break; }}
                }}
            }}
            if (chosen !== null) {{
                sel.selectedIndex = chosen;
                sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                logs.push('Select: ' + clean.substring(0, 25) + ' -> ' + sel.options[chosen].text);
            }}
        }});

        // 3. Fieldsets & Radios
        const fieldsets = modal.querySelectorAll('fieldset');
        fieldsets.forEach(fs => {{
            const legend = (fs.querySelector('legend')?.innerText || fs.closest('div')?.querySelector('span, label, p')?.innerText || '').toLowerCase();
            const radios = fs.querySelectorAll('input[type="radio"]');
            if (radios.length === 0) return;

            let targetVal = 'yes';
            if (legend.includes('sponsor') || legend.includes('immigration')) {{
                targetVal = 'no';
            }} else if (legend.includes('ever worked') || legend.includes('previously worked')) {{
                targetVal = 'no';
            }} else if (legend.includes('ever interviewed') || legend.includes('previously interviewed')) {{
                targetVal = 'no';
            }} else if (legend.includes('authorized to work')) {{
                targetVal = 'yes';
            }} else if (legend.includes('certify') || legend.includes('complete and accurate')) {{
                targetVal = 'yes';
            }}

            radios.forEach(r => {{
                let rText = '';
                let container = r;
                for (let c = 0; c < 5; c++) {{
                    if (!container.parentElement) break;
                    container = container.parentElement;
                    if (container.innerText && container.innerText.trim().length > 0 && container.innerText.trim().length < 30) {{
                        rText = container.innerText.trim().toLowerCase();
                        break;
                    }}
                }}
                if (rText === targetVal || rText.startsWith(targetVal)) {{
                    r.click();
                    r.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    logs.push('Radio (' + targetVal + '): ' + legend.substring(0, 25));
                }}
            }});
        }});

        return {{ logs }};
    }}"""

    actions = page.evaluate(fill_js)
    print("Actions taken on Step 4:", actions)
    time.sleep(1)

    print("Clicking Next now...")
    next_btn = modal.locator('button:has-text("Next"), button:has-text("Review")').first
    next_btn.click()
    time.sleep(2)

    # Let's inspect everything on the screen now
    diag = page.evaluate("""() => {
        const modal = document.querySelector('dialog[open], div.jobs-easy-apply-modal, div[role="dialog"]');
        const errs = Array.from(modal.querySelectorAll('.artdeco-inline-feedback, [data-test-form-element-error-messages], .fb-dash-form-element--error, [aria-invalid="true"]')).map(e => e.innerText);
        const title = modal.querySelector('h3, h2, h1')?.innerText;
        const buttons = Array.from(modal.querySelectorAll('button')).map(b => b.innerText.trim());
        const allInputs = Array.from(modal.querySelectorAll('input, select, textarea')).map(i => ({
            tag: i.tagName,
            type: i.type,
            val: i.value,
            checked: i.checked,
            id: i.id,
            required: i.required || i.getAttribute('aria-required')
        }));
        return { title, buttons, errs, allInputs };
    }""")
    print("Post-Next State:")
    print("Title:", diag.get("title"))
    print("Buttons:", diag.get("buttons"))
    print("Errors:", diag.get("errs"))

    modal.screenshot(path="data/step4_after_click.png")
    print("Screenshot saved to data/step4_after_click.png")
    page.close()
