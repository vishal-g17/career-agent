import os
import json
from typing import Dict, Any, Tuple, List
from . import humanizer
from . import detect

HERE = os.path.dirname(os.path.abspath(__file__))
SLOP_PATH = os.path.join(HERE, "slop.json")

def load_lexicon() -> Dict[str, Any]:
    with open(SLOP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

_LEXICON = load_lexicon()

def evaluate(text: str) -> Dict[str, Any]:
    """Score text on 5 checks (burstiness, specificity, slop, fingerprint, voice)."""
    results, overall, verdict = detect.run(text, _LEXICON)
    checks_detail = {k: {"score": v[0], "detail": v[1]} for k, v in results.items()}
    return {
        "overall": round(overall, 1),
        "verdict": verdict,
        "checks": checks_detail
    }

def clean_and_score(text: str) -> Tuple[str, List[Any], Dict[str, Any], Dict[str, Any]]:
    """
    Runs humanizer pass on text, then evaluates before & after human score.
    Returns: (cleaned_text, fixes, before_score, after_score)
    """
    before_report = evaluate(text)
    cleaned_text, fixes = humanizer.humanize(text, _LEXICON)
    after_report = evaluate(cleaned_text)
    return cleaned_text, fixes, before_report, after_report
