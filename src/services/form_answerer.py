import json
import os
import re
from typing import Dict, Any, Optional
from .. import config
from ..engine.llm import generate_text

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "profiles", "candidate_profile.json")
STORIES_PATH = os.path.join(os.path.dirname(__file__), "..", "profiles", "story_bank.json")

def load_candidate_profile() -> Dict[str, Any]:
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_stories() -> list:
    if os.path.exists(STORIES_PATH):
        with open(STORIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("stories", [])
    return []

def answer_standard_field(field_name: str, options: Optional[list] = None) -> Optional[str]:
    """
    Deterministically matches standard application fields to candidate profile values.
    """
    profile = load_candidate_profile()
    p = profile.get("personal", {})
    edu = profile.get("education", {})
    screen = profile.get("screening_defaults", {})
    fn_lower = field_name.lower().strip()

    # Full Name / Preferred Name / Middle Name
    if "middle" in fn_lower and "name" in fn_lower or "middlename" in fn_lower:
        return ""
    if "preferred name" in fn_lower:
        return p.get("first_name", "Vishal")
    if any(k in fn_lower for k in ["full name", "your name", "name"]):
        if "first" in fn_lower:
            return p.get("first_name", "")
        if "last" in fn_lower:
            return p.get("last_name", "")
        return p.get("full_name", "")

    # Email
    if "email" in fn_lower:
        return p.get("email", "")

    # Phone / WhatsApp
    if any(k in fn_lower for k in ["phone", "mobile", "whatsapp", "contact number"]):
        return p.get("phone", "")

    # College / University
    if any(k in fn_lower for k in ["college", "university", "institution", "school"]):
        return edu.get("institution", "Netaji Subhas University of Technology (NSUT), Delhi")

    # Degree / Major
    if any(k in fn_lower for k in ["degree", "major", "branch", "stream"]):
        return edu.get("major", "Computer Science & Engineering (Mathematics & Computing)")

    # Graduation Year
    if any(k in fn_lower for k in ["graduation", "passing year", "batch", "grad year"]):
        return edu.get("graduation_year", "2026")

    # CGPA
    if "cgpa" in fn_lower or "gpa" in fn_lower:
        return edu.get("cgpa", "7.05")

    # Data Visualization / Tools
    if any(k in fn_lower for k in ["visualiz", "dashboard", "bi tool", "power bi", "tableau", "metabase"]):
        return "Metabase, Looker, Python (matplotlib/seaborn), Excel, and custom Streamlit dashboards for operational and funnel telemetry."

    # Current Company / Employer
    if (
        fn_lower == "org"
        or re.search(r'\borg\b', fn_lower)
        or any(k in fn_lower for k in ["current company", "current employer", "most recent) employer", "most recent employer", "organization", "present employer"])
    ):
        return "a quick-commerce startup"

    # Current Title / Role
    if any(k in fn_lower for k in ["current title", "current role", "most recent) title", "most recent title", "designation"]) and not any(w in fn_lower for w in ["tool", "technique", "visualiz", "describe", "what is your expected"]):
        return p.get("current_title", "Tech Product Analyst")

    # How did you hear about this job
    if any(k in fn_lower for k in ["how did you hear", "how did you learn", "source"]):
        return "Company Careers Page"

    # Location / City
    if any(k in fn_lower for k in ["current location", "current city", "city", "location"]):
        return p.get("city", "Delhi NCR, India")

    # Relocation
    if any(k in fn_lower for k in ["relocate", "relocation", "willing to move"]):
        if options:
            for opt in options:
                if "yes" in opt.lower():
                    return opt
        return "Yes"

    # Notice Period / Joining Time
    if any(k in fn_lower for k in ["notice period", "available to join", "availability", "how soon can you join", "start date"]):
        if "in days" in fn_lower or "days)" in fn_lower:
            return "15"
        if options:
            for opt in options:
                if any(w in opt.lower() for w in ["immediate", "15 days", "0-30", "1 month"]):
                    return opt
        return p.get("notice_period", "15 days")

    # Current CTC / Compensation
    if any(k in fn_lower for k in ["current ctc", "current salary", "current fixed", "current compensation"]):
        if any(n in fn_lower for n in [".amount", "in inr", "(inr)"]):
            return "1200000"
        if "lpa" in fn_lower or "fixed" in fn_lower:
            return "10"
        return p.get("current_ctc_lpa", "12 LPA")

    # Expected CTC / Compensation
    if any(k in fn_lower for k in ["expected ctc", "expected salary", "fixed ctc", "expected compensation"]):
        if any(n in fn_lower for n in [".amount", "in inr", "(inr)"]):
            return "1600000"
        if "lpa" in fn_lower or "fixed" in fn_lower:
            return "16"
        return p.get("expected_ctc_lpa", "15 - 18 LPA")

    # LinkedIn Profile Link
    if "linkedin" in fn_lower:
        return p.get("linkedin_url", "")

    # Portfolio / GitHub Link (Strictly NO portfolio link)
    if "github" in fn_lower:
        return p.get("github_url", "")
    if any(k in fn_lower for k in ["portfolio", "website", "personal link"]):
        return p.get("github_url", "") or p.get("linkedin_url", "")

    # Resume Link (for Drive uploads)
    if any(k in fn_lower for k in ["resume link", "cv link", "drive link", "resume url"]):
        return p.get("resume_drive_link", "")

    # Work Authorization (India)
    if any(k in fn_lower for k in ["authorized to work", "legally authorized", "citizenship"]):
        return "Yes"
    if any(k in fn_lower for k in ["sponsorship", "visa"]):
        return "No"

    # Experience Years (only for short fields asking for numeric years of experience)
    if "experience" in fn_lower or "years" in fn_lower:
        # If it's a long descriptive or essay question, delegate to open-ended LLM answerer
        if len(fn_lower) > 45 or any(w in fn_lower for w in ["describe", "explain", "detail", "tell us", "what experience", "datasets", "projects", "outcomes"]):
            return None
        if "month" in fn_lower:
            return "0"
        if "sql" in fn_lower:
            return "2"
        if "python" in fn_lower:
            return "2"
        if "product" in fn_lower or "pm" in fn_lower:
            return "1"
        return "1"

    return None

def answer_open_ended_question(question: str, role_title: str = "Associate Product Manager", company_name: str = "") -> str:
    """
    Uses the LLM to write an authentic, high-impact, non-slop answer to behavioral
    and application essay questions based on the candidate's real story bank.
    Enforces strict company anonymization.
    """
    stories = load_stories()
    stories_summary = "\n".join([
        f"- Role: {s['role']} at {s['context']}: {s['core_metric']}. Details: {s['technical_details']}. Takeaway: {s['product_lesson']}"
        for s in stories
    ])

    system_prompt = (
        "You are answering an application screening question on behalf of an aspiring Product Manager / Tech Analyst.\n"
        "Profile: Final-year B.Tech in CSE (Mathematics & Computing) at NSUT (Netaji Subhas University of Technology, Delhi).\n"
        "Experience: Hands-on work in product analytics, ERP automation, dynamic pricing algorithms, and founding PM work.\n"
        "Tone Rules:\n"
        "1. Direct, clear, humble yet technically sound, metric-grounded.\n"
        "2. Keep the answer concise (2-4 punchy sentences or 70-120 words unless explicitly asked for an essay).\n"
        "3. NEVER use AI buzzwords (delve, tapestry, seamlessly, game-changer, spearhead, synergistic).\n"
        "4. NEVER mention company names (Magicpin, Lifelong, NOM). Refer to them as 'a quick-commerce marketplace startup', 'a D2C consumer brand', or 'an early-stage AI startup'.\n"
        "5. Focus on practical problem-solving: pipelines, metrics, operational friction, telemetry, and unsexy execution."
    )

    user_prompt = (
        f"Job Title: {role_title}\n"
        f"Company Name: {company_name or 'the hiring startup'}\n"
        f"Application Question: \"{question}\"\n\n"
        f"Candidate Background Stories to draw from:\n{stories_summary}\n\n"
        f"Write a sharp, authentic, candidate-voice answer to this question:"
    )

    try:
        response = generate_text(
            prompt=user_prompt,
            system_instruction=system_prompt,
            temperature=0.3
        )
        return response.strip()
    except Exception as e:
        print(f"[FormAnswerer] LLM generation error: {e}")
        return (
            "With a background in Computer Science & Mathematics at NSUT and hands-on experience in product analytics "
            "and automation across quick-commerce and D2C startups, I focus on solving operational friction and building "
            "reliable data pipelines that directly move core metrics like conversion and merchant retention."
        )
