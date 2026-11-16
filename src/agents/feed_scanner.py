import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from playwright.sync_api import sync_playwright

from .. import config  # noqa: F401 - ensures .env is loaded for GEMINI_API_KEY / ANTHROPIC_API_KEY
from ..engine import llm, scrubber
from ..services.exclusion_filter import (
    FORBIDDEN_COMPANIES,
    has_forbidden_company,
    is_bounced_or_discarded,
    is_excluded,
)

DB_PATH = Path.home() / ".linkedin_agent.db"
RIDEALONG_CDP_ENDPOINT = "http://127.0.0.1:9230/default/feed-job-scanner"
IST = timezone(timedelta(hours=5, minutes=30))

# Safe daily & per-scan engagement caps (organic human networking pattern)
DAILY_MAX_FEED_LIKES = 35
DAILY_MAX_FEED_COMMENTS = 14

HIRING_TRIGGERS = [
    r"\bhiring\b",
    r"\bwe're hiring\b",
    r"\bwe are hiring\b",
    r"\blooking for an? (?:apm|pm|product manager|product analyst|tech product analyst|business analyst|fde|fdse|founder's office|chief of staff)\b",
    r"\bopen role\b",
    r"\bjoin our team\b",
    r"\bexpanding our (?:product|analytics|data) team\b",
    r"\bdm me\b",
    r"\bshare (?:your )?(?:cv|resume)\b",
    r"\bsend (?:your )?(?:resume|cv)\b",
    r"\bmail (?:your )?(?:resume|cv)\b",
]

TARGET_ROLES_RE = re.compile(
    r"\b(apm|associate product manager|product manager|product analyst|tech product analyst|data analyst|business analyst|operations analyst|growth analyst|category manager|associate category manager|forward deployed engineer|fde|fdse|founder's office|chief of staff|business operations|bizops|generalist|analyst)\b",
    re.IGNORECASE,
)

