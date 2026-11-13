from playwright.sync_api import sync_playwright
import urllib.parse
import re

ENDPOINT = "http://127.0.0.1:9230/default/post-search-test"
QUERY = 'hiring ("product analyst" OR "business analyst" OR "APM")'
URL = f"https://www.linkedin.com/search/results/content/?keywords={urllib.parse.quote(QUERY)}&sortBy=%22date_posted%22"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(ENDPOINT)
    context = browser.contexts[0]
    page = context.new_page() if not context.pages else context.pages[0]
    
    print(f"Navigating to LinkedIn Content Search: {URL}")
    page.goto(URL, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(3500)
    
    # Scroll a bit
    for _ in range(4):
        page.mouse.wheel(0, 1000)
        page.wait_for_timeout(800)
        
    text = page.inner_text("main")
    print(f"Fetched {len(text)} characters of post text.")
    
    # Extract emails
    email_regex = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
    found_emails = list(set(email_regex.findall(text)))
    print(f"Emails found in fresh hiring posts: {found_emails}")
    
    # Extract sample post snippets
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 60]
    print(f"\nSample post snippets ({len(lines)} total):")
    for l in lines[:6]:
        print(" ->", l[:120])
        
    page.screenshot(path="/Users/admin/.gemini/antigravity-cli/brain/60fa42fe-7829-4c81-bde3-7c764da495f2/post_search_sample.png")
    page.close()
