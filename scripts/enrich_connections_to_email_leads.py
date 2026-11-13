import re
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.email_verifier import verify_email
from src.services.exclusion_filter import is_excluded

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')

COMPANY_DOMAINS = {
    "swiggy": "swiggy.in",
    "instamart": "swiggy.in",
    "blinkit": "blinkit.com",
    "zomato": "zomato.com",
    "rapido": "rapido.bike",
    "zepto": "zeptonow.com",
    "cars24": "cars24.com",
    "phonepe": "phonepe.com",
    "paytm": "paytm.com",
    "lenskart": "lenskart.in",
    "urban company": "urbancompany.com",
    "cred": "cred.club",
    "razorpay": "razorpay.com",
    "meesho": "meesho.com",
    "flipkart": "flipkart.com",
    "delhivery": "delhivery.com",
    "highradius": "highradius.com",
    "spinny": "spinny.com",
    "scaler": "scaler.com",
    "shiprocket": "shiprocket.com",
    "cashkaro": "cashkaro.com",
    "unifyapps": "unifyapps.com",
    "zycus": "zycus.com",
    "optiwise": "optiwise.ai",
    "gobblecube": "gobblecube.com",
    "sarvam": "sarvam.ai",
    "cleartax": "cleartax.in",
    "rupeek": "rupeek.com",
    "smytten": "smytten.com",
    "rebel foods": "rebelfoods.com",
    "money view": "moneyview.in",
    "fampay": "fampay.in",
    "fam": "fampay.in",
    "curefit": "curefit.com",
    "cult.fit": "curefit.com",
    "groww": "groww.in",
    "zerodha": "zerodha.com",
    "astrotalk": "astrotalk.com",
    "cardekho": "cardekho.com",
    "bain": "bain.com",
    "bcg": "bcg.com",
    "mckinsey": "mckinsey.com",
    "ditto": "joinditto.in",
    "zoomcar": "zoomcar.com",
    "sbnri": "sbnri.com",
    "vayuz": "vayuz.com",
    "swish": "swish.club",
    "deriv": "deriv.com",
    "hcltech": "hcltech.com",
    "sarvatra": "sarvatra.in",
    "valuemomentum": "valuemomentum.com",
    "ethara": "ethara.ai",
    "ethera": "ethera.ai",
    "credgenics": "credgenics.com",
    "sharechat": "sharechat.co",
    "dream11": "dream11.com",
    "myntra": "myntra.com",
    "nykaa": "nykaa.com",
    "1mg": "1mg.com",
    "pharmeasy": "pharmeasy.in",
    "practo": "practo.com",
    "ola": "olacabs.com",
    "uber": "uber.com",
    "amazon": "amazon.com",
    "google": "google.com",
    "microsoft": "microsoft.com"
}

def clean_name(full_name: str):
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0].lower(), parts[-1].lower()
    elif len(parts) == 1:
        return parts[0].lower(), ""
    return "", ""

def enrich_and_queue_connection_emails():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get all connections from queue and outreach
    cur.execute('''
        SELECT id, name, headline FROM linkedin_connections_queue
        UNION
        SELECT id, name, headline FROM linkedin_connections_outreach
    ''')
    rows = cur.fetchall()
    print(f"[LeadEnricher] Analyzing {len(rows)} connections for corporate email synthesis...")

    added = 0
    for cid, full_name, headline in rows:
        if is_excluded(name=full_name, headline=headline or ""):
            continue
        text = f"{full_name} {headline}".lower()
        first, last = clean_name(full_name)
        if not first or not last:
            continue

        matched_company = None
        matched_domain = None
        for comp, domain in COMPANY_DOMAINS.items():
            if re.search(r'\b' + re.escape(comp) + r'\b', text):
                matched_company = comp.title()
                matched_domain = domain
                break

        if not matched_domain:
            continue

        candidate_email = f"{first}.{last}@{matched_domain}"
        
        # Check if already in outreach_leads
        cur.execute("SELECT id FROM outreach_leads WHERE email = ?", (candidate_email,))
        if cur.fetchone():
            continue

        is_valid, reason, _ = verify_email(candidate_email)
        if is_valid:
            cur.execute('''
                INSERT INTO outreach_leads (name, email, company, role_target, status, created_at)
                VALUES (?, ?, ?, 'APM / Product Analyst', 'pending', CURRENT_TIMESTAMP)
            ''', (full_name, candidate_email, matched_company))
            added += 1
            print(f"[LeadEnricher] ✅ Enriched Connection: {full_name} @ {matched_company} -> {candidate_email}")

    conn.commit()
    conn.close()
    print(f"\n[LeadEnricher] Enriched & added {added} verified connection emails directly into cold email queue!")

    # Auto-sync master sheet
    try:
        from scripts.sync_leads_to_sheet import export_all_leads_to_sheet
        export_all_leads_to_sheet()
    except Exception as se:
        print(f"[LeadEnricher] Sheet sync notice: {se}")

if __name__ == "__main__":
    enrich_and_queue_connection_emails()
