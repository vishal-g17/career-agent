import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, List, Optional, Tuple
from ..engine.llm import generate_text
from .email_verifier import verify_email
from ..db import database
from .. import config

EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

IGNORED_DOMAINS = {
    "linkedin.com", "example.com", "sentry.io", "w3.org", "schema.org", "github.com", "google.com"
}

EMPLOYER_BLACKLIST = {
    "magicpin.in", "magicpin.com", "lifelongonline.com", "lifelongindia.com", "lifelong.in"
}

def extract_emails_from_text(text: str) -> List[str]:
    """
    Extracts valid recruiter/founder email addresses from feed posts or job descriptions.
    Strictly excludes current and past employer domains (Magicpin & Lifelong).
    """
    found = EMAIL_REGEX.findall(text)
    clean_emails = []
    for em in found:
        em_lower = em.lower()
        domain = em_lower.split("@")[-1]
        
        # Hard exclusion for current/past employers
        if any(b in em_lower or b in domain for b in ["magicpin", "lifelong"]):
            continue

        if domain not in IGNORED_DOMAINS and domain not in EMPLOYER_BLACKLIST and not em_lower.endswith(".png") and not em_lower.endswith(".jpg"):
            if em_lower not in clean_emails:
                clean_emails.append(em_lower)
    return clean_emails

def get_sender_credentials(preferred_sender: Optional[str] = None) -> Tuple[str, str]:
    """
    Returns (sender_email, app_password) for the requested account or defaults intelligently.
    Supports dual-sender engine:
      1. College Email: vishal.gupta.ug22@nsut.ac.in (Google Workspace for Education, 1,500/day limit)
      2. Personal Gmail: vishal90301@gmail.com (Consumer Gmail, 500/day limit)
    """
    gmail_addr = getattr(config, "GMAIL_ADDRESS", os.environ.get("GMAIL_ADDRESS", "vishal90301@gmail.com"))
    gmail_pwd = getattr(config, "GMAIL_APP_PASSWORD", os.environ.get("GMAIL_APP_PASSWORD", "")).replace(" ", "").strip()
    
    college_addr = getattr(config, "COLLEGE_EMAIL_ADDRESS", os.environ.get("COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in"))
    college_pwd = getattr(config, "COLLEGE_EMAIL_APP_PASSWORD", os.environ.get("COLLEGE_EMAIL_APP_PASSWORD", "")).replace(" ", "").strip()

    if preferred_sender and "nsut" in preferred_sender.lower():
        if college_pwd:
            return college_addr, college_pwd
        return gmail_addr, gmail_pwd

    if preferred_sender and "gmail" in preferred_sender.lower():
        if gmail_pwd:
            return gmail_addr, gmail_pwd
        return college_addr, college_pwd

    # Default priority: NSUT college email if available (higher quota & higher conversion), else Gmail
    if college_pwd:
        return college_addr, college_pwd
    return gmail_addr, gmail_pwd

def compose_cold_email(
    recipient_email: str, 
    role_title: str = "", 
    company_name: str = "", 
    job_context: str = "",
    tailor_with_llm: bool = False,
    sender_email: Optional[str] = None
) -> Dict[str, str]:
    """
    Composes candidate's high-converting cold email pitch with dynamic subject,
    sender-aware institutional signature (NSUT vs Gmail), and optional LLM tailoring.
    """
    if sender_email is None:
        sender_email, _ = get_sender_credentials()

    is_nsut = "nsut" in sender_email.lower()

    # 1. Subject Line formatting (Strict: User directive - general cold mail uses Business / Product Analyst / Management)
    clean_role = role_title.strip() if role_title else ""
    if not clean_role or clean_role.lower() in [
        "business analyst", "ba", "general", "analyst", "business/product analyst", 
        "product/business analyst", "business / product analyst / management", "product / analyst roles"
    ]:
        subject = "Application: Business / Product Analyst / Management — Vishal Gupta (1 yr exp | NSUT Delhi | Immediate Joiner)"
    elif "business analyst" in clean_role.lower() and "management" not in clean_role.lower():
        clean_role = clean_role.replace("Business Analyst", "Business / Product Analyst / Management").replace("business analyst", "Business / Product Analyst / Management")
        subject = f"Application: {clean_role} — Vishal Gupta (1 yr exp | NSUT Delhi | Immediate Joiner)"
    else:
        subject = f"Application for {clean_role} — Vishal Gupta (1 yr exp | NSUT Delhi | Immediate Joiner)"

    # 2. Body composition (Strictly anonymized: zero employer names Magicpin/Lifelong)
    experience_highlight = (
        "To share quick context on my journey: my earlier stints were an off-campus venture with a founder followed by my campus placement. "
        "Across my experiences as an operator, I’ve worked hands-on on product growth, "
        "merchant reactivation, conversion optimization, pricing telemetry, and business analytics. "
        "Some of my key contributions include reducing merchant cancellations from 10–12% to 4–6% at a quick-commerce startup and "
        "building Python and Playwright automation pipelines at a consumer D2C brand that eliminated 3–4 hours of daily manual reporting."
    )

    if tailor_with_llm and job_context:
        try:
            prompt = (
                f"Job Requirements/Context: {job_context[:600]}\n"
                f"Candidate's real experiences: quick-commerce startup (reduced cancellations 10-12% to 4-6%, telemetry), "
                f"consumer D2C brand (Python/Playwright automation saving 3-4 hours daily), early-stage AI startup (LLM product flows & evaluation).\n"
                f"STRICT RULE: Never mention employer names Magicpin or Lifelong.\n"
                f"Write exactly 2 concise, metric-backed sentences highlighting the most relevant 2 experiences that "
                f"match what this company/founder needs. Sound humble, technical, data-grounded, zero buzzwords."
            )
            tailored_text = generate_text(prompt, temperature=0.3).strip()
            if tailored_text and len(tailored_text) > 40:
                experience_highlight = tailored_text
        except Exception as e:
            print(f"[ColdMailer] LLM tailoring notice: {e}")

    if is_nsut:
        intro_sentence = "I’m Vishal Gupta, a B.Tech graduate in Mathematics & Computing (CSE) from NSUT Delhi with 1 year of full-time experience in Product & Analytics. Following a recent team-wide layoff / restructuring at my previous startup, I am actively exploring new opportunities and available to join immediately (15 days notice)."
        contact_email = college_addr = sender_email
    else:
        intro_sentence = "I’m Vishal Gupta, a B.Tech graduate from NSUT Delhi with 1 year of full-time experience in Product & Analytics. Following a recent team-wide layoff / restructuring at my previous startup, I am actively exploring new opportunities and available to join immediately (15 days notice)."
        contact_email = sender_email

    body = f"""Hi,

{intro_sentence}

{experience_highlight}

I’m actively exploring opportunities across Product Management, APM, Product Analyst, Business Analyst, and broader Business roles. I’d appreciate it if you could consider my profile for relevant openings or connect me with the right person.

Resume: https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing

Best regards,
Vishal Gupta
📞 +91 8368387245
✉️ {contact_email}
🔗 https://www.linkedin.com/in/vshal-g007"""

    return {
        "recipient": recipient_email,
        "sender": sender_email,
        "subject": subject,
        "body": body
    }

