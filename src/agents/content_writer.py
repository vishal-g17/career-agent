import json
import os
import uuid
from typing import Dict, Any, Tuple
from .. import config
from ..engine import llm
from ..engine import scrubber
from ..db import database

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(os.path.dirname(HERE), "profiles")
PROMPTS_DIR = os.path.join(os.path.dirname(HERE), "prompts")

def load_voice_profile() -> str:
    path = os.path.join(PROFILES_DIR, "voice_profile.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_story_bank() -> Dict[str, Any]:
    path = os.path.join(PROFILES_DIR, "story_bank.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_hooks() -> list:
    path = os.path.join(PROMPTS_DIR, "hooks.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("hooks", []) if isinstance(data, dict) else data

def draft_post(topic_hint: str = "", preferred_hook_id: str = "") -> Dict[str, Any]:
    """
    Drafts an authentic LinkedIn post blending real CV stories with a chosen hook formula.
    Automatically scrubs slop and scores human-likeness.
    """
    voice_md = load_voice_profile()
    story_bank = load_story_bank()
    hooks = load_hooks()

    # Pick hook
    selected_hook = None
    if preferred_hook_id:
        for h in hooks:
            if h.get("id") == preferred_hook_id:
                selected_hook = h
                break
    if not selected_hook:
        selected_hook = hooks[0]  # Default to Contrarian Take or first

    stories_summary = json.dumps([
        {
            "id": s["id"],
            "context": s.get("context", "a tech company"),
            "role": s["role"],
            "metric": s["core_metric"],
            "technical_details": s["technical_details"],
            "lesson": s["product_lesson"],
            "anonymization_note": s.get("anonymization_note", "Keep company anonymous.")
        }
        for s in story_bank.get("stories", [])
    ], indent=2)

    system_prompt = f"""
You are ghostwriting a LinkedIn post for a Tech Product Analyst & Aspiring Product Manager with an engineering/math background.

VOICE & BRAND GUIDELINES:
{voice_md}

CANDIDATE STORY BANK (REAL NUMBERS ONLY - NEVER INVENT METRICS):
{stories_summary}

HOOK FORMULA TO USE:
Name: {selected_hook.get('name')}
Template: {selected_hook.get('template')}
Trap to avoid: {selected_hook.get('trap')}

CRITICAL FORMATTING RULES:
1. Max length: 700 to 1100 characters.
2. Line 1 MUST be a punchy hook under 140 characters.
3. NO generic AI slop: no "delve", "game-changer", "let's unpack", "testament", "tapestry", "supercharge".
4. NO numbered lists with bold headers (e.g. "1. **Discovery**"). Write in natural human paragraphs.
5. End with 1 sharp, open question or a contrarian takeaway that invites thoughtful comments.
6. NEVER name any specific company (current or past employer). Use anonymized descriptions like "a quick-commerce startup" or "an early-stage AI company".
"""

    user_prompt = f"Write an authentic LinkedIn post."
    if topic_hint:
        user_prompt += f" Topic focus: {topic_hint}"
    else:
        user_prompt += " Pick the most compelling story from the candidate's Magicpin or Lifelong experience."

    raw_draft = llm.generate_text(user_prompt, system_instruction=system_prompt, temperature=0.7)

    # Clean and score
    cleaned_draft, fixes, before_score, after_score = scrubber.clean_and_score(raw_draft)

    post_id = f"post_{uuid.uuid4().hex[:8]}"
    database.save_post(
        post_id=post_id,
        topic=topic_hint or selected_hook.get("name"),
        hook_id=selected_hook.get("id", "custom"),
        content=cleaned_draft,
        human_score=after_score.get("overall", 0),
        status="draft"
    )

    return {
        "post_id": post_id,
        "hook_name": selected_hook.get("name"),
        "raw_draft": raw_draft,
        "cleaned_draft": cleaned_draft,
        "fixes_applied": fixes,
        "human_score_before": before_score,
        "human_score_after": after_score,
        "char_count": len(cleaned_draft)
    }
