from playwright.sync_api import sync_playwright
import urllib.parse

ENDPOINT = "http://127.0.0.1:9230/default/swiggy-search"
URL = "https://www.linkedin.com/search/results/people/?keywords=Swiggy&network=%5B%22F%22%5D"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(ENDPOINT)
    context = browser.contexts[0]
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(3000)
    
    # Extract profiles
    cards = page.locator('li.reusable-search__result-container, div.entity-result').all()
    print(f"Found {len(cards)} 1st-degree connections at Swiggy:")
    for c in cards[:10]:
        title_el = c.locator('.entity-result__title-text a').first
        name = title_el.inner_text().strip().split("\n")[0]
        url = title_el.get_attribute("href")
        headline_el = c.locator('.entity-result__primary-subtitle').first
        headline = headline_el.inner_text().strip() if headline_el.is_visible() else ""
        print(f" - {name} | {headline} | {url}")
        
    page.close()
