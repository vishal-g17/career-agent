import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional

# Priority chain: Claude Opus 4.6 → Claude Sonnet 4.6 → Gemini 3.8 Flash
CLAUDE_MODELS = [
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
]

GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

_MODEL_COOLDOWN_UNTIL: dict[str, float] = {}


def _call_claude(model: str, prompt: str, system_instruction: Optional[str], temperature: float, api_key: str) -> Optional[str]:
    """Call Anthropic Messages API. Returns text or None on failure."""
    url = "https://api.anthropic.com/v1/messages"
    messages = [{"role": "user", "content": prompt}]
    payload = {
        "model": model,
        "max_tokens": 2048,
        "temperature": temperature,
        "messages": messages,
    }
    if system_instruction:
        payload["system"] = system_instruction

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                content_blocks = body.get("content", [])
                texts = [b["text"] for b in content_blocks if b.get("type") == "text"]
                if texts:
                    return "".join(texts).strip()
        except urllib.error.HTTPError as e:
            if e.code in (429, 529, 503):
                time.sleep(2.0 * (attempt + 1))
                continue
            err = e.read().decode("utf-8", errors="ignore")
            print(f"[LLM] Claude {model} error {e.code}: {err[:200]}")
            return None
        except Exception as e:
            print(f"[LLM] Claude {model} exception: {e}")
            return None
    return None


def _call_gemini(model: str, prompt: str, system_instruction: Optional[str], temperature: float, api_key: str) -> Optional[str]:
    """Call Google Gemini generateContent API. Returns text or None on failure."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    user_text = prompt
    if system_instruction:
        user_text = f"SYSTEM INSTRUCTIONS:\n{system_instruction}\n\nUSER PROMPT:\n{prompt}"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=9) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                candidates = body.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    texts = [p["text"] for p in parts if "text" in p]
                    if texts:
                        return "".join(texts).strip()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _MODEL_COOLDOWN_UNTIL[model] = time.time() + 3600.0
                return None
            if e.code in (429, 503):
                if attempt == 0:
                    time.sleep(0.6)
                    continue
                _MODEL_COOLDOWN_UNTIL[model] = time.time() + 90.0
                return None
            return None
        except Exception:
            _MODEL_COOLDOWN_UNTIL[model] = time.time() + 45.0
            return None
    return None


def generate_text(prompt: str, system_instruction: Optional[str] = None, temperature: float = 0.7, max_tokens: int = 2048) -> str:
    """
    Calls LLMs with cascading fallback:
      1. Claude Opus 4.6 (if ANTHROPIC_API_KEY set)
      2. Claude Sonnet 4.6
      3. Gemini 3.8 Flash (free tier, always available)
      4. Gemini 3.5 Flash / Flash-Lite
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    # Try Claude models first (highest quality)
    if anthropic_key:
        for model in CLAUDE_MODELS:
            result = _call_claude(model, prompt, system_instruction, temperature, anthropic_key)
            if result:
                print(f"[LLM] Used {model}")
                return result
            print(f"[LLM] {model} failed or rate-limited, falling back...")

    # Fallback to Gemini (free tier)
    if gemini_key:
        now = time.time()
        for model in GEMINI_MODELS:
            if _MODEL_COOLDOWN_UNTIL.get(model, 0.0) > now:
                continue
            result = _call_gemini(model, prompt, system_instruction, temperature, gemini_key)
            if result:
                print(f"[LLM] Used {model}")
                return result
            print(f"[LLM] {model} failed, trying next...")
        # Last-resort retry ignoring cooldown if all cooled down
        for model in reversed(GEMINI_MODELS):
            result = _call_gemini(model, prompt, system_instruction, temperature, gemini_key)
            if result:
                return result

    raise RuntimeError("All LLM backends exhausted. Check ANTHROPIC_API_KEY and/or GEMINI_API_KEY.")
