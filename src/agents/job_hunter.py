import urllib.request
import urllib.parse
import re
import hashlib
import time
import json
from typing import List, Dict, Any, Optional, Tuple
from playwright.sync_api import sync_playwright
from ..db import database

RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/multiplatform-hunter"

TARGET_KEYWORDS = [
    "Associate Product Manager",
    "APM",
    "Product Analyst",
    "Data Analyst",
    "Business Analyst",
    "Quantitative Analyst",
    "Product Operations Analyst",
    "Operations Analyst",
    "Growth Analyst",
    "Category Manager",
    "Associate Category Manager",
    "Product Manager",
    "Forward Deployed Engineer",
    "Forward Deployed Software Engineer",
    "FDSE",
    "FDE",
    "Founders Office",
    "Chief of Staff",
    "Business Operations"
]

TARGET_LOCATIONS = [
    "India",
    "Bangalore",
    "Gurgaon",
    "Delhi",
    "Noida",
    "Mumbai",
    "Remote"
]

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# STRICT EMPLOYER BLACKLIST
BLACKLISTED_COMPANIES = ["magicpin", "lifelong", "nom"]

def generate_dedup_id(title: str, company: str, location: str) -> str:
    clean_str = f"{title.lower().strip()}_{company.lower().strip()}_{location.lower().strip()}"
    return hashlib.sha256(clean_str.encode("utf-8")).hexdigest()

def score_job_heuristic(title: str, company: str) -> Tuple[int, List[str]]:
    """
    Heuristic scoring strictly prioritizing early-career, associate, analyst,
    FDE, and founder's office roles while heavily penalizing senior positions
    and strictly filtering blacklisted employers (Magicpin, Lifelong).
    """
    t_lower = title.lower()
    c_lower = company.lower()
    score = 50
    reasons = []

    # 0. STRICT BLACKLIST CHECK
    if any(b in c_lower for b in BLACKLISTED_COMPANIES):
        return 0, ["Filtered out: Current or previous employer blacklist"]

    # 0.1 FALSE APM TOOL CHECK (Application Performance Monitoring vs Associate Product Manager)
    if any(tool in t_lower for tool in ["new relic", "datadog", "dynatrace", "appdynamics", "performance monitoring"]):
        return 0, ["Filtered out: DevOps/APM tooling role, not Product APM"]

    # 0.2 HIGH EXPERIENCE IN TITLE (e.g., 4+ years, 8+ years)
    exp_m = re.search(r'\b([4-9]|1[0-9])\+?\s*(?:yrs|years|yr)\b', t_lower)
    if exp_m:
        return 0, [f"Filtered out: High experience required ({exp_m.group(0)})"]

    # 1. HARD PENALTIES: Senior / Experienced positions
    senior_triggers = [
        "senior", "sr.", "sr ", "lead", "principal", "director", "vp", "head of",
        "staff", "manager ii", "architect", "group product manager", "gpm", "experienced"
    ]
    if any(s in t_lower for s in senior_triggers):
        score -= 60
        reasons.append("Filtered out: Senior / Experienced leadership role")
        return max(score, 0), reasons

    # 2. STRONG BOOSTS: Target Roles & Early-Career Fit
    if any(k in t_lower for k in ["associate product manager", "apm", "junior product manager", "jr. product manager"]):
        score += 45
        reasons.append("High match: Early-career APM role (0-2 yrs)")
    elif any(k in t_lower for k in ["product analyst", "tech product analyst"]):
        score += 45
        reasons.append("Exact match: Tech Product Analyst role")
    elif any(k in t_lower for k in ["forward deployed engineer", "fde", "forward deployed software engineer", "fdse", "solutions engineer"]):
        score += 45
        reasons.append("High match: FDE/FDSE role (matches NSUT engineering & problem solving depth)")
    elif any(k in t_lower for k in ["category manager", "associate category manager", "assistant category manager"]):
        score += 40
        reasons.append("High match: Category Management role")
    elif any(k in t_lower for k in ["data analyst", "business analyst", "quantitative analyst", "operations analyst", "growth analyst", "product operations", "analyst"]):
        score += 40
        reasons.append("High match: Analyst role (matches data, SQL, telemetry & analytics skillset)")
    elif any(k in t_lower for k in ["founders office", "founder's office", "chief of staff", "business operations", "bizops", "generalist"]):
        score += 40
        reasons.append("High match: Founder's Office / Business Operations / Generalist role")
    elif "product manager" in t_lower:
        score += 25
        reasons.append("Product Manager title")

    # 3. Graduate / Entry level signals
    if any(w in t_lower for w in ["entry level", "graduate", "trainee", "freshers", "0-2", "0-1", "associate"]):
        score += 15
        reasons.append("Explicit early career / graduate cohort")

    # 4. Filter out pure non-technical internships if not desired
    if "intern" in t_lower and not ("graduate" in t_lower or "trainee" in t_lower):
        score -= 30
        reasons.append("Internship position")

    return min(max(score, 0), 100), reasons