def send_cold_email_smtp(recipient_email: str, subject: str, body: str, sender_email: Optional[str] = None) -> bool:
    """
    Sends email via Google SMTP (SSL 465) from either Gmail or NSUT Google Workspace.
    Automatically runs DNS MX verification and strictly prevents sending to Magicpin or Lifelong.
    """
    clean_recip = recipient_email.strip().lower()

    # Hard guardrail against emailing Magicpin or Lifelong
    if any(b in clean_recip for b in ["magicpin", "lifelong"]):
        print(f"[ColdMailer] 🛑 BLOCKED: Recipient '{recipient_email}' belongs to current/past employer (Magicpin/Lifelong). Outreach rejected.")
        database.log_event("COLD_EMAIL", "WARNING", f"Blocked send to blacklisted employer: {recipient_email}")
        return False

    # 0. Pre-Send DNS & MX Verification (Anti-Bounce Shield)
    is_valid, reason, meta = verify_email(recipient_email)
    if not is_valid:
        print(f"[ColdMailer] 🛡️ BLOCKED BOUNCE: Refusing to send to '{recipient_email}'. Reason: {reason}")
        database.log_event("COLD_EMAIL", "WARNING", f"Blocked send to dead email: {recipient_email}", {"reason": reason})
        return False

    sender, app_password = get_sender_credentials(sender_email)

    if not app_password:
        print(f"[ColdMailer] App password not set for sender '{sender}'. Email blocked.")
        return False

    try:
        from email.mime.application import MIMEApplication
        msg = MIMEMultipart()
        msg["From"] = f"Vishal Gupta <{sender}>"
        msg["To"] = recipient_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Attach native ATS resume PDF
        pdf_path = "/Users/admin/Downloads/Vishal_Gupta_Resume.pdf"
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                part = MIMEApplication(f.read(), Name="Vishal_Gupta_Resume.pdf")
            part['Content-Disposition'] = 'attachment; filename="Vishal_Gupta_Resume.pdf"'
            msg.attach(part)

        # Send via port 587 STARTTLS for maximum reliability
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(sender, app_password)
                server.sendmail(sender, [recipient_email], msg.as_string())
        except Exception:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(sender, app_password)
                server.sendmail(sender, [recipient_email], msg.as_string())

        print(f"[ColdMailer] [{sender}] Successfully dispatched cold email to {recipient_email}")
        database.log_event("COLD_EMAIL", "INFO", f"Cold email sent from {sender} to {recipient_email}", {"subject": subject})
        return True
    except smtplib.SMTPResponseException as smtp_err:
        if smtp_err.smtp_code == 550 and ("limit exceeded" in str(smtp_err).lower() or "sending limit" in str(smtp_err).lower()):
            college_addr, college_pwd = get_sender_credentials("nsut")
            if sender != college_addr and college_pwd:
                print(f"[ColdMailer] Gmail daily limit hit. Auto-failing over to NSUT ({college_addr})...")
                try:
                    msg["From"] = f"Vishal Gupta <{college_addr}>"
                    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                        server.ehlo()
                        server.starttls()
                        server.ehlo()
                        server.login(college_addr, college_pwd)
                        server.sendmail(college_addr, [recipient_email], msg.as_string())
                    print(f"[ColdMailer] [{college_addr}] Successfully dispatched cold email (failover) to {recipient_email}")
                    database.log_event("COLD_EMAIL", "INFO", f"Cold email sent from {college_addr} to {recipient_email}", {"subject": subject})
                    return True
                except Exception as fe:
                    print(f"[ColdMailer] Failover send failed: {fe}")
        print(f"[ColdMailer] Failed to send email via SMTP ({sender}): {smtp_err}")
        return False
    except Exception as e:
        print(f"[ColdMailer] Failed to send email via SMTP ({sender}): {e}")
        return False