FORM_URL_RE = re.compile(
    r"(https?://(?:forms\.gle|docs\.google\.com/forms|airtable\.com|notion\.site|jobs\.lever\.co|boards\.greenhouse\.io)[^\s\"\'>]+)",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

JOB_LINK_RE = re.compile(
    r"(https?://(?:www\.|in\.)?linkedin\.com/jobs/view/[^\s\"\'>?]+)",
    re.IGNORECASE,
)

# Never like or comment on sensitive, grief, politics, job-seeker #opentowork, or engagement-bait spam posts
SENSITIVE_OR_BAIT_RE = re.compile(
    r"\b("
    r"passed away|rest in peace|rip\b|condolences|funeral|heartbroken|grief|tragic|bereavement|"
    r"hospitalized|cancer|prayers for|"
    r"laid off|impacted by layoffs|affected by the recent layoff|looking for my next role|#opentowork|open to work|please help me find a job|"
    r"comment\s+['\"“”]?interested['\"“”]?|comment\s+['\"“”]?pdf['\"“”]?|comment\s+['\"“”]?link['\"“”]?|drop a ['\"“”]?yes['\"“”]?|"
    r"happy birthday|work anniversary|completed a course|earned a certificate|"
    r"election|political party|religion|ceasefire|terrorist"
    r")\b",
    re.IGNORECASE,
)

FOREIGN_ONLY_RE = re.compile(
    r"\b(vietnam|hanoi|ho chi minh|malaysia|kuala lumpur|indonesia|jakarta|philippines|manila|"
    r"saudi arabia|riyadh|cairo|egypt|nigeria|lagos|brazil|sao paulo|mexico|poland|romania|"
    r"us citizens only|gc/usc only|no sponsorship|uk residents only)\b",
    re.IGNORECASE,
)

BIG_LEADER_HEADLINE_RE = re.compile(
    r"\b(founder|co-founder|cofounder|ceo|cto|cpo|coo|cfo|cmo|cxo|chief\b|"
    r"vp\b|vice president|svp|avp|director|head of|managing director|partner|investor|vc\b|"
    r"principal|general manager|founder's office|entrepreneur)\b",
    re.IGNORECASE,
)

RECRUITER_OR_HM_HEADLINE_RE = re.compile(
    r"\b(recruiter|talent acquisition|talent partner|hiring|hr\b|hrbp|people partner|headhunter|staffing|"
    r"product manager|group pm|senior pm|lead pm|product lead|analytics|data science|business intelligence|"
    r"engineering manager|tech lead|strategy|growth|category)\b",
    re.IGNORECASE,
)

GENUINE_DOMAIN_BODY_RE = re.compile(
    r"\b(product|startup|revenue|growth|conversion|funnel|retention|metrics|analytics|data|sql|python|"
    r"ai\b|llm|agent|automation|quick commerce|ecommerce|d2c|fintech|saas|b2b|consumer|marketplace|"
    r"pricing|margin|unit economics|distribution|founder|execution|roadmap|user|customer|hiring)\b",
    re.IGNORECASE,
)

BANNED_COMMENT_OPENERS = (
    "great post",
    "great insights",
    "great breakdown",
    "great point",
    "love this",
    "spot on",
    "totally agree",
    "couldn't agree more",
    "well said",
    "so true",
    "thanks for sharing",
    "thank you for sharing",
    "insightful",
    "this resonates",
    "100%",
    "absolutely agree",
    "amazing post",
    "nice post",
)

# Extracts ONLY the visited profile's own body (stripping <aside> & "More profiles for you" recommendations)
JS_EXTRACT_PROFILE_BODY_ONLY = """
() => {
    const main = document.querySelector('main');
    if (!main) return '';
    const clone = main.cloneNode(true);
    clone.querySelectorAll('aside, [aria-label*="More profiles"], [aria-label*="People also"]').forEach(el => el.remove());
    for (const sec of Array.from(clone.querySelectorAll('section'))) {
        const h = (sec.querySelector('h2, h3')?.innerText || '').toLowerCase();
        if (h.includes('more profiles for you') || h.includes('people you may know') || h.includes('people also viewed') || h.includes('explore premium')) {
            sec.remove();
        }
    }
    const labels = Array.from(clone.querySelectorAll('[aria-label], img[alt]'))
        .map(el => (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('alt') || ''))
        .join(' ');
    return ((clone.innerText || '') + ' ' + labels).trim();
}
"""


def init_feed_interaction_tables() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS linkedin_feed_interactions (
            post_key TEXT PRIMARY KEY,
            author_name TEXT,
            author_headline TEXT,
            author_url TEXT,
            category TEXT,
            liked INTEGER DEFAULT 0,
            commented INTEGER DEFAULT 0,
            comment_text TEXT,
            post_snippet TEXT,
            interacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS linkedin_profile_safety_cache (
            username TEXT PRIMARY KEY,
            is_safe INTEGER NOT NULL,
            reason TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def _get_today_interaction_counts() -> Tuple[int, int]:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        init_feed_interaction_tables()
        row = conn.execute(
            """
            SELECT COALESCE(SUM(liked), 0), COALESCE(SUM(commented), 0)
            FROM linkedin_feed_interactions
            WHERE date(interacted_at, '+5 hours', '30 minutes') = date('now', '+5 hours', '30 minutes')
            """
        ).fetchone()
        conn.close()
        return int(row[0] or 0), int(row[1] or 0)
    except Exception:
        return 0, 0


def _already_interacted(post_key: str, author_name: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        if conn.execute("SELECT 1 FROM linkedin_feed_interactions WHERE post_key = ?", (post_key,)).fetchone():
            conn.close()
            return True
        # Avoid interacting with multiple posts from the exact same author within 48 hours
        if author_name:
            recent_same_author = conn.execute(
                """
                SELECT 1 FROM linkedin_feed_interactions
                WHERE LOWER(TRIM(author_name)) = ?
                AND julianday('now') - julianday(interacted_at) < 2.0
                LIMIT 1
                """,
                (author_name.strip().lower(),),
            ).fetchone()
            if recent_same_author:
                conn.close()
                return True
        conn.close()
    except Exception:
        pass
    return False


def _record_interaction(
    post_key: str,
    author_name: str,
    author_headline: str,
    author_url: str,
    category: str,
    liked: bool,
    commented: bool,
    comment_text: str,
    post_snippet: str,
) -> None:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        init_feed_interaction_tables()
        conn.execute(
            """
            INSERT OR REPLACE INTO linkedin_feed_interactions
            (post_key, author_name, author_headline, author_url, category, liked, commented, comment_text, post_snippet, interacted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                post_key,
                author_name[:100],
                author_headline[:160],
                author_url[:240],
                category,
                1 if liked else 0,
                1 if commented else 0,
                (comment_text or "")[:500],
                (post_snippet or "")[:350],
            ),
        )
        if commented and comment_text:
            try:
                cid = f"feedcmt_{post_key[:14]}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO comments
                    (id, post_id, comment_id, author_name, author_headline, content, category, reply_draft, reply_status, created_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'auto_posted', CURRENT_TIMESTAMP)
                    """,
                    (
                        cid,
                        cid,
                        author_name[:100],
                        author_headline[:160],
                        (post_snippet or "")[:300],
                        category,
                        comment_text[:500],
                    ),
                )
            except Exception:
                pass
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[FeedInteract] DB log warning: {e}")


def sync_feed_interactions_to_cloud() -> None:
    """Syncs recent LinkedIn feed Likes & Comments to the 24/7 Cloud VM DB for Dashboard & 11 PM Daily Report."""
    ssh_key = os.path.expanduser("~/Downloads/agent-vm-key.key")
    if sys.platform != "darwin" or not os.path.exists(ssh_key):
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        init_feed_interaction_tables()
        rows = conn.execute(
            """
            SELECT post_key, author_name, author_headline, author_url, category,
                   liked, commented, comment_text, post_snippet, interacted_at
            FROM linkedin_feed_interactions
            ORDER BY interacted_at DESC
            LIMIT 60
            """
        ).fetchall()
        conn.close()
        if not rows:
            return
        b64 = base64.b64encode(json.dumps(rows).encode("utf-8")).decode("ascii")
        remote_py = (
            f"import base64,json,sqlite3; rows=json.loads(base64.b64decode('{b64}').decode()); "
            "c=sqlite3.connect('/home/opc/.linkedin_agent.db',timeout=20.0); "
            "c.execute('CREATE TABLE IF NOT EXISTS linkedin_feed_interactions ("
            "post_key TEXT PRIMARY KEY, author_name TEXT, author_headline TEXT, author_url TEXT, "
            "category TEXT, liked INTEGER DEFAULT 0, commented INTEGER DEFAULT 0, "
            "comment_text TEXT, post_snippet TEXT, interacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)'); "
            "c.executemany('INSERT OR REPLACE INTO linkedin_feed_interactions "
            "(post_key,author_name,author_headline,author_url,category,liked,commented,comment_text,post_snippet,interacted_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)', rows); "
            "c.commit(); c.close()"
        )
        subprocess.run(
            [
                "ssh",
                "-i",
                ssh_key,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                "opc@130.210.108.55",
                "/home/opc/career-agent/venv/bin/python -",
            ],
            input=remote_py,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _verify_author_profile_safe(context, author_url: str) -> bool:
    """
    Fail-closed full-profile Experience check for personal /in/{username}/ authors before
    liking or commenting on their post, cached in SQLite so each author is checked once.
    """
    if not author_url or "/in/" not in author_url:
        # Company page (/company/...) — already checked via card text & company slug against FORBIDDEN_COMPANIES
        return not bool(has_forbidden_company(author_url))

    m = re.search(r"/in/([^/?#]+)", author_url)
    if not m:
        return True
    username = m.group(1).strip().lower()
    if username in ("vshal-g007",):
        return False

    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        init_feed_interaction_tables()
        row = conn.execute(
            "SELECT is_safe FROM linkedin_profile_safety_cache WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
        if row is not None:
            return bool(row[0])
    except Exception:
        pass

    prof_page = None
    try:
        prof_page = context.new_page()
        prof_page.goto(f"https://www.linkedin.com/in/{username}/", wait_until="domcontentloaded", timeout=20000)
        prof_page.wait_for_timeout(1800)
        prof_page.mouse.wheel(0, 1200)
        prof_page.wait_for_timeout(900)
        prof_txt = prof_page.evaluate(JS_EXTRACT_PROFILE_BODY_ONLY) or ""
        if len(prof_txt.strip()) < 40:
            return False
        hit = has_forbidden_company(prof_txt)
        is_safe = 0 if hit else 1
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        conn.execute(
            "INSERT OR REPLACE INTO linkedin_profile_safety_cache (username, is_safe, reason, checked_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (username, is_safe, hit or "SAFE"),
        )
        conn.commit()
        conn.close()
        if hit:
            print(f"  🛑 [FeedInteract:Guard] Blocked interaction with {username} (restricted employer on profile).")
            return False
        return True
    except Exception:
        return False
    finally:
        if prof_page is not None:
            try:
                prof_page.close()
            except Exception:
                pass


def _extract_post_bodies_no_comments(page) -> str:
    """
    Extracts ONLY post body text from LinkedIn's 2026 DOM (`main div[role="listitem"]` as well as
    legacy containers) while strictly stopping BEFORE the Reaction/Comment bar so commenter replies
    and commenter emails are NEVER read or scraped.
    """
    js = """
    () => {
        const cloneRoot = document.querySelector('main');
        if (!cloneRoot) return '';
        const blocks = [];
        let postEls = Array.from(cloneRoot.querySelectorAll(
            'div[role="listitem"], div.feed-shared-update-v2, div[data-urn*="urn:li:activity"], article'
        ));
        for (const post of postEls) {
            const reactBtn = post.querySelector('button[aria-label*="Reaction button state"], button[aria-label*="React Like"]');
            if (reactBtn) {
                const walker = document.createTreeWalker(post, NodeFilter.SHOW_TEXT);
                const chunks = [];
                let node;
                while ((node = walker.nextNode())) {
                    const rel = reactBtn.compareDocumentPosition(node);
                    if (rel & Node.DOCUMENT_POSITION_PRECEDING) {
                        const t = (node.nodeValue || '').trim();
                        if (t) chunks.push(t);
                    } else {
                        break;
                    }
                }
                const txt = chunks.join('\\n').trim();
                if (txt.length > 60) blocks.push('Feed post\\n' + txt);
            } else if (post.matches('div.feed-shared-update-v2, div[data-urn*="urn:li:activity"], article')) {
                const clone = post.cloneNode(true);
                clone.querySelectorAll(
                    '.comments-comments-list, .comments-comment-item, .comments-comment-box, ' +
                    '[class*="comments-container"], [class*="comment-item"], [class*="social-details-social-activity"]'
                ).forEach(el => el.remove());
                const txt = (clone.innerText || '').trim();
                if (txt.length > 60) blocks.push('Feed post\\n' + txt);
            }
        }
        if (blocks.length > 0) return blocks.join('\\n\\n');
        return '';
    }
    """
    try:
        return page.evaluate(js) or ""
    except Exception:
        return ""


def _parse_reaction_count(raw: str) -> int:
    if not raw:
        return 0
    s = raw.strip().upper().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KM]?)", s)
    if not m:
        return 0
    val = float(m.group(1))
    mult = m.group(2)
    if mult == "K":
        val *= 1000
    elif mult == "M":
        val *= 1_000_000
    return int(val)


def _extract_visible_cards_metadata(page) -> List[Dict[str, Any]]:
    """
    Inspects currently mounted `main div[role="listitem"]` cards and returns structured metadata
    for genuine posts (strictly excluding comments below the reaction button).
    """
    js = """
    () => {
        const items = Array.from(document.querySelectorAll('main div[role="listitem"]'));
        const out = [];
        items.forEach((card, domIdx) => {
            const reactBtn = card.querySelector('button[aria-label*="Reaction button state"]');
            const commentBtn = card.querySelector('button[aria-label="Comment"]');
            if (!reactBtn || !commentBtn) return;

            const reactAria = reactBtn.getAttribute('aria-label') || '';
            const alreadyLiked = !reactAria.toLowerCase().includes('no reaction');
            const reactCountRaw = (reactBtn.innerText || '').trim();

            // Exact post author from LinkedIn's own post menu aria-label
            let authorName = '';
            const menuBtn = card.querySelector('button[aria-label^="Open control menu for post by "], button[aria-label^="Hide post by "]');
            if (menuBtn) {
                const a = menuBtn.getAttribute('aria-label') || '';
                authorName = a.replace(/^Open control menu for post by\\s+/i, '').replace(/^Hide post by\\s+/i, '').trim();
            }

            // Collect all text strictly BEFORE reactBtn (header + body, zero comments)
            const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
            const lines = [];
            let n;
            while ((n = walker.nextNode())) {
                if (reactBtn.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_PRECEDING) {
                    const t = (n.nodeValue || '').replace(/\\s+/g, ' ').trim();
                    if (t) lines.push(t);
                } else {
                    break;
                }
            }

            // Collect links & aria/alt labels strictly BEFORE reactBtn for exclusion & author URL matching
            const headerLinks = [];
            let postPermalink = '';
            for (const a of Array.from(card.querySelectorAll('a[href]'))) {
                if (!(reactBtn.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_PRECEDING)) continue;
                const href = a.getAttribute('href') || '';
                const txt = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                const aria = a.getAttribute('aria-label') || '';
                if (href.includes('/feed/update/') || href.includes('/posts/')) {
                    if (!postPermalink) postPermalink = href.split('?')[0];
                }
                if (href.includes('/in/') || href.includes('/company/')) {
                    headerLinks.push({ href: href.split('?')[0], text: txt, aria });
                }
            }

            let authorUrl = '';
            if (authorName) {
                const normAuth = authorName.toLowerCase();
                const exact = headerLinks.find(l => l.text.toLowerCase().includes(normAuth) || l.aria.toLowerCase().includes(normAuth));
                if (exact) authorUrl = exact.href;
            }
            if (!authorUrl && headerLinks.length > 0) {
                authorUrl = headerLinks[0].href;
                if (!authorName) authorName = headerLinks[0].text.split('•')[0].trim();
            }

            const allLabels = Array.from(card.querySelectorAll('[aria-label], img[alt]'))
                .map(el => (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('alt') || ''))
                .join(' ');

            out.push({
                domIdx,
                authorName,
                authorUrl,
                postPermalink,
                alreadyLiked,
                reactCountRaw,
                lines,
                allHrefs: headerLinks.map(l => l.href).join(' '),
                allLabels: allLabels.slice(0, 1500),
            });
        });
        return out;
    }
    """
    try:
        return page.evaluate(js) or []
    except Exception:
        return []


def _parse_header_and_body(lines: List[str], author_name: str) -> Tuple[str, str, bool]:
    """Splits pre-reaction card text lines into (author_headline, post_body, is_promoted)."""
    is_promoted = any(l.strip().lower() in ("promoted", "promoted by") or l.strip().lower().startswith("promoted") for l in lines[:12])

    # Locate where authorName appears in lines
    auth_idx = 0
    if author_name:
        norm_a = author_name.lower()
        for idx, ln in enumerate(lines[:10]):
            if norm_a in ln.lower():
                auth_idx = idx
                break

    # Timestamp marker (e.g. "21h", "2d", "3w", "45m", "1mo") marks end of actor header
    time_re = re.compile(r"^(\d+\s*(?:s|m|h|d|w|mo|yr)s?|just now)(\s*•.*)?$", re.IGNORECASE)
    noise_tokens = {"feed post", "follow", "following", "connect", "•", "• 1st", "• 2nd", "• 3rd+", "1st", "2nd", "3rd+", "… more", "...more"}

    ts_idx = None
    for idx in range(auth_idx + 1, min(len(lines), auth_idx + 9)):
        l_clean = lines[idx].strip()
        if time_re.match(l_clean):
            ts_idx = idx
            break

    headline_parts = []
    body_start = auth_idx + 1
    if ts_idx is not None:
        for ln in lines[auth_idx + 1 : ts_idx]:
            low = ln.strip().lower()
            if low in noise_tokens or norm_a in low if author_name else False:
                continue
            if low.startswith("view ") and "profile" in low:
                continue
            headline_parts.append(ln.strip())
        body_start = ts_idx + 1
    else:
        # Fallback: take next 1-2 non-noise short lines as headline
        for idx in range(auth_idx + 1, min(len(lines), auth_idx + 4)):
            low = lines[idx].strip().lower()
            if low in noise_tokens:
                body_start = idx + 1
                continue
            if len(lines[idx]) < 110 and not lines[idx].endswith("."):
                headline_parts.append(lines[idx].strip())
                body_start = idx + 1
            else:
                break

    body_lines = []
    for ln in lines[body_start:]:
        low = ln.strip().lower()
        if low in noise_tokens or time_re.match(ln.strip()):
            continue
        if low in ("like", "comment", "repost", "send"):
            continue
        body_lines.append(ln.strip())

    headline = " | ".join(headline_parts)[:160]
    body = "\n".join(body_lines).strip()
    return headline, body, is_promoted


def _classify_post_for_engagement(
    author_name: str,
    author_headline: str,
    author_url: str,
    body: str,
    reaction_count: int,
    source_stage: str,
) -> Optional[Tuple[str, bool]]:
    """
    Decides whether a post warrants selective interaction.
    Returns None (skip) OR (category, wants_comment).
    """
    if not author_name or len(body) < 70:
        return None

    low_author = author_name.lower()
    if "vishal gupta" in low_author or "vshal-g007" in (author_url or "").lower():
        return None

    combined = f"{author_name}\n{author_headline}\n{body}"

    # Hard block sensitive, grief, layoff-victim, certificate, or engagement-bait spam posts
    if SENSITIVE_OR_BAIT_RE.search(combined):
        return None

    # Require predominantly English/Latin text so comments always match language
    ascii_letters = sum(1 for ch in body if "a" <= ch.lower() <= "z")
    if ascii_letters < max(35, int(len(body) * 0.45)):
        return None

    # Exclude juniors (class of 2027+) & irrelevant non-tech profiles
    if "/in/" in (author_url or "") and is_excluded(name=author_name, headline=author_headline):
        return None

    is_hiring = any(re.search(pat, body, re.IGNORECASE) for pat in HIRING_TRIGGERS)
    has_target_role = bool(TARGET_ROLES_RE.search(body))
    is_foreign_only = bool(FOREIGN_ONLY_RE.search(combined)) and ("india" not in combined.lower() and "remote" not in combined.lower())
    if is_foreign_only:
        return None

    is_big_leader = bool(BIG_LEADER_HEADLINE_RE.search(author_headline)) or (
        "/company/" in (author_url or "") and reaction_count >= 50
    )
    is_recruiter_or_hm = bool(RECRUITER_OR_HM_HEADLINE_RE.search(author_headline))
    has_domain_substance = bool(GENUINE_DOMAIN_BODY_RE.search(body))
    asks_question = "?" in body[-220:]

    # 1. Direct Hiring Post for Target Product/Analytics/Founder's Office Roles
    if is_hiring and has_target_role:
        return ("hiring_decision_maker", True)

    # In Stage 2 (keyword post search), ONLY interact with relevant hiring/HM/founder posts
    if source_stage != "personal_feed":
        if (is_big_leader or is_recruiter_or_hm) and has_domain_substance and len(body) >= 120:
            return ("recruiter_or_hm", random.random() < 0.45)
        return None

    # 2. Big Person / Founder / CXO / VP / Director / High-Traction Industry Post
    if is_big_leader and (has_domain_substance or reaction_count >= 40):
        wants_cmt = True if asks_question else (random.random() < 0.60)
        return ("big_person_leader", wants_cmt)

    # 3. Recruiter / Talent Acquisition / Hiring Manager / Senior PM / Analytics Leader
    if is_recruiter_or_hm and (has_domain_substance or is_hiring):
        wants_cmt = True if (asks_question or is_hiring) else (random.random() < 0.45)
        return ("recruiter_or_hm", wants_cmt)

    # 4. High-signal viral / genuine domain post in Personal Feed
    if has_domain_substance and len(body) >= 140 and (reaction_count >= 25 or asks_question):
        wants_cmt = asks_question and (random.random() < 0.40)
        return ("genuine_domain_post", wants_cmt)

    return None


def _generate_contextual_comment(
    author_name: str,
    author_headline: str,
    post_body: str,
    category: str,
    has_email_in_post: bool = False,
) -> Optional[str]:
    """
    Generates a concise, authentic, post-specific 1-2 sentence LinkedIn comment in Vishal's voice,
    scrubbed of AI tells and strictly checked against confidentiality & anti-cliché rules.
    """
    first_name = author_name.split()[0] if author_name else ""

    if category == "hiring_decision_maker":
        mode_instructions = (
            "MODE: Relevant Hiring Post.\n"
            "- Write a crisp, respectful, high-signal 1-2 sentence comment (20 to 38 words).\n"
            "- Reference the exact role/team or problem area mentioned in the post.\n"
            "- Briefly note hands-on experience in product & analytics (B.Tech CSE/Math from NSUT Delhi, immediate/15-day joiner) "
            + (
                "and mention sending across your CV to the email shared."
                if has_email_in_post
                else "and note you'd love to connect or share your CV for the role."
            )
            + "\n- NEVER include phone numbers, email addresses, or URLs in the public comment."
        )
    else:
        mode_instructions = (
            "MODE: Genuine Industry / Leadership / Product Engagement (NOT a job pitch).\n"
            "- DO NOT ask for a job, referral, or mention being on the job market.\n"
            "- Write 1-2 sharp, natural sentences (18 to 40 words) reacting directly to a SPECIFIC point, metric, trade-off, or question in the post.\n"
            "- Bring a grounded product/analytics/operator perspective (e.g., unit economics, funnel reality, execution bottleneck, data vs vanity metrics, distribution).\n"
            "- NEVER start with generic praise like 'Great post', 'Spot on', 'Totally agree', 'Well said', 'Love this', 'Thanks for sharing', or 'This resonates'. Start directly with the insight!"
        )

    system_prompt = (
        "You are writing a real LinkedIn comment for Vishal Gupta (B.Tech Mathematics & Computing from NSUT Delhi; "
        "pragmatic product & analytics builder).\n\n"
        f"{mode_instructions}\n\n"
        "STRICT OUTPUT RULES:\n"
        "1. Output ONLY the raw comment text (1-2 sentences, max 250 characters).\n"
        "2. NO hashtags (#), NO emojis, NO bullet points, NO em-dashes (—), NO surrounding quotation marks.\n"
        "3. NEVER name any past or current employer of Vishal.\n"
        "4. Sound like a sharp human peer typing naturally on LinkedIn, never an AI assistant."
    )

    user_prompt = (
        f"Post Author: {author_name} ({author_headline or 'Industry Leader'})\n"
        f"Author First Name (optional to use only if natural): {first_name}\n"
        f"Post Content:\n{post_body[:1400]}\n\n"
        "Write the 1-2 sentence LinkedIn comment now:"
    )

    try:
        raw = llm.generate_text(user_prompt, system_instruction=system_prompt, temperature=0.65, max_tokens=180)
    except Exception as e:
        print(f"[FeedInteract] LLM comment skip: {e}")
        return None

    if not raw:
        return None

    # Scrub invisible unicode, em-dashes, and AI slop words
    cleaned, _, _, _ = scrubber.clean_and_score(raw.strip())
    cleaned = cleaned.strip().strip('"').strip("'").strip("“”")
    cleaned = re.sub(r"#\w+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) < 30 or len(cleaned) > 280:
        return None
    low = cleaned.lower()
    if any(low.startswith(op) for op in BANNED_COMMENT_OPENERS):
        return None
    if has_forbidden_company(cleaned):
        return None
    if "http://" in low or "https://" in low or "@" in cleaned or "{{" in cleaned or "[" in cleaned:
        return None

    return cleaned


def _interact_with_visible_posts(
    page,
    context,
    source_stage: str,
    scan_likes_budget: int,
    scan_comments_budget: int,
    seen_post_keys: set,
    deadline: Optional[float] = None,
) -> Tuple[int, int]:
    """
    Evaluates currently visible feed cards and selectively Likes (and occasionally Comments on)
    genuine posts from Founders/Leaders, Recruiters, Hiring Managers, and strong domain voices.
    Returns (likes_done, comments_done).
    """
    if scan_likes_budget <= 0 or page.is_closed():
        return 0, 0

    likes_today, comments_today = _get_today_interaction_counts()
    rem_likes = min(scan_likes_budget, max(0, DAILY_MAX_FEED_LIKES - likes_today))
    rem_comments = min(scan_comments_budget, max(0, DAILY_MAX_FEED_COMMENTS - comments_today))
    if rem_likes <= 0:
        return 0, 0

    cards_meta = _extract_visible_cards_metadata(page)
    likes_done = 0
    comments_done = 0

    for meta in cards_meta:
        if likes_done >= rem_likes or page.is_closed():
            break
        if deadline is not None and time.time() >= (deadline - 12.0):
            break
        if meta.get("alreadyLiked"):
            continue

        author_name = (meta.get("authorName") or "").strip()
        author_url = (meta.get("authorUrl") or "").strip()
        lines = meta.get("lines") or []
        headline, body, is_promoted = _parse_header_and_body(lines, author_name)
        if is_promoted or not author_name or len(body) < 70:
            continue

        # 1. Card-level restricted employer check (checks author, headline, body, hrefs, aria-labels, img alts)
        full_card_surface = f"{author_name} {headline} {body} {meta.get('allHrefs', '')} {meta.get('allLabels', '')}"
        if has_forbidden_company(full_card_surface):
            continue

        norm_body_prefix = re.sub(r"\W+", " ", body[:180].lower()).strip()
        post_key_seed = meta.get("postPermalink") or f"{author_name.lower()}|{norm_body_prefix}"
        post_key = hashlib.sha1(post_key_seed.encode("utf-8")).hexdigest()[:18]
        if post_key in seen_post_keys:
            continue
        seen_post_keys.add(post_key)

        reaction_count = _parse_reaction_count(meta.get("reactCountRaw", ""))
        decision = _classify_post_for_engagement(
            author_name=author_name,
            author_headline=headline,
            author_url=author_url,
            body=body,
            reaction_count=reaction_count,
            source_stage=source_stage,
        )
        if not decision:
            continue

        if _already_interacted(post_key, author_name):
            continue

        # 2. Full-profile Experience check for /in/{username}/ authors (Priya-Rai-proof guard!)
        if not _verify_author_profile_safe(context, author_url):
            continue

        category, wants_comment = decision
        do_comment = bool(wants_comment and comments_done < rem_comments)

        # Re-locate live card in DOM by matching author control menu (immune to index shifts!)
        try:
            safe_author_esc = author_name.replace('"', '\\"')
            card_loc = page.locator(
                f'main div[role="listitem"]:has(button[aria-label="Open control menu for post by {safe_author_esc}"])'
            ).first
            if card_loc.count() == 0:
                card_loc = page.locator('main div[role="listitem"]').nth(int(meta["domIdx"]))
            if card_loc.count() == 0 or not card_loc.is_visible():
                continue

            card_loc.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(random.randint(2200, 4600))

            # Expand "… more" if truncated so we read full post before commenting
            more_btn = card_loc.locator('button:has-text("… more"), button:has-text("...more")').first
            if more_btn.count() > 0 and more_btn.is_visible():
                try:
                    more_btn.click(timeout=3000)
                    page.wait_for_timeout(random.randint(900, 1600))
                    # Re-check expanded text against forbidden companies & refresh body for LLM!
                    expanded_txt = card_loc.inner_text() or ""
                    if has_forbidden_company(expanded_txt):
                        continue
                    refreshed = _extract_visible_cards_metadata(page)
                    for rm in refreshed:
                        if rm.get("authorName") == author_name:
                            _, exp_body, _ = _parse_header_and_body(rm.get("lines") or [], author_name)
                            if len(exp_body) > len(body):
                                body = exp_body
                            break
                except Exception:
                    pass

            # Click Like ("Reaction button state: no reaction")
            like_btn = card_loc.locator('button[aria-label="Reaction button state: no reaction"]').first
            if like_btn.count() == 0 or not like_btn.is_visible():
                continue
            like_btn.click(timeout=4000)
            page.wait_for_timeout(random.randint(1400, 2600))

            # Verify reaction state flipped
            after_aria = (
                card_loc.locator('button[aria-label*="Reaction button state"]').first.get_attribute("aria-label") or ""
            )
            liked_ok = "no reaction" not in after_aria.lower()
            if not liked_ok:
                continue

            likes_done += 1
            comment_posted = False
            comment_text = ""

            if do_comment:
                has_em = bool(EMAIL_RE.search(body))
                draft_cmt = _generate_contextual_comment(
                    author_name=author_name,
                    author_headline=headline,
                    post_body=body,
                    category=category,
                    has_email_in_post=has_em,
                )
                if draft_cmt:
                    c_toggle = card_loc.locator('button[aria-label="Comment"]').first
                    if c_toggle.count() > 0 and c_toggle.is_visible():
                        c_toggle.click(timeout=4000)
                        page.wait_for_timeout(random.randint(1300, 2300))

                        editor = card_loc.locator(
                            'div[role="textbox"][aria-label*="comment" i], div.ProseMirror[role="textbox"]'
                        ).first
                        if editor.count() > 0 and editor.is_visible():
                            editor.click(timeout=3000)
                            page.wait_for_timeout(random.randint(600, 1200))
                            page.keyboard.insert_text(draft_cmt)
                            page.wait_for_timeout(random.randint(1800, 3400))

                            submit_btn = card_loc.locator('button:not([aria-label="Comment"]):has-text("Comment")').last
                            if (
                                submit_btn.count() > 0
                                and submit_btn.is_visible()
                                and submit_btn.is_enabled()
                                and (submit_btn.inner_text() or "").strip() == "Comment"
                            ):
                                submit_btn.click(timeout=4000)
                                page.wait_for_timeout(random.randint(2800, 4200))

                                leftover = ""
                                try:
                                    if editor.count() > 0 and editor.is_visible():
                                        leftover = (editor.inner_text() or "").strip()
                                except Exception:
                                    leftover = ""

                                if len(leftover) < 10:
                                    comment_posted = True
                                    comment_text = draft_cmt
                                    comments_done += 1
                                else:
                                    # Clean up unsent text so nothing lingers in UI
                                    try:
                                        editor.click()
                                        page.keyboard.press("Meta+A")
                                        page.keyboard.press("Backspace")
                                    except Exception:
                                        pass

            _record_interaction(
                post_key=post_key,
                author_name=author_name,
                author_headline=headline,
                author_url=author_url,
                category=category,
                liked=True,
                commented=comment_posted,
                comment_text=comment_text,
                post_snippet=body[:320],
            )
            if comment_posted:
                print(
                    f"  💬 [FeedInteract] Liked + Commented ({category}) on {author_name}'s post "
                    f"[{headline[:45]}]: \"{comment_text}\""
                )
                page.wait_for_timeout(random.randint(6000, 12000))
            else:
                print(f"  👍 [FeedInteract] Liked ({category}) {author_name}'s post [{headline[:50]}]")
                page.wait_for_timeout(random.randint(2500, 5200))

        except Exception as ie:
            print(f"  [FeedInteract] Minor card skip ({author_name}): {ie}")
            continue

    return likes_done, comments_done


def queue_discovered_job_url(url: str, role_hint: str, author: str) -> bool:
    clean_url = url.split("?")[0].rstrip("/")
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        job_id = f"feed_{abs(hash(clean_url))}"
        c.execute("SELECT 1 FROM jobs WHERE url LIKE ? LIMIT 1", (f"%{clean_url[-25:]}%",))
        if c.fetchone():
            conn.close()
            return False
        c.execute(
            """
            INSERT OR IGNORE INTO jobs (id, title, company, location, url, status, match_score, scorecard, created_at)
            VALUES (?, ?, ?, 'India', ?, 'new', 85, '{}', CURRENT_TIMESTAMP)
            """,
            (job_id, role_hint[:80] or "Product / Analytics Role", f"Post by {author[:40]}", clean_url),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def queue_email_lead(email: str, author_name: str, company: str, role: str, post_snippet: str = "") -> bool:
    """Inserts a freshly scraped email from a LinkedIn post directly into outreach_leads."""
    email_clean = email.strip().lower()
    if any(d in email_clean for d in ["example.com", "domain.com", "linkedin.com", "email.com"]):
        return False

    try:
        combined = f"{email_clean} {author_name} {company} {post_snippet}".lower()
        if any(fc in combined for fc in FORBIDDEN_COMPANIES) or has_forbidden_company(combined):
            return False
        if is_bounced_or_discarded(email_clean):
            return False
    except Exception:
        pass

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """
            INSERT OR IGNORE INTO outreach_leads (name, email, company, role_target, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
            """,
            (author_name[:80], email_clean, company[:80] or "Startup/Hiring Company", role[:60] or "Product/Analytics"),
        )
        changes = conn.total_changes
        conn.commit()
        conn.close()
        return changes > 0
    except Exception as e:
        print(f"[FeedScanner] Error inserting lead {email_clean}: {e}")
        return False


def _extract_leads_from_text(main_text: str, source_label: str, seen_snippets: Optional[set] = None) -> List[Dict[str, Any]]:
    post_blocks = re.split(r"(?:Feed post|Sort by:|Suggested|Promoted)", main_text)
    found_leads = []
    seen = seen_snippets if seen_snippets is not None else set()

    for block in post_blocks:
        clean = block.strip()
        if len(clean) < 80:
            continue

        is_hiring = any(re.search(pat, clean, re.IGNORECASE) for pat in HIRING_TRIGGERS)
        has_role = TARGET_ROLES_RE.search(clean)

        if is_hiring or has_role:
            snippet_key = re.sub(r"\W+", " ", clean[:90].lower()).strip()
            if snippet_key in seen:
                continue
            seen.add(snippet_key)

            lines = [l.strip() for l in clean.split("\n") if l.strip()]
            author = lines[0] if lines else "LinkedIn Connection"
            role_matched = has_role.group(0) if has_role else "Hiring Role"

            emails = EMAIL_RE.findall(clean)
            emails = [e for e in set(emails) if not any(j in e for j in ["example", "sentry", "w3.org"])]

            forms = FORM_URL_RE.findall(clean)
            job_links = JOB_LINK_RE.findall(clean)

            lead = {
                "source": source_label,
                "author": author,
                "role_matched": role_matched,
                "emails": emails,
                "forms": forms,
                "job_links": job_links,
                "snippet": clean[:300],
            }
            found_leads.append(lead)

            for em in emails:
                queued = queue_email_lead(em, author, "Hiring Lead via LinkedIn Post", role_matched, post_snippet=clean[:300])
                if queued:
                    print(f"  🎯 [Auto-Queued Email from Post Body] {em} (Role: {role_matched} | Post Author: {author})")

            for jl in job_links:
                if queue_discovered_job_url(jl, role_matched, author):
                    print(f"  💼 [Queued Job Link from Post] {jl}")

            for fm in forms:
                print(f"  📋 [Application Form Found] {fm} (Role: {role_matched} | Post Author: {author})")
                try:
                    from ..services.ats_filler import fill_ats_application

                    res = fill_ats_application(fm, role_title=role_matched, auto_submit=True)
                    if res.get("submitted") and res.get("confirmed"):
                        print(f"  ✅ [Form Auto-Submitted & Confirmed] {fm}")
                except Exception:
                    pass

    return found_leads


def scan_personal_feed_and_hiring_posts(duration_minutes: float = 10.0) -> List[Dict[str, Any]]:
    """
    Connects to real logged-in LinkedIn session for ~duration_minutes:
    1. Scans personal home feed (/feed/) — extracts hiring emails/links from post bodies ONLY (never comments)
       AND selectively Likes + writes contextual Comments on genuine posts by Founders/Leaders, Recruiters, HMs,
       and strong product/analytics voices.
    2. Searches fresh LinkedIn hiring posts (/search/results/content/) — extracts emails/forms/job links
       AND engages with genuine hiring posts from Recruiters / Hiring Managers / Founders.
    """
    init_feed_interaction_tables()
    all_leads: List[Dict[str, Any]] = []
    seen_snippets: set = set()
    seen_post_keys: set = set()

    # Randomized per-window interaction targets (selective, natural human networking)
    target_scan_likes = random.randint(4, 6)
    target_scan_comments = random.randint(1, 3)
    total_likes_session = 0
    total_comments_session = 0

    deadline = time.time() + max(60.0, duration_minutes * 60.0)
    print(
        f"\n[FeedScanner] Starting ~{duration_minutes:.1f}-min Feed Hunt + Selective Engagement "
        f"(Budget: up to {target_scan_likes} Likes & {target_scan_comments} Comments | Post-Body Emails Only)..."
    )

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(RIDEALONG_CDP_ENDPOINT)
            context = browser.contexts[0]
            page = context.new_page()
        except Exception as conn_err:
            print(f"[FeedScanner] Could not connect to CDP: {conn_err}")
            return []

        try:
            from ..services.linkedin_control import (
                is_linkedin_logged_out,
                set_linkedin_enabled,
                sync_control_with_cloud,
            )

            # 1. SCAN PERSONAL HOME FEED + SELECTIVE ENGAGEMENT
            print("[FeedScanner] Stage 1: Navigating to Personal Feed (https://www.linkedin.com/feed/)...")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3200)
            if is_linkedin_logged_out(page):
                set_linkedin_enabled(False, reason="linkedin_logged_out", updated_by="feed_scanner")
                return []

            feed_scrolls = random.randint(8, 13)
            for step in range(feed_scrolls):
                if time.time() >= deadline or not sync_control_with_cloud().get("linkedin_enabled", True):
                    break
                page.mouse.wheel(0, random.randint(680, 1250))
                page.wait_for_timeout(random.randint(2400, 4800))

                # Extract post bodies incrementally before virtualized cards unmount
                chunk_text = _extract_post_bodies_no_comments(page)
                if chunk_text:
                    all_leads.extend(_extract_leads_from_text(chunk_text, "Personal Feed", seen_snippets))

                # Every 2-3 scrolls, selectively Like / Comment on high-value visible posts
                if step % 2 == 1 and total_likes_session < target_scan_likes:
                    lk, cm = _interact_with_visible_posts(
                        page=page,
                        context=context,
                        source_stage="personal_feed",
                        scan_likes_budget=min(2, target_scan_likes - total_likes_session),
                        scan_comments_budget=min(1, target_scan_comments - total_comments_session),
                        seen_post_keys=seen_post_keys,
                        deadline=deadline,
                    )
                    total_likes_session += lk
                    total_comments_session += cm

            print(
                f"[FeedScanner] Stage 1 Complete: {len(all_leads)} hiring posts captured | "
                f"{total_likes_session} Likes & {total_comments_session} Comments in Personal Feed."
            )

            # 2. SEARCH FRESH POSTS FOR PRODUCT & ANALYTICS ROLES + HIRING POST ENGAGEMENT
            search_queries = [
                'hiring ("product analyst" OR "business analyst") India',
                'hiring ("APM" OR "associate product manager") India',
                'hiring ("founder\'s office" OR "product manager") ("send resume" OR "mail" OR "@")',
                '("we\'re hiring" OR "open role") ("product manager" OR "data analyst") India',
            ]
            random.shuffle(search_queries)

            for query in search_queries:
                if time.time() >= deadline or not sync_control_with_cloud().get("linkedin_enabled", True):
                    break
                try:
                    search_url = (
                        f"https://www.linkedin.com/search/results/content/"
                        f"?keywords={urllib.parse.quote(query)}&sortBy=%22date_posted%22"
                    )
                    print(f"\n[FeedScanner] Stage 2: Searching LinkedIn Posts for: {query}...")
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(random.randint(2800, 4800))
                    if is_linkedin_logged_out(page):
                        set_linkedin_enabled(False, reason="linkedin_logged_out", updated_by="feed_scanner")
                        return all_leads

                    q_leads_before = len(all_leads)
                    for s_idx in range(random.randint(5, 9)):
                        if time.time() >= deadline or not sync_control_with_cloud().get("linkedin_enabled", True):
                            break
                        page.mouse.wheel(0, random.randint(680, 1250))
                        page.wait_for_timeout(random.randint(2400, 5000))

                        search_text = _extract_post_bodies_no_comments(page)
                        if search_text:
                            all_leads.extend(
                                _extract_leads_from_text(search_text, f"Post Search: {query}", seen_snippets)
                            )

                        # Engage selectively with genuine hiring posts from Recruiters/HMs/Founders
                        if s_idx % 3 == 1 and total_likes_session < target_scan_likes:
                            lk, cm = _interact_with_visible_posts(
                                page=page,
                                context=context,
                                source_stage="hiring_search",
                                scan_likes_budget=min(2, target_scan_likes - total_likes_session),
                                scan_comments_budget=min(1, target_scan_comments - total_comments_session),
                                seen_post_keys=seen_post_keys,
                                deadline=deadline,
                            )
                            total_likes_session += lk
                            total_comments_session += cm

                    print(
                        f"[FeedScanner] Found {len(all_leads) - q_leads_before} fresh hiring posts for '{query}'."
                    )
                except Exception as se:
                    print(f"[FeedScanner] Error searching posts for '{query}': {se}")

            # 3. Utilize remaining window organically on Personal Feed
            remaining = deadline - time.time()
            if remaining > 8 and sync_control_with_cloud().get("linkedin_enabled", True):
                try:
                    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                    while time.time() < deadline:
                        page.mouse.wheel(0, random.randint(650, 1050))
                        time.sleep(min(8.0, max(1.0, deadline - time.time())))
                        extra_txt = _extract_post_bodies_no_comments(page)
                        if extra_txt:
                            all_leads.extend(
                                _extract_leads_from_text(extra_txt, "Personal Feed Deep Scroll", seen_snippets)
                            )
                        if total_likes_session < target_scan_likes:
                            lk, cm = _interact_with_visible_posts(
                                page=page,
                                context=context,
                                source_stage="personal_feed",
                                scan_likes_budget=min(1, target_scan_likes - total_likes_session),
                                scan_comments_budget=min(1, target_scan_comments - total_comments_session),
                                seen_post_keys=seen_post_keys,
                                deadline=deadline,
                            )
                            total_likes_session += lk
                            total_comments_session += cm
                except Exception:
                    pass
        finally:
            try:
                page.close()
            except Exception:
                pass

    total_emails = sum(len(l["emails"]) for l in all_leads)
    total_forms = sum(len(l["forms"]) for l in all_leads)
    print(
        f"\n[FeedScanner] Finished: {len(all_leads)} hiring posts | {total_emails} post-body emails queued | "
        f"{total_forms} forms | 👍 {total_likes_session} Likes & 💬 {total_comments_session} Contextual Comments posted!"
    )

    # Sync newly scraped emails + feed engagement metrics to 24/7 Cloud VM
    try:
        sync_feed_interactions_to_cloud()
        from scripts.master_autonomous_cycle import sync_new_outreach_leads_to_cloud_vm

        conn_sync = sqlite3.connect(DB_PATH)
        sync_new_outreach_leads_to_cloud_vm(conn_sync)
        conn_sync.close()
    except Exception:
        pass

    if total_likes_session > 0 or total_comments_session > 0:
        try:
            from ..agents.discord_notifier import DiscordNotifier

            DiscordNotifier().client.send_message(
                config.DISCORD_AGENT_CHANNEL_ID,
                f"👍 **LinkedIn Feed Engagement Complete**\n"
                f"• Selective Interactions: **`{total_likes_session}` Likes** & **`{total_comments_session}` Contextual Comments** on Founder / HM / Recruiter / Industry posts\n"
                f"• Hiring Leads Scraped (Post Body Only): **`{len(all_leads)}` posts** (`{total_emails}` emails queued)",
            )
        except Exception:
            pass

    return all_leads


# Alias for backwards compatibility with master_autonomous_cycle
def scan_personal_feed_for_jobs(duration_minutes: float = 10.0):
    return scan_personal_feed_and_hiring_posts(duration_minutes=duration_minutes)


if __name__ == "__main__":
    scan_personal_feed_and_hiring_posts()