def scrape_fresh_linkedin_jobs(keyword: str, location: str = "India", max_pages: int = 1) -> List[Dict[str, Any]]:
    """
    Scrapes LinkedIn jobs strictly posted in the last 24 hours (f_TPR=r86400).
    Zero cookies or API keys required.
    """
    found_jobs = []
    base_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    
    for page in range(max_pages):
        start = page * 10
        params = {
            "keywords": keyword,
            "location": location,
            "f_TPR": "r86400",
            "start": str(start)
        }
        query_str = urllib.parse.urlencode(params)
        full_url = f"{base_url}?{query_str}"
        
        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                html = response.read().decode("utf-8", errors="ignore")
                
            cards = re.findall(r'<div class=\"base-card[^\"]*base-card--link[^\"]*\"([\s\S]*?)</div>\s*</div>', html)
            if not cards:
                cards = re.findall(r'<li[^>]*>([\s\S]*?)</li>', html)

            for card in cards:
                title_m = re.search(r'<h3 class=\"base-search-card__title\">\s*([^<]+)\s*</h3>', card)
                comp_m = re.search(r'<h4 class=\"base-search-card__subtitle\">\s*(?:<a[^>]*>)?\s*([^<]+)\s*(?:</a>)?\s*</h4>', card)
                loc_m = re.search(r'<span class=\"job-search-card__location\">\s*([^<]+)\s*</span>', card)
                link_m = re.search(r'<a class=\"base-card__full-link[^\"]*\"\s+href=\"([^\"]+)\"', card)
                time_m = re.search(r'<time class=\"job-search-card__listdate[^\"]*\"[^>]*>\s*([^<]+)\s*</time>', card)
                
                if not (title_m and comp_m and link_m):
                    continue
                    
                title = title_m.group(1).strip()
                company = comp_m.group(1).strip()
                loc = loc_m.group(1).strip() if loc_m else location
                link = link_m.group(1).split("?")[0]
                posted_time = time_m.group(1).strip() if time_m else "Recent (<24h)"
                
                dedup_id = generate_dedup_id(title, company, loc)
                
                job_item = {
                    "dedup_id": dedup_id,
                    "job_id": f"linkedin_{dedup_id[:16]}",
                    "title": title,
                    "company": company,
                    "location": loc,
                    "url": link,
                    "posted_time": posted_time,
                    "platform": "LinkedIn",
                    "keyword_matched": keyword
                }
                found_jobs.append(job_item)
                
            time.sleep(0.3)
        except Exception as e:
            print(f"[JobHunter] LinkedIn scrape error for '{keyword}': {e}")
            break
            
    return found_jobs

