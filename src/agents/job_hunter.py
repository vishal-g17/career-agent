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
    Scrapes fresh Indeed India jobs (<24h, fromage=1) using persistent Ridealong session.
    """
    if keywords is None:
        keywords = ["Associate Product Manager", "Product Analyst", "Business Analyst"]
        
    found_jobs = []
    seen_jks = set()
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            
            for kw in keywords:
                try:
                    q = urllib.parse.quote(kw)
                    # fromage=1 strictly filters for jobs posted in the past 24 hours
                    search_url = f"https://in.indeed.com/jobs?q={q}&l={urllib.parse.quote(location)}&fromage=1"
                    page.goto(search_url, timeout=25000)
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
    except Exception as e:
        print(f"[JobHunter] Indeed CDP connection error: {e}")
        
    return found_jobs

def scrape_fresh_naukri_jobs(keywords: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Scrapes fresh Naukri jobs (<24h, dayFilter=1) using persistent Ridealong session.
    """
    if keywords is None:
        keywords = ["associate product manager", "product analyst", "business analyst"]
        
    found_jobs = []
    seen_urls = set()
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            
            for kw in keywords:
                try:
                    slug = kw.strip().lower().replace(" ", "-")
                    # dayFilter=1 filters jobs posted in the last 24 hours
                    search_url = f"https://www.naukri.com/{slug}-jobs?dayFilter=1"
                    page.goto(search_url, timeout=25000)
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
    except Exception as e:
        print(f"[JobHunter] Naukri CDP connection error: {e}")
        
    return found_jobs

def run_job_hunt() -> Dict[str, Any]:
    """
    Executes a unified multi-platform job hunting scan across:
      1. LinkedIn Jobs (guest endpoint, <24h)
      2. Indeed India (Ridealong CDP authenticated session, <24h)
      3. Naukri India (Ridealong CDP authenticated session, <24h)
    
    Deduplicates against SQLite DB, enforces Magicpin/Lifelong blacklist,
    and returns hot matches (score >= 75) and solid matches.
    """
    all_fresh = []
    seen_in_run = set()
    
    # 1. LinkedIn Scrape
    for kw in TARGET_KEYWORDS:
        jobs = scrape_fresh_linkedin_jobs(kw)
        for j in jobs:
            if j["dedup_id"] not in seen_in_run:
                seen_in_run.add(j["dedup_id"])
                all_fresh.append(j)

    # 2. Indeed Scrape (<24h)
    try:
        indeed_jobs = scrape_fresh_indeed_jobs()
        for j in indeed_jobs:
            if j["dedup_id"] not in seen_in_run:
                seen_in_run.add(j["dedup_id"])
                all_fresh.append(j)
    except Exception as e:
        print(f"[JobHunter] Indeed batch run failed: {e}")

    # 3. Naukri Scrape (<24h)
    try:
        naukri_jobs = scrape_fresh_naukri_jobs()
        for j in naukri_jobs:
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
        "new_discovered": len(new_jobs),
        "hot_matches": hot_matches,
        "solid_matches": solid_matches
    }
