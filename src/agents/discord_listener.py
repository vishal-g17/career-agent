import os
import sys
import time
import json
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Set, Dict, Any, List

import shutil
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.discord_client import DiscordClient
from src import config

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR") or str(PROJECT_ROOT)

class DiscordListener:
    def __init__(self, channel_id: str = None):
        self.client = DiscordClient()
        self.channel_id = channel_id or getattr(config, "DISCORD_PROMPT_CHANNEL_ID", "1570342549435187290")
        jobs_ch = getattr(config, "DISCORD_JOBS_CHANNEL_ID", "1569246471251959838")
        self.watch_channels: List[str] = list(dict.fromkeys([c for c in [self.channel_id, jobs_ch] if c]))
        self.processed_msg_ids: Set[str] = set()
        self.turn_history: List[Dict[str, Any]] = []
        self.last_seen_job_url: str = ""
        self.lock = threading.Lock()

    def get_live_cloud_base_url(self) -> str:
        """Reads the active Cloudflare Quick Tunnel base URL on the Cloud VM dynamically."""
        import re
        log_candidates = [
            Path.home() / ".cloudflared_dashboard.log",
            Path("/tmp/cloudflared_dashboard.log"),
        ]
        for lp in log_candidates:
            if lp.exists():
                try:
                    txt = lp.read_text(encoding="utf-8", errors="ignore")
                    matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
                    if matches:
                        return matches[-1]
                except Exception:
                    pass
        try:
            j_out = subprocess.run(
                ["journalctl", "-u", "linkedin-dashboard-tunnel.service", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=4,
            ).stdout
            matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", j_out or "")
            if matches:
                return matches[-1]
        except Exception:
            pass
        return os.environ.get("DASHBOARD_PUBLIC_URL", "https://had-arg-switches-parts.trycloudflare.com")

    def get_sheet_links_message(self) -> str:
        """Builds an instant response with live Cloud VM Master Sheet & Excel/CSV download links."""
        base_url = self.get_live_cloud_base_url().rstrip("/")
        sent_cnt = pend_cnt = rep_cnt = blk_cnt = 0
        try:
            db_path = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")
            conn = sqlite3.connect(db_path, timeout=15.0)
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS bounced_blocklist (email TEXT PRIMARY KEY, reason TEXT, detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            counts = dict(c.execute("SELECT status, COUNT(*) FROM outreach_leads GROUP BY status").fetchall())
            sent_cnt = counts.get("sent", 0) + counts.get("unresponsive", 0)
            pend_cnt = counts.get("pending", 0)
            rep_cnt = counts.get("replied", 0)
            blk_cnt = c.execute("SELECT COUNT(*) FROM bounced_blocklist").fetchone()[0]
            conn.close()
        except Exception:
            pass

        total_clean = sent_cnt + pend_cnt + rep_cnt
        return (
            "📊 **Live Master Recruiter & HR Leads Sheet (24/7 Cloud VM)**\n"
            f"• **Clean Active Leads:** `{total_clean:,}` (`{sent_cnt:,}` Sent | `{pend_cnt:,}` Pending | `{rep_cnt:,}` Replied | `0` Bounced in sheet — `{blk_cnt:,}` dead bounces auto-deleted)\n\n"
            f"🔗 **Open Live Searchable Sheet (Browser):**\n{base_url}/sheet\n\n"
            f"📥 **Download Clean Excel (`.xlsx`):**\n{base_url}/download/MASTER_LEADS_SHEET.xlsx\n\n"
            f"📄 **Download Clean CSV (`.csv`):**\n{base_url}/download/ACTIVE_DELIVERED_LEADS.csv\n\n"
            f"📈 **Live Career Autopilot Dashboard:**\n{base_url}/p/dashboard"
        )

    def send_chunked(self, target_channel: str, text: str) -> None:
        """Sends messages safely within Discord's 2000-char limit."""
        if not text:
            return
        chunk_size = 1900
        for i in range(0, len(text), chunk_size):
            self.client.send_message(target_channel, text[i:i + chunk_size])
            time.sleep(0.3)

    def execute_bash_direct(self, cmd: str) -> str:
        """Fast-path execution for direct shell commands (!sh <cmd>) with hard anti-fake-SQL integrity firewall."""
        import re
        cmd_norm = re.sub(r"\s+", " ", cmd.lower())
        # HARD INTEGRITY FIREWALL: Never allow raw SQL to fake jobs.status = 'applied'
        if (
            any(sql_kw in cmd_norm for sql_kw in ["update jobs", "insert into jobs", "replace into jobs"])
            and ("'applied'" in cmd_norm or '"applied"' in cmd_norm)
            and "priority_exec.py" not in cmd_norm
            and "process_external_redirects.py" not in cmd_norm
        ):
            return (
                "🛑 BLOCKED BY INTEGRITY FIREWALL: Raw SQL setting jobs.status='applied' is strictly forbidden! "
                "Jobs may ONLY be marked 'applied' by real Playwright submission + verified confirmation receipt "
                "(use `scripts/priority_exec.py apply --url <url>`)."
            )
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=110, cwd=WORKSPACE_DIR)
            stdout = res.stdout.strip()
            stderr = res.stderr.strip()
            out = stdout if stdout else (stderr if stderr else "Exit code 0 (no output)")
            return out[:1800]
        except subprocess.TimeoutExpired:
            return "Command timed out after 110 seconds."
        except Exception as e:
            return f"Bash Error: {e}"

    def get_system_status_text(self) -> str:
        """Generates real-time system status directly from local processes and DB without LLM quota."""
        import datetime
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_str = datetime.datetime.now(ist_tz).strftime('%d %b %Y, %I:%M %p IST')
        lines = [f"📊 **Live Systems Status Report** ({now_str})\n"]

        ps_res = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
        lines.append("**24/7 Standalone Oracle Cloud VM (`130.210.108.55` — Zero Local Device Dependency):**")
        cloud_procs = {
            'Portal Hunter (`Instahyre REST + Greenhouse/Lever/Ashby 24/7`)': 'run_portal_worker.py',
            'Cold Email Worker (`Dual-Inbox + Reply Scanner`)': 'run_email_worker.py',
            'Instant 6s Bounce Monitor': 'run_bounce_monitor.py',
            'Live Sheet & Dashboard Server': 'run_dashboard_server.py',
            'Discord `#prompt` AI Listener': 'discord_listener.py',
        }
        for label, pat in cloud_procs.items():
            running = any(pat in l for l in ps_res.splitlines())
            status_icon = "🟢 RUNNING" if running else "🔴 STOPPED"
            lines.append(f"• {label}: {status_icon}")

        try:
            from src.services.outreach_campaign import is_followups_enabled
            fu_en = is_followups_enabled()
            lines.append(
                f"• Cold Email Follow-Ups (`+3d / +7d`): "
                f"{'🟢 ENABLED' if fu_en else '⏸️ PAUSED (waiting for your start trigger)'}"
            )
        except Exception:
            pass

        try:
            db_path = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")
            if not os.path.exists(db_path):
                db_path = str(PROJECT_ROOT / ".linkedin_agent.db")
            if not os.path.exists(db_path):
                db_path = str(PROJECT_ROOT / "agent.db")
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute('SELECT status, count(*) FROM outreach_leads GROUP BY status')
            email_counts = dict(c.fetchall())
            c.execute('CREATE TABLE IF NOT EXISTS bounced_blocklist (email TEXT PRIMARY KEY, reason TEXT, detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
            blk_cnt = c.execute('SELECT count(*) FROM bounced_blocklist').fetchone()[0]
            lines.append("\n**Outreach Leads (100% Bounce-Clean):**")
            lines.append(
                f"• Sent: {email_counts.get('sent', 0)} | Pending: {email_counts.get('pending', 0)} | "
                f"Replies: {email_counts.get('replied', 0)} | Bounces Auto-Purged: {blk_cnt}"
            )

            try:
                c.execute('SELECT count(*) FROM linkedin_connections_outreach')
                li_sent = c.fetchone()[0]
                lines.append(f"\n**LinkedIn Outreach:**\n• Total 1st-Degree Sent: {li_sent}")
            except Exception:
                pass

            c.execute('SELECT status, count(*) FROM jobs GROUP BY status')
            job_counts = dict(c.fetchall())
            lines.append(f"\n**Jobs Pipeline:**\n• Applied: {job_counts.get('applied', 0)} | Redirects Pending: {job_counts.get('external_redirect', 0)} | New: {job_counts.get('new', 0)}")
            conn.close()
        except Exception as e:
            lines.append(f"\nDB Error: {e}")

        base_url = self.get_live_cloud_base_url().rstrip("/")
        lines.append(
            f"\n**Live Cloud Links:**\n"
            f"• 📋 **Live Sheet:** {base_url}/sheet\n"
            f"• 📥 **Excel Download:** {base_url}/download/MASTER_LEADS_SHEET.xlsx\n"
            f"• 📊 **Dashboard:** {base_url}/p/dashboard"
        )

        return "\n".join(lines)

    def ask_gemini_agent(self, prompt: str, target_channel: str = None) -> str:
        """CLI-grade tool-calling Gemini agent running 24/7 on Cloud VM (zero laptop dependency)."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key and os.path.exists(str(PROJECT_ROOT / ".env")):
            for line in open(str(PROJECT_ROOT / ".env")):
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
        if not api_key:
            return "❌ Agent Error: No `GEMINI_API_KEY` configured in `.env`."

        stop_typing = threading.Event()
        if target_channel:
            def _keep_typing():
                while not stop_typing.is_set():
                    try:
                        self.client.trigger_typing(target_channel)
                    except Exception:
                        pass
                    time.sleep(4)
            threading.Thread(target=_keep_typing, daemon=True).start()

        py_exec = sys.executable or "python3"
        db_loc = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")
        live_base = self.get_live_cloud_base_url().rstrip("/")
        # Load candidate profile summary so Cloud Gemini always has Vishal's full resume/profile context
        profile_snippet = ""
        try:
            prof_file = Path(WORKSPACE_DIR) / "src" / "profiles" / "candidate_profile.json"
            if prof_file.exists():
                prof_obj = json.loads(prof_file.read_text(encoding="utf-8"))
                profile_snippet = json.dumps(
                    {
                        "personal": prof_obj.get("personal", {}),
                        "education": prof_obj.get("education", {}),
                        "skills_summary": prof_obj.get("skills_summary", {}),
                        "screening_defaults": prof_obj.get("screening_defaults", {}),
                    }
                )
        except Exception:
            pass

        host_desc = (
            f"100% Standalone 24/7 Oracle Cloud VM (`130.210.108.55`, workspace `{WORKSPACE_DIR}`, Python `{py_exec}`, SQLite DB `{db_loc}`). "
            "ZERO dependency on Vishal's local laptop/Mac — everything runs autonomously 24/7 in the Cloud:\n"
            "  • `linkedin-email-worker.service` (24/7 simultaneous Cold Email across `vishal.gupta.ug22@nsut.ac.in` & `vishal90301@gmail.com` + pipelined IMAP reply scanner; follow-ups controlled via `data/followup_control.json`)\n"
            "  • `linkedin-bounce-monitor.service` (6s parallel IMAP bounce wiper & DB/sheet purger)\n"
            "  • `linkedin-portal-worker.service` (24/7 Instahyre Zero-RAM REST Auto-Apply + Greenhouse/Lever/Ashby/Workday/Google Forms)\n"
            "  • `linkedin-dashboard.service` + `linkedin-dashboard-tunnel.service` "
            f"(Live Master Sheet: `{live_base}/sheet`, Clean Excel Download: `{live_base}/download/MASTER_LEADS_SHEET.xlsx`, Clean CSV: `{live_base}/download/ACTIVE_DELIVERED_LEADS.csv`, Dashboard: `{live_base}/p/dashboard`)\n"
            "  • `linkedin-discord-listener.service` (this 24/7 Discord `#prompt` CLI daemon)\n"
            "SQLite DB Tables in `~/.linkedin_agent.db`:\n"
            "  • `jobs(id, job_id, title, company, location, posted_at, url, match_score, scorecard, status, created_at)` — `status='applied'` ONLY for genuinely submitted & confirmed apps.\n"
            "  • `outreach_leads(id, name, email, company, role_target, sender_email, status, first_sent_at, followup_1_at, followup_2_at, last_sent_at, followup_count, reply_snippet)` — `status IN ('pending', 'sent', 'replied', 'unresponsive')`.\n"
            "  • `bounced_blocklist(email, reason, detected_at)` — dead/bounced addresses permanently blocked & deleted from `outreach_leads`.\n"
            "  • `linkedin_connections_outreach(id, name, headline, status, sent_at)` — historical LinkedIn 1st-degree DMs.\n"
            "Priority #0 Immediate Cloud Execution Commands (`scripts/priority_exec.py`):\n"
            f"  1) **Send Immediate Email (Cold Pitch+CV OR Custom Email — bypasses 2,200-lead queue!):**\n"
            f"     - Cold pitch + CV right now: `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py email --to \"hr@company.com\" --name \"HR\" --company \"Company\" --role \"APM\"`\n"
            f"     - Custom email right now: `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py email --to \"x@y.com\" --subject \"...\" --body \"...\" --no-cv`\n"
            f"  2) **Apply Immediately to Any Job / Form / Post URL (Instahyre, Greenhouse, Lever, Ashby, Keka, Workday, Google Form, `lnkd.in/p/...`):**\n"
            f"     `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py apply --url \"<job_url>\" --role \"Product Analyst\"`\n"
            "  3) **Batch Instahyre Sweep on Demand:** `from src.services.instahyre_service import run_instahyre_auto_apply; run_instahyre_auto_apply(max_apps=10)`.\n"
            "  4) **Enable / Pause Cold Email Follow-Ups:** `from src.services.outreach_campaign import set_followups_enabled; set_followups_enabled(True|False)`.\n"
            "  5) **Any Cloud DB / Sheet / Worker Query or Edit:** Execute inline immediately via `execute_shell_command` on `{db_loc}` / `{WORKSPACE_DIR}` and verify!\n"
            "• 🚨 ABSOLUTE HONESTY & ZERO-MAC-DEPENDENCY LAW:\n"
            "  - NEVER depend on, wait on, or attempt to connect to Vishal's local Mac for anything.\n"
            "  - NEVER run raw SQL (`UPDATE jobs SET status='applied'` or `INSERT INTO jobs`) to fake job applications!"
        )
        system_context = (
            "You are Vishal Gupta's Priority #0 Standalone Cloud AI Agent running 24/7 in Discord #prompt with ZERO local device dependency.\n"
            f"Candidate Profile JSON: {profile_snippet}\n"
            f"Execution Host: {host_desc}\n"
            "STRICT PRIORITY #0 & HONESTY RULES:\n"
            "1) IMMEDIATE PRIORITY EXECUTION: Every user command in #prompt has TOP PRIORITY over all background workers. Execute it IMMEDIATELY right now on this Cloud VM using `execute_shell_command` (and `scripts/priority_exec.py` for email/apply tasks) and report the REAL verified result!\n"
            "2) CONTEXTUAL FOLLOW-UPS: Pay close attention to recent Discord channel conversation history! Combine multi-message instructions seamlessly.\n"
            "3) NEVER hallucinate or claim something was sent/applied unless `execute_shell_command` returned verified confirmation.\n"
            "4) NEVER mention past employer names under any circumstances.\n"
            "5) Keep responses concise, factual, and formatted in clean Markdown."
        )

        # Hydrate conversation history directly from real Discord channel messages so context is NEVER lost across restarts or Mac sleep/wake!
        contents: List[Dict[str, Any]] = []
        try:
            ch_to_read = target_channel or self.channel_id
            recent_discord = self.client.get_channel_messages(ch_to_read, limit=10)
            for dm in reversed(recent_discord):
                dm_txt = (dm.get("content") or "").strip()
                if not dm_txt or dm_txt == prompt.strip():
                    continue
                dm_role = "model" if (dm.get("author") or {}).get("bot") else "user"
                if contents and contents[-1]["role"] == dm_role:
                    contents[-1]["parts"][0]["text"] += "\n" + dm_txt[:1000]
                else:
                    contents.append({"role": dm_role, "parts": [{"text": dm_txt[:1000]}]})
            # Gemini requires `contents` to start with a `user` turn
            while contents and contents[0]["role"] != "user":
                contents.pop(0)
        except Exception:
            contents = list(self.turn_history[-10:])
        if contents and contents[-1]["role"] == "user":
            contents[-1]["parts"][0]["text"] += "\n" + prompt
        else:
            contents.append({"role": "user", "parts": [{"text": prompt}]})

        tools_spec = [{
            "functionDeclarations": [{
                "name": "execute_shell_command",
                "description": "Execute a real bash command in WORKSPACE_DIR on this server and return stdout/stderr.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {"type": "STRING", "description": "Exact bash command to execute"}
                    },
                    "required": ["command"]
                }
            }]
        }]

        models_to_try = ["gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash"]
        active_model_idx = 0

        try:
            max_steps = 12
            for step_idx in range(max_steps + 1):
                is_final_summary = step_idx == max_steps
                model_name = models_to_try[active_model_idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                if is_final_summary:
                    contents.append({
                        "role": "user",
                        "parts": [{"text": "Now provide a clear, concise final summary of what you found and executed so far (no more tool calls)."}]
                    })
                payload = {
                    "systemInstruction": {"parts": [{"text": system_context}]},
                    "contents": contents,
                    "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.2}
                }
                if not is_final_summary:
                    payload["tools"] = tools_spec
                data = json.dumps(payload).encode("utf-8")
                res_json = None
                last_api_err = None
                for _retry in range(3):
                    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                    try:
                        resp = urllib.request.urlopen(req, timeout=50)
                        res_json = json.loads(resp.read().decode())
                        break
                    except Exception as api_err:
                        last_api_err = api_err
                        time.sleep(1.5)
                if res_json is None:
                    if active_model_idx + 1 < len(models_to_try):
                        active_model_idx += 1
                        # Strip model-specific thoughtSignature if switching models mid-turn
                        for msg_item in contents:
                            for pt in msg_item.get("parts", []):
                                pt.pop("thoughtSignature", None)
                        continue
                    return f"❌ AI Tool-Agent Error: `{last_api_err}`"

                cand_content = res_json["candidates"][0]["content"]
                parts = cand_content.get("parts", [])
                fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not fn_calls or is_final_summary:
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    final_reply = "\n".join(text_parts).strip()
                    if not final_reply and not is_final_summary:
                        contents.append(cand_content)
                        contents.append({
                            "role": "user",
                            "parts": [{"text": "Provide a clear, specific answer to the user's message based on the real system/tool results (never reply with empty text)."}]
                        })
                        continue
                    final_reply = final_reply or "✅ Command executed."
                    self.turn_history.append({"role": "user", "parts": [{"text": prompt[:1000]}]})
                    self.turn_history.append({"role": "model", "parts": [{"text": final_reply[:1200]}]})
                    if len(self.turn_history) > 16:
                        self.turn_history = self.turn_history[-16:]
                    return final_reply

                contents.append(cand_content)
                fn_responses = []
                for fc in fn_calls:
                    cmd_str = (fc.get("args") or {}).get("command", "")
                    print(f"[DiscordCLI:CloudTool] Executing: {cmd_str}")
                    tool_out = self.execute_bash_direct(cmd_str)
                    fn_resp_obj = {
                        "name": fc["name"],
                        "response": {"output": tool_out}
                    }
                    if "id" in fc:
                        fn_resp_obj["id"] = fc["id"]
                    fn_responses.append({"functionResponse": fn_resp_obj})
                contents.append({"role": "user", "parts": fn_responses})
            return "✅ Completed execution."
        finally:
            stop_typing.set()

    def run_antigravity_cli_turn(self, prompt: str, target_channel: str, msg_id: str) -> str:
        """Executes every turn directly on the 24/7 Cloud Gemini Tool-Agent (100% standalone, zero local device dependency)."""
        print(f"[DiscordCLI] Running 24/7 Cloud CLI Tool-Agent for prompt: {prompt}")
        return self.ask_gemini_agent(prompt, target_channel=target_channel)

    def process_command(self, content: str, msg_id: str, author_id: str, channel_id: str = None) -> None:
        """Parses and acts on instructions from Discord."""
        text = content.strip()
        lower = text.lower()
        target_channel = channel_id or self.channel_id

        print(f"\n[DiscordListener:Msg:{target_channel}] Received from user: {text}")
        self.client.trigger_typing(target_channel)
        self.client.add_reaction(target_channel, msg_id, "⚙️")

        # 0. Instant Fast Path: ONLY pure link requests (never hijack questions about dashboard/sheet!)
        clean_q = lower.strip("?!. ")
        pure_sheet_queries = {
            "sheet", "sheet link", "leads sheet", "leads link", "mail list",
            "master list", "excel", "xlsx", "csv", "download leads",
            "dashboard", "dashboard link", "links", "cloud links",
        }
        if clean_q in pure_sheet_queries:
            print("[DiscordCLI] Serving instant Cloud VM Master Sheet & Download links")
            self.send_chunked(target_channel, self.get_sheet_links_message())
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 0b. Deterministic Priority #0 Fast-Path for Job / Post URLs & "fill it" follow-ups!
        import re
        urls_in_msg = [u.rstrip(".,;>)") for u in re.findall(r"https?://[^\s<>]+", text)]
        job_url_domains = (
            "instahyre.com", "naukri.com",
            "greenhouse.io", "lever.co", "ashbyhq.com", "keka.com", "myworkdayjobs.com",
            "lnkd.in/", "linkedin.com/posts/", "linkedin.com/feed/update/", "linkedin.com/jobs/",
            "forms.gle", "docs.google.com/forms", "smartrecruiters.com", "bamboohr.com",
            "icims.com", "medpace.com", "careers.", "jobs."
        )
        matched_job_urls = [
            u for u in urls_in_msg
            if any(dom in u.lower() for dom in job_url_domains) and "linkedin.com/in/" not in u.lower()
        ]
        if matched_job_urls:
            self.last_seen_job_url = matched_job_urls[0]

        is_fill_followup = (
            not urls_in_msg
            and bool(self.last_seen_job_url)
            and len(text.split()) <= 6
            and any(k in lower for k in ["fill it", "fill this", "apply", "apply now", "submit it", "do it"])
        )
        is_direct_url_apply = bool(matched_job_urls) and (
            len(text.split()) <= 10
            or any(k in lower for k in ["apply", "fill", "submit", "job", "check this"])
        )
        if is_direct_url_apply or is_fill_followup:
            target_apply_url = matched_job_urls[0] if matched_job_urls else self.last_seen_job_url
            print(f"[DiscordCLI] Deterministic Priority #0 Apply Fast-Path -> {target_apply_url}")
            py_bin = sys.executable or "python3"
            exec_script = str(PROJECT_ROOT / "scripts" / "priority_exec.py")
            try:
                proc = subprocess.run(
                    [py_bin, exec_script, "apply", "--url", target_apply_url, "--channel", target_channel],
                    cwd=WORKSPACE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=140,
                )
                out_raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
                clean_lines = [
                    ln for ln in out_raw.splitlines()
                    if not ln.startswith("[ATSFiller]") and not ln.startswith("[LLM]")
                ]
                reply_msg = "\n".join(clean_lines).strip() or out_raw or "⚠️ Application attempt finished with no output."
                self.send_chunked(target_channel, reply_msg[:1900])
                self.client.add_reaction(target_channel, msg_id, "✅")
                return
            except Exception as ae:
                self.send_chunked(target_channel, f"❌ Priority Apply Error on `{target_apply_url}`: `{ae}`")
                return

        # 0c. Deterministic Priority #0 Fast-Path for Immediate Cold Emails (e.g. "avila@onfinance.in and sanjana@onfinance.in send mails")
        emails_in_msg = list(dict.fromkeys(
            e.lower().strip(".,;<>()\"'")
            for e in re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)
            if not any(own in e.lower() for own in ("vishal90301@", "vishal.gupta.ug22@"))
        ))
        is_direct_email_cmd = (
            bool(emails_in_msg)
            and any(k in lower for k in ["send mail", "send mails", "send email", "send emails", "mail them", "email them", "send cold", "pitch"])
            and not any(k in lower for k in ["subject:", "body:", "say ", "saying ", "write:"])
            and len(text.split()) <= 25
        )
        if is_direct_email_cmd:
            print(f"[DiscordCLI] Deterministic Priority #0 Email Fast-Path -> {emails_in_msg}")
            py_bin = sys.executable or "python3"
            exec_script = str(PROJECT_ROOT / "scripts" / "priority_exec.py")
            receipts = []
            for em_addr in emails_in_msg[:10]:
                local_part, dom_part = em_addr.split("@", 1)
                first_guess = re.split(r"[._0-9-]+", local_part)[0].capitalize() or "Hiring Team"
                comp_guess = dom_part.split(".")[0].capitalize()
                try:
                    proc = subprocess.run(
                        [
                            py_bin, exec_script, "email",
                            "--to", em_addr,
                            "--name", first_guess,
                            "--company", comp_guess,
                            "--role", "Business / Product Analyst / Management",
                        ],
                        cwd=WORKSPACE_DIR,
                        capture_output=True,
                        text=True,
                        timeout=45,
                    )
                    out_e = "\n".join(
                        ln for ln in ((proc.stdout or "").strip() or (proc.stderr or "").strip()).splitlines()
                        if not ln.startswith("[ColdMailer]")
                    ).strip()
                    receipts.append(out_e or f"✅ Dispatched to `{em_addr}`")
                except Exception as ee:
                    receipts.append(f"❌ Error sending to `{em_addr}`: `{ee}`")
            self.send_chunked(target_channel, "\n\n".join(receipts)[:1900])
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 1. Fast path: Direct Raw Terminal / Shell Execution (!sh <cmd> or !bash <cmd>)
        if text.startswith("!sh ") or text.startswith("!bash ") or text.startswith("!run "):
            cmd_to_run = text.split(" ", 1)[1].strip()
            print(f"[DiscordCLI] Executing direct bash: {cmd_to_run}")
            res = self.execute_bash_direct(cmd_to_run)
            self.send_chunked(target_channel, f"💻 **CLI Terminal Output:**\n```text\n{res}\n```")
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 1b. Fast path: 1-Task-at-a-Time Verified LinkedIn Automation (!linkedin_run ...)
        if lower.startswith("!linkedin_run") or lower.startswith("!li "):
            parts = text.split()
            sub_action = parts[1].lower() if len(parts) >= 2 else "check"
            target_arg = parts[2].strip("<>") if len(parts) >= 3 else ""
            py_bin = sys.executable or "python3"
            script_path = str(PROJECT_ROOT / "scripts" / "linkedin_single_task.py")
            if sub_action in ("check", "status"):
                cmd_list = [py_bin, script_path, "check"]
            elif sub_action == "next":
                cmd_list = [py_bin, script_path, "next"]
            elif sub_action == "apply":
                cmd_list = [py_bin, script_path, "apply", target_arg or "next"]
            else:
                # Allow `!linkedin_run https://www.linkedin.com/jobs/view/...` directly
                if sub_action.startswith("http"):
                    cmd_list = [py_bin, script_path, "apply", parts[1].strip("<>")]
                else:
                    self.send_chunked(
                        target_channel,
                        "ℹ️ **1-Task LinkedIn Commands:**\n"
                        "• `!linkedin_run check` — Verify LinkedIn session & view top queued roles\n"
                        "• `!linkedin_run next` — Execute 1 verified Easy Apply task from top of queue\n"
                        "• `!linkedin_run apply <linkedin_job_url>` — Apply to 1 specific job URL with strict receipt check"
                    )
                    self.client.add_reaction(target_channel, msg_id, "✅")
                    return

            try:
                proc = subprocess.run(cmd_list, cwd=WORKSPACE_DIR, capture_output=True, text=True, timeout=150)
                out = (proc.stdout or "").strip() or (proc.stderr or "").strip() or "Completed with no output."
                # Extract clean summary lines (skip verbose [EasyApply] debug prints if summary present)
                summary_lines = [ln for ln in out.splitlines() if not ln.startswith("[EasyApply]") and not ln.startswith("[ATSFiller]")]
                clean_out = "\n".join(summary_lines).strip() or out
                self.send_chunked(target_channel, clean_out[:1900])
                self.client.add_reaction(target_channel, msg_id, "✅")
            except Exception as le:
                self.send_chunked(target_channel, f"❌ LinkedIn 1-Task Runner Error: `{le}`")
            return

        # 2. Fast path: ONLY pure full-system status pings (specific questions like 'status of cold mailer?' go to AGY/Gemini!)
        pure_status_queries = {
            "status", "system status", "systems status", "running systems",
            "currently running systems", "what systems are running", "which systems are running",
        }
        if clean_q in pure_status_queries:
            print("[DiscordCLI] Serving fast-path local status check")
            status_text = self.get_system_status_text()
            self.send_chunked(target_channel, status_text)
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 2b. Fast path: Cloud-Synced LinkedIn Stop / Start Control (0 LLM tokens)
        if any(p in lower for p in ["stop linkedin", "pause linkedin", "halt linkedin", "linkedin stop", "disable linkedin"]):
            from src.services.linkedin_control import set_linkedin_enabled
            st = set_linkedin_enabled(False, reason="stopped_via_discord_prompt", updated_by="discord_prompt")
            self.send_chunked(
                target_channel,
                f"🛑 **LinkedIn Automation STOPPED** (`{st['updated_at']}`).\n"
                "• All LinkedIn tasks (messaging, feed hunt, LinkedIn Jobs) will stay completely paused until you send `start linkedin` here in `#prompt` or on CLI."
            )
            self.client.add_reaction(target_channel, msg_id, "🛑")
            return

        if any(p in lower for p in ["start linkedin", "resume linkedin", "linkedin start", "enable linkedin"]):
            from src.services.linkedin_control import set_linkedin_enabled
            st = set_linkedin_enabled(True, reason="started_via_discord_prompt", updated_by="discord_prompt")
            self.send_chunked(
                target_channel,
                f"🟢 **LinkedIn Automation ENABLED** (`{st['updated_at']}`).\n"
                "• Sequential 1-task LinkedIn cycle is active and will resume on the next heartbeat (~5–12s)."
            )
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 2c. Fast path: Follow-Up Emails Start / Stop Control (24/7 Cloud VM)
        if any(p in lower for p in ["stop followup", "pause followup", "disable followup", "dont start followup", "don't start followup"]):
            from src.services.outreach_campaign import set_followups_enabled
            st = set_followups_enabled(False, updated_by="discord_prompt")
            self.send_chunked(
                target_channel,
                f"⏸️ **Cold Email Follow-Ups PAUSED** (`{st['updated_at']}`).\n"
                "• Zero follow-up emails will be sent until you send `start followups` here in `#prompt` or on CLI."
            )
            self.client.add_reaction(target_channel, msg_id, "⏸️")
            return

        if any(p in lower for p in ["start followup", "resume followup", "enable followup"]):
            from src.services.outreach_campaign import set_followups_enabled
            st = set_followups_enabled(True, updated_by="discord_prompt")
            self.send_chunked(
                target_channel,
                f"🟢 **Cold Email Follow-Ups ENABLED** (`{st['updated_at']}`).\n"
                "• 24/7 Cloud Email Worker will begin dispatching same-thread follow-ups to non-replied leads on its next cycle!"
            )
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 3. Emergency Safety Pause
        if any(w in lower for w in ["emergency stop", "kill all workers", "pause everything"]):
            try:
                from src.services.linkedin_control import set_linkedin_enabled
                set_linkedin_enabled(False, reason="emergency_stop", updated_by="discord_prompt")
            except Exception:
                pass
            subprocess.run("pkill -f 'master_autonomous_cycle'", shell=True)
            subprocess.run("pkill -f 'run_email_worker'", shell=True)
            subprocess.run("pkill -f 'run_portal_worker'", shell=True)
            self.client.send_message(
                target_channel,
                "🛑 **EMERGENCY STOP TRIGGERED:** LinkedIn control disabled and background workers stopped."
            )
            self.client.add_reaction(target_channel, msg_id, "🛑")
            return

        # 4. Exact Antigravity CLI Execution (With conversation continuation -c)
        with self.lock:
            response_text = self.run_antigravity_cli_turn(text, target_channel, msg_id)
            self.send_chunked(target_channel, response_text)
            self.client.add_reaction(target_channel, msg_id, "✅")

    def start_polling(self, interval_seconds: int = 3) -> None:
        """Polls Discord channels (#prompt and #jobs) for new incoming user commands."""
        print(f"[DiscordListener] Active on channels {self.watch_channels}. Polling every {interval_seconds}s...")
        print(f"[DiscordListener] Engine: 24/7 Cloud VM CLI + Instant Sheet/Status Responder")

        for ch in self.watch_channels:
            try:
                initial_msgs = self.client.get_channel_messages(ch, limit=25)
                for m in initial_msgs:
                    self.processed_msg_ids.add(m["id"])
                print(f"[DiscordListener] Initialized channel {ch} with {len(initial_msgs)} existing messages.")
            except Exception as e:
                print(f"[DiscordListener] Notice fetching initial messages for {ch}: {e}")

        while True:
            try:
                for ch in self.watch_channels:
                    messages = self.client.get_channel_messages(ch, limit=10)
                    for msg in reversed(messages):
                        msg_id = msg.get("id")
                        if msg_id in self.processed_msg_ids:
                            continue
                        self.processed_msg_ids.add(msg_id)

                        author = msg.get("author", {})
                        if author.get("bot"):
                            continue

                        content = msg.get("content", "").strip()
                        # In #jobs channel (alerts channel), only respond to explicit commands/questions or sheet/status queries
                        if ch != self.channel_id:
                            clow = content.lower()
                            if not any(
                                k in clow
                                for k in ("sheet", "excel", "xlsx", "csv", "status", "dashboard", "link", "!sh", "!li", "stop", "start")
                            ):
                                continue

                        if content:
                            t = threading.Thread(
                                target=self.process_command,
                                args=(content, msg_id, author.get("id"), ch),
                                daemon=True,
                            )
                            t.start()

                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                print("[DiscordListener] Stopped by user.")
                break
            except Exception as e:
                print(f"[DiscordListener] Polling tick error: {e}")
                time.sleep(interval_seconds)

if __name__ == "__main__":
    listener = DiscordListener()
    listener.start_polling()