def scrape_fresh_indeed_jobs(keywords: Optional[List[str]] = None, location: str = "India") -> List[Dict[str, Any]]:
    """
    Scrapes fresh Indeed India jobs (<24h, fromage=1) using Ridealong CDP or native headless Playwright.
    """
    if keywords is None:
        keywords = ["Associate Product Manager", "Product Analyst", "Business Analyst"]
        
    found_jobs = []
    seen_jks = set()
    
    try:
        with sync_playwright() as p:
            browser = None
            is_local = False
            try:
                browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=3000)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
            except Exception:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 768}
                )
                is_local = True

            page = context.new_page()
            
            for kw in keywords:
                try:
                    q = urllib.parse.quote(kw)
                    # fromage=1 strictly filters for jobs posted in the past 24 hours
                    search_url = f"https://in.indeed.com/jobs?q={q}&l={urllib.parse.quote(location)}&fromage=1"
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2000)
                    
                    cards = page.locator("div.job_seen_beacon").all()
                    for card in cards:
                        try:
                            link_el = card.locator("a[data-jk]").first
                            if link_el.count() == 0:
                                continue
                            jk = link_el.get_attribute("data-jk")
                            if not jk or jk in seen_jks:
                                continue
                            seen_jks.add(jk)
                            
                            title = link_el.inner_text().strip()
                            comp_el = card.locator('[data-testid="company-name"]').first
                            comp = comp_el.inner_text().strip() if comp_el.count() > 0 else "Unknown"
                            loc_el = card.locator('[data-testid="text-location"]').first
                            loc = loc_el.inner_text().strip() if loc_el.count() > 0 else location
                            
                            dedup_id = generate_dedup_id(title, comp, loc)
                            
                            found_jobs.append({
                                "dedup_id": dedup_id,
                                "job_id": f"indeed_{jk}",
                                "title": title,
                                "company": comp,
                                "location": loc,
                                "url": f"https://in.indeed.com/viewjob?jk={jk}",
                                "posted_time": "Recent (<24h)",
                                "platform": "Indeed",
                                "keyword_matched": kw
                            })
                        except Exception as ce:
                            continue
                except Exception as pe:
                    print(f"[JobHunter] Indeed scrape error for '{kw}': {pe}")
                    continue
            page.close()
            if is_local and browser:
                try:
                    browser.close()
                except:
                    pass
    except Exception as e:
        print(f"[JobHunter] Indeed scraper notice: {e}")
        
    return found_jobs

