import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .. import config
from ..engine.discord_client import DiscordClient
from .exclusion_filter import FORBIDDEN_COMPANIES

IST = timezone(timedelta(hours=5, minutes=30))


def _get_db_path() -> str:
    env_p = os.environ.get("DATABASE_PATH")
    if env_p and os.path.exists(env_p):
        return env_p
    return os.path.expanduser("~/.linkedin_agent.db")


def _sanitize(text: str) -> str:
    if not text:
        return ""
    out = str(text)
    for fc in FORBIDDEN_COMPANIES:
        if len(fc) <= 4:
            out = re.sub(rf"\b{re.escape(fc)}\b", "[restricted]", out, flags=re.IGNORECASE)
        else:
            out = re.sub(re.escape(fc), "[restricted]", out, flags=re.IGNORECASE)
    return out


def _ensure_alert_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_alerts_state (
            alert_key TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            last_alerted_ts REAL DEFAULT 0,
            last_summary TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def send_system_alert(
    platform: str,
    issue_type: str,
    summary: str,
    fix_steps: str,
    details: str = "",
    cooldown_minutes: int = 45,
) -> bool:
    """
    Immediately dispatches an urgent Discord alert to #prompt (with user @mention) and #agent
    whenever LinkedIn, Indeed, Naukri, or Instahyre gets logged out, gated by CAPTCHA, or hits an error.
    Deduplicates identical unresolved alerts for `cooldown_minutes` so Discord is never spammed.
    """
    plat = (platform or "System").strip()
    issue = (issue_type or "error").strip().lower()
    alert_key = f"{plat.lower()}::{issue}"
    now_ts = time.time()

    clean_summary = _sanitize(summary)[:350]
    clean_fix = _sanitize(fix_steps)[:450]
    clean_details = _sanitize(details)[:450]

    # 1. Check SQLite deduplication cooldown
    try:
        conn = sqlite3.connect(_get_db_path(), timeout=15.0)
        _ensure_alert_table(conn)
        row = conn.execute(
            "SELECT is_active, last_alerted_ts FROM system_alerts_state WHERE alert_key = ?",
            (alert_key,),
        ).fetchone()
        if row and row[0] == 1 and (now_ts - float(row[1] or 0)) < (cooldown_minutes * 60.0):
            conn.close()
            return False

        conn.execute(
            """
            INSERT INTO system_alerts_state (alert_key, platform, issue_type, is_active, last_alerted_ts, last_summary, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(alert_key) DO UPDATE SET
                is_active = 1,
                last_alerted_ts = excluded.last_alerted_ts,
                last_summary = excluded.last_summary,
                updated_at = CURRENT_TIMESTAMP
            """,
            (alert_key, plat, issue, now_ts, clean_summary),
        )
        conn.commit()
        conn.close()
    except Exception as db_err:
        print(f"[SystemAlerter] DB state notice: {db_err}")

    # 2. Build clear, actionable Discord embed
    env_label = "MacBook Local (Ridealong)" if sys.platform == "darwin" else "OCI 24/7 Cloud VM"
    now_ist = datetime.now(IST).strftime("%d %b %I:%M %p IST")

    is_logout_or_gate = any(k in issue for k in ("logout", "logged_out", "login", "captcha", "cookie", "auth"))
    color = 0xE74C3C if is_logout_or_gate else 0xF39C12  # Red for auth/CAPTCHA, Amber for runtime/network error
    badge = "🛑 SESSION / GATE ACTION NEEDED" if is_logout_or_gate else "⚠️ SYSTEM ERROR DETECTED"

    fields = [
        {"name": "🌐 Platform", "value": f"**{plat}**", "inline": True},
        {"name": "🏷️ Issue Type", "value": f"`{issue}`", "inline": True},
        {"name": "🖥️ Host", "value": env_label, "inline": True},
        {"name": "❗ What Happened", "value": clean_summary or "Portal requires attention.", "inline": False},
        {"name": "🛠️ Immediate Fix", "value": clean_fix or "Check Ridealong viewer (`http://127.0.0.1:8083`).", "inline": False},
    ]
    if clean_details:
        fields.append({"name": "🔍 Diagnostic Snippet", "value": f"```text\n{clean_details}\n```", "inline": False})

    embed = {
        "title": f"{badge} — {plat}",
        "color": color,
        "fields": fields,
        "footer": {"text": f"{now_ist} | Auto-deduped ({cooldown_minutes}m cooldown until resolved)"},
    }

    mention = f"<@{config.DISCORD_USER_ID}> " if getattr(config, "DISCORD_USER_ID", "") else ""
    headline = f"🚨 {mention}**{plat} Alert (`{issue}`)** — Immediate attention requested:"

    sent_any = False
    try:
        client = DiscordClient()
        prompt_ch = getattr(config, "DISCORD_PROMPT_CHANNEL_ID", "") or "1570342549435187290"
        agent_ch = getattr(config, "DISCORD_AGENT_CHANNEL_ID", "") or "1569246471251959838"

        res1 = client.send_message(channel_id=prompt_ch, content=headline, embeds=[embed])
        if res1:
            sent_any = True

        if agent_ch and agent_ch != prompt_ch:
            client.send_message(channel_id=agent_ch, content=headline, embeds=[embed])
    except Exception as dc_err:
        print(f"[SystemAlerter] Discord send error: {dc_err}")

    return sent_any


def clear_system_alert(platform: str, notify_recovery: bool = True) -> None:
    """
    Marks any active alerts for `platform` as resolved once a healthy run completes,
    and sends a brief green recovery confirmation to Discord if an alert was active.
    """
    plat = (platform or "").strip()
    if not plat:
        return

    had_active = []
    try:
        conn = sqlite3.connect(_get_db_path(), timeout=15.0)
        _ensure_alert_table(conn)
        rows = conn.execute(
            "SELECT issue_type FROM system_alerts_state WHERE lower(platform) = ? AND is_active = 1",
            (plat.lower(),),
        ).fetchall()
        had_active = [r[0] for r in rows]
        if had_active:
            conn.execute(
                "UPDATE system_alerts_state SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE lower(platform) = ?",
                (plat.lower(),),
            )
            conn.commit()
        conn.close()
    except Exception:
        return

    if had_active and notify_recovery:
        try:
            now_ist = datetime.now(IST).strftime("%I:%M %p IST")
            msg = (
                f"✅ **{plat} Recovered ({now_ist})** — Resolved `{', '.join(had_active)}` "
                f"and resumed autonomous execution automatically."
            )
            client = DiscordClient()
            prompt_ch = getattr(config, "DISCORD_PROMPT_CHANNEL_ID", "") or "1570342549435187290"
            client.send_message(channel_id=prompt_ch, content=msg)
        except Exception:
            pass