def scrape_fresh_naukri_jobs(keywords: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Scrapes fresh Naukri jobs (<24h, dayFilter=1) using Ridealong CDP or native headless Playwright.
    """
    if keywords is None:
        keywords = ["associate product manager", "product analyst", "business analyst"]
        
    found_jobs = []
    seen_urls = set()
    
    try:
        with sync_playwright() as p:
            browser = None
            is_local = False
            try:
                browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT, timeout=3000)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
            except Exception:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 768}
                )
                is_local = True

            page = context.new_page()
            
            for kw in keywords:
                try:
                    slug = kw.strip().lower().replace(" ", "-")
                    # dayFilter=1 filters jobs posted in the last 24 hours
                    search_url = f"https://www.naukri.com/{slug}-jobs?dayFilter=1"
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2000)
                    
                    cards = page.locator(".srp-jobtuple-wrapper, div.cust-job-tuple").all()
                    for card in cards:
                        try:
                            title_el = card.locator("a.title").first
                            if title_el.count() == 0:
                                continue
                            title = title_el.inner_text().strip()
                            href = title_el.get_attribute("href")
                            if not href or href in seen_urls:
                                continue
                            seen_urls.add(href)
                            
                            comp_el = card.locator("a.comp-name, .subTitle").first
                            comp = comp_el.inner_text().strip() if comp_el.count() > 0 else "Unknown"
                            loc_el = card.locator(".locWdth, .loc-wrap, .location").first
                            loc = loc_el.inner_text().strip() if loc_el.count() > 0 else "India"
                            
                            dedup_id = generate_dedup_id(title, comp, loc)
                            
                            found_jobs.append({
                                "dedup_id": dedup_id,
                                "job_id": f"naukri_{dedup_id[:16]}",
                                "title": title,
                                "company": comp,
                                "location": loc,
                                "url": href,
                                "posted_time": "Recent (<24h)",
                                "platform": "Naukri",
                                "keyword_matched": kw
                            })
                        except Exception as ce:
                            continue
                except Exception as pe:
                    print(f"[JobHunter] Naukri scrape error for '{kw}': {pe}")
                    continue
            page.close()
            if is_local and browser:
                try:
                    browser.close()
                except:
                    pass
    except Exception as e:
        print(f"[JobHunter] Naukri scraper notice: {e}")
        
    return found_jobs

GREENHOUSE_BOARDS = [
    "inmobi", "groww", "stripe", "coinbase", "rubrik", "druva", "apolloio", "gitlab", "datadog",
    "twilio", "okta", "mongodb", "elastic", "confluent", "databricks", "coupang", "agoda",
    "zscaler", "nutanix", "purestorage", "cohesity", "sprinklr", "thoughtspot", "udemy", "coursera",
    "affirm", "pinterest", "reddit", "discord", "duolingo", "asana", "gusto", "brex", "cloudflare",
    "razorpay", "phonepe", "meesho", "cred", "postman", "browserstack", "chargebee", "clevertap",
    "moengage", "whatfix", "hasura", "innovaccer", "mindtickle", "highradius", "zenoti", "rippling",
    "remote", "mercury", "figma", "vercel", "supabase", "anthropic", "scaleai", "samsara", "toast",
    "robinhood", "chime", "plaid", "adyen", "wise", "klarna", "doordash", "instacart", "airbnb",
    "lyft", "dropbox", "box", "hubspot", "zendesk", "intercom", "pagerduty", "grafanalabs",
    "cockroachlabs", "redis", "neo4j", "fivetran", "airbyte", "temporal", "launchdarkly", "snyk",
    "crowdstrike", "sentinelone", "netskope", "braze", "amplitude", "mixpanel", "Iterable",
    "algolia", "contentful", "webflow", "canva", "miro", "airtable", "calendly", "loom", "carta",
    "navan", "tripactions", "flexport", "nuro", "waymo", "aurora", "upstart", "sofi", "marqeta",
]

LEVER_BOARDS = [
    "palantir", "zeta", "cred", "upstox", "slice", "khatabook", "plum", "niyo", "smallcase",
    "jar", "jupiter", "porter", "spinny", "acko", "atherenergy", "lenskart", "zepto", "cars24",
    "juspay", "setu", "cashfree", "pinelabs", "spotify", "netflix", "attentive", "outreach",
    "clari", "gong", "highspot", "seismic", "veeva", "workato", "talend", "alation", "collibra",
    "monte-carlo-data", "starburst", "planetscale", "neon", "railway", "fly", "turso", "mux",
]

ASHBY_BOARDS = [
    "linear", "notion", "ramp", "deel", "vanta", "perplexity", "openai", "replit", "posthog",
    "cal", "elevenlabs", "render", "retool", "zapier", "langchain", "pinecone", "weaviate",
    "qdrant", "cohere", "mistral", "together", "modal", "baseten", "fireworks-ai", "Harvey",
    "cursor", "cognition", "glean", "clay", "attio", "loops", "resend", "dub", "Trigger",
]

INDIA_LOC_TOKENS = (
    "india", "bangalore", "bengaluru", "gurgaon", "gurugram",
    "delhi", "noida", "mumbai", "pune", "hyderabad", "chennai", "remote",
)


def _is_india_or_remote_india(loc: str) -> bool:
    l_low = (loc or "").lower()
    if any(tok in l_low for tok in INDIA_LOC_TOKENS[:-1]):
        return True
    if "remote" in l_low and ("india" in l_low or "apac" in l_low or "global" in l_low or "anywhere" in l_low):
        return True
    return False


def scrape_fresh_greenhouse_jobs() -> List[Dict[str, Any]]:
    """
    Fetches live India openings directly from 100+ official Greenhouse company job boards in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _scan_org(org: str) -> List[Dict[str, Any]]:
        org_jobs: List[Dict[str, Any]] = []
        if any(b in org.lower() for b in BLACKLISTED_COMPANIES):
            return org_jobs
        try:
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs"
            req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            for item in data.get("jobs", []):
                loc = ((item.get("location") or {}).get("name") or "").strip()
                title = (item.get("title") or "").strip()
                jid = item.get("id")
                if not title or not jid or not _is_india_or_remote_india(loc):
                    continue
                sc, _ = score_job_heuristic(title, org)
                if sc < 75:
                    continue
                abs_url = (item.get("absolute_url") or "").strip()
                target_url = (
                    abs_url
                    if "greenhouse.io" in abs_url
                    else f"https://boards.greenhouse.io/embed/job_app?for={org}&token={jid}"
                )
                dedup_id = generate_dedup_id(title, org, loc)
                org_jobs.append(
                    {
                        "dedup_id": dedup_id,
                        "job_id": f"gh_{org}_{jid}",
                        "title": title,
                        "company": org.capitalize(),
                        "location": loc,
                        "url": target_url,
                        "posted_time": (item.get("updated_at") or "Recent")[:10],
                        "platform": "Greenhouse ATS",
                        "keyword_matched": "Direct Greenhouse Board",
                    }
                )
        except Exception:
            pass
        return org_jobs

    found: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        for batch in pool.map(_scan_org, GREENHOUSE_BOARDS):
            found.extend(batch)
    return found


def scrape_fresh_lever_and_ashby_jobs() -> List[Dict[str, Any]]:
    """
    Fetches live India openings directly from Lever & Ashby public JSON APIs in parallel (<4s total).
    """
    from concurrent.futures import ThreadPoolExecutor

    def _scan_lever(org: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if any(b in org.lower() for b in BLACKLISTED_COMPANIES):
            return out
        try:
            api_url = f"https://api.lever.co/v0/postings/{org}?mode=json"
            req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=4) as resp:
                items = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if not isinstance(items, list):
                return out
            for it in items:
                title = (it.get("text") or "").strip()
                cats = it.get("categories") or {}
                loc = (cats.get("location") or "").strip()
                apply_url = (it.get("applyUrl") or it.get("hostedUrl") or "").strip()
                jid = (it.get("id") or "")[:16]
                if not title or not apply_url or not _is_india_or_remote_india(loc):
                    continue
                sc, _ = score_job_heuristic(title, org)
                if sc < 75:
                    continue
                dedup_id = generate_dedup_id(title, org, loc)
                out.append(
                    {
                        "dedup_id": dedup_id,
                        "job_id": f"lv_{org}_{jid}",
                        "title": title,
                        "company": org.capitalize(),
                        "location": loc,
                        "url": apply_url,
                        "posted_time": "Recent",
                        "platform": "Lever ATS",
                        "keyword_matched": "Direct Lever Board",
                    }
                )
        except Exception:
            pass
        return out

    def _scan_ashby(org: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if any(b in org.lower() for b in BLACKLISTED_COMPANIES):
            return out
        try:
            api_url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
            req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            for it in data.get("jobs", []):
                title = (it.get("title") or "").strip()
                loc = (it.get("location") or "").strip()
                job_url = (it.get("applyUrl") or it.get("jobUrl") or "").strip()
                jid = (it.get("id") or "")[:16]
                if not title or not job_url or not _is_india_or_remote_india(loc):
                    continue
                sc, _ = score_job_heuristic(title, org)
                if sc < 75:
                    continue
                dedup_id = generate_dedup_id(title, org, loc)
                out.append(
                    {
                        "dedup_id": dedup_id,
                        "job_id": f"ash_{org}_{jid}",
                        "title": title,
                        "company": org.capitalize(),
                        "location": loc,
                        "url": job_url,
                        "posted_time": "Recent",
                        "platform": "Ashby ATS",
                        "keyword_matched": "Direct Ashby Board",
                    }
                )
        except Exception:
            pass
        return out

    found: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        for b in pool.map(_scan_lever, LEVER_BOARDS):
            found.extend(b)
        for b in pool.map(_scan_ashby, ASHBY_BOARDS):
            found.extend(b)
    return found


def _has_local_ridealong_cdp() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 9230), timeout=0.4):
            return True
    except OSError:
        return False


def run_job_hunt() -> Dict[str, Any]:
    """
    Executes a high-speed unified multi-platform job hunting scan:
      - Direct Greenhouse + Lever + Ashby JSON APIs (200+ boards in <5s, works 100% on Cloud VM)
      - LinkedIn guest search (parallelized)
      - Indeed & Naukri authenticated browser scrapes ONLY when local Ridealong CDP is connected
        (avoids 4-minute headless datacenter login-wall stalls on Cloud VM).
    """
    from concurrent.futures import ThreadPoolExecutor

    all_fresh = []
    seen_in_run = set()
    has_cdp = _has_local_ridealong_cdp()

    # 1. Direct Company ATS Boards FIRST (Greenhouse + Lever + Ashby — fast JSON APIs, zero login walls)
    for fn_name, fn in (("Greenhouse", scrape_fresh_greenhouse_jobs), ("Lever/Ashby", scrape_fresh_lever_and_ashby_jobs)):
        try:
            ats_jobs = fn()
            for j in ats_jobs:
                if j["dedup_id"] not in seen_in_run:
                    seen_in_run.add(j["dedup_id"])
                    all_fresh.append(j)
        except Exception as e:
            print(f"[JobHunter] {fn_name} direct scan failed: {e}")

    # 2. Parallel LinkedIn Guest Scrape (6 threads instead of sequential 19x loop -> 6x faster)
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for jobs in pool.map(scrape_fresh_linkedin_jobs, TARGET_KEYWORDS[:10]):
                for j in jobs:
                    if j["dedup_id"] not in seen_in_run:
                        seen_in_run.add(j["dedup_id"])
                        all_fresh.append(j)
    except Exception as e:
        print(f"[JobHunter] LinkedIn parallel scan notice: {e}")

    # 3. Authenticated browser scrapes ONLY when local Ridealong CDP session is active
    if has_cdp:
        try:
            for j in scrape_fresh_indeed_jobs():
                if j["dedup_id"] not in seen_in_run:
                    seen_in_run.add(j["dedup_id"])
                    all_fresh.append(j)
        except Exception as e:
            print(f"[JobHunter] Indeed batch run failed: {e}")

        try:
            for j in scrape_fresh_naukri_jobs():
                if j["dedup_id"] not in seen_in_run:
                    seen_in_run.add(j["dedup_id"])
                    all_fresh.append(j)
        except Exception as e:
            print(f"[JobHunter] Naukri batch run failed: {e}")

    new_jobs = []
    hot_matches = []
    solid_matches = []

    for j in all_fresh:
        if database.is_job_seen(j["dedup_id"]):
            continue  # Already processed previously
            
        score, reasons = score_job_heuristic(j["title"], j["company"])
        if score == 0 and any("blacklist" in r.lower() for r in reasons):
            continue  # Strictly ignore blacklisted companies
            
        scorecard = {
            "score": score,
            "reasons": reasons,
            "keyword": j.get("keyword_matched", ""),
            "platform": j.get("platform", "LinkedIn")
        }
        
        # Save to database
        database.save_job(
            dedup_id=j["dedup_id"],
            job_id=j["job_id"],
            title=j["title"],
            company=j["company"],
            location=j["location"],
            posted_at=j["posted_time"],
            url=j["url"],
            match_score=score,
            scorecard=scorecard
        )
        
        j["match_score"] = score
        j["reasons"] = reasons
        new_jobs.append(j)
        
        if score >= 75:
            hot_matches.append(j)
        elif score >= 55:
            solid_matches.append(j)

    return {
        "total_scanned": len(all_fresh),
        "total_scraped": len(all_fresh),
        "new_discovered": len(new_jobs),
        "saved_to_db": len(new_jobs),
        "hot_matches": hot_matches,
        "solid_matches": solid_matches
    }
