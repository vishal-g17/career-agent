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

AGY_BIN = os.environ.get("AGY_BIN") or shutil.which("agy") or str(Path.home() / ".local" / "bin" / "agy")
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
        is_mac = sys.platform == "darwin"
        lines.append(f"**Active Host (`{'Mac + Ridealong' if is_mac else 'Oracle Cloud VM'}`):**")
        local_procs = {
            'LinkedIn Master Cycle (1-Task Loop)': 'master_autonomous_cycle.py',
            'Ridealong Browser Daemon': 'ridealong',
            'Discord CLI Listener': 'discord_listener.py',
        } if is_mac else {
            'Portal ATS Hunter (Turbo)': 'run_portal_worker.py',
            'Cold Email Worker (Dual-Inbox)': 'run_email_worker.py',
            'Instant 6s Bounce Monitor': 'run_bounce_monitor.py',
            'Live Sheet & Dashboard Server': 'run_dashboard_server.py',
            'Discord Listener': 'discord_listener.py',
        }
        for label, pat in local_procs.items():
            running = any(pat in l for l in ps_res.splitlines())
            status_icon = "🟢 RUNNING" if running else "🔴 STOPPED"
            lines.append(f"• {label}: {status_icon}")

        try:
            from src.services.linkedin_control import read_local_control
            ctl = read_local_control()
            li_en = ctl.get("linkedin_enabled", True)
            li_stage = ctl.get("stage", "idle")
            li_hb = ctl.get("last_mac_heartbeat", "n/a")
            li_reason = ctl.get("reason", "active")
            lines.append(
                f"• LinkedIn Automation (Cloud-Synced Control): "
                f"{'🟢 ENABLED' if li_en else '🛑 STOPPED'} (`{li_stage}` | reason: `{li_reason}` | last heartbeat: `{li_hb}`)"
            )
        except Exception:
            pass

        if is_mac:
            try:
                vm_chk = subprocess.run(
                    [
                        "ssh", "-i", "/Users/admin/Downloads/agent-vm-key.key",
                        "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=4",
                        "opc@130.210.108.55",
                        "systemctl is-active linkedin-portal-worker.service linkedin-email-worker.service"
                    ],
                    capture_output=True, text=True, timeout=6
                ).stdout.strip().splitlines()
                pw_ok = len(vm_chk) >= 1 and vm_chk[0].strip() == "active"
                ew_ok = len(vm_chk) >= 2 and vm_chk[1].strip() == "active"
                lines.append("\n**24/7 Oracle Cloud VM (`130.210.108.55`):**")
                lines.append(f"• Portal ATS Hunter: {'🟢 RUNNING' if pw_ok else '🔴 STOPPED'}")
                lines.append(f"• Cold Email Worker: {'🟢 RUNNING' if ew_ok else '🔴 STOPPED'}")
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

        is_mac = sys.platform == "darwin"
        py_exec = sys.executable or "python3"
        db_loc = os.environ.get("DATABASE_PATH") or str(Path.home() / ".linkedin_agent.db")
        live_base = self.get_live_cloud_base_url().rstrip("/")
        host_desc = (
            f"Local Mac (`{WORKSPACE_DIR}`), Python `{py_exec}`, DB `{db_loc}`."
            if is_mac else
            f"24/7 Oracle Cloud VM (`130.210.108.55`, workspace `{WORKSPACE_DIR}`, Python `{py_exec}`, SQLite DB `{db_loc}`). "
            "Systemd services here: `linkedin-email-worker.service` (24/7 simultaneous Cold Email across `vishal.gupta.ug22@nsut.ac.in` & `vishal90301@gmail.com`), "
            "`linkedin-bounce-monitor.service` (6s parallel IMAP bounce wiper & DB/sheet purger), "
            "`linkedin-portal-worker.service` (24/7 Greenhouse/Lever/Ashby/Naukri turbo auto-apply), "
            "`linkedin-dashboard.service` + `linkedin-dashboard-tunnel.service` "
            f"(Live Master Sheet: `{live_base}/sheet`, Clean Excel Download: `{live_base}/download/MASTER_LEADS_SHEET.xlsx`, Clean CSV: `{live_base}/download/ACTIVE_DELIVERED_LEADS.csv`, Dashboard: `{live_base}/p/dashboard`), "
            "and `linkedin-discord-listener.service` (this Discord CLI daemon). "
            "Note on Job Applications & LinkedIn Posts: `scripts/priority_exec.py apply --url <url>` runs 24/7 directly on this Cloud VM! It automatically unwraps `lnkd.in/p/...` and `linkedin.com/posts/...` via OpenGraph HTML, extracts direct ATS links (Greenhouse, Lever, Ashby, Keka, Workday, Google Forms) + recruiter emails, and runs headless Playwright directly on the Cloud VM even when Vishal's Mac is asleep! Only pure `linkedin.com/jobs/view/...` Easy Apply pages and LinkedIn DMs require the residential Mac Ridealong session.\n"
            f"• To stop, pause, start, or check LinkedIn automation from this VM, run `{py_exec} {WORKSPACE_DIR}/src/services/linkedin_control.py stop` or `start` or `status`.\n"
            "• 🚨 ABSOLUTE HONESTY & INTEGRITY LAW:\n"
            "  - NEVER run raw SQL (`UPDATE jobs SET status='applied'` or `INSERT INTO jobs`) to fake job applications! That is strictly blocked by the firewall.\n"
            "  - NEVER say Greenhouse, Lever, Ashby, Keka, or `lnkd.in/p/...` links cannot be automated from the Cloud VM — ALWAYS run `priority_exec.py apply --url <url>` immediately and report its real output!\n"
            "• 🚨 PRIORITY #0 IMMEDIATE EXECUTION LAW (APPLIES TO EVERY TASK — LINKEDIN, EMAIL, PORTALS, DATA, SHEET, CONFIG):\n"
            "  Whenever Vishal asks you to do ANYTHING in `#prompt`, DO IT IMMEDIATELY ON PRIORITY #0 RIGHT NOW! NEVER queue his request behind background batches and NEVER reply 'Queued, will run later'!\n"
            "  Use the unified synchronous Priority #0 Executor (which executes immediately and waits for real verified completion before returning):\n"
            f"  1) **Send LinkedIn Message (Custom Text OR Job Pitch+CV) / Connect:**\n"
            f"     - Custom message (e.g. 'Send hello pagal to Shivani Gupta'):\n"
            f"       `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py linkedin-msg --query \"Shivani Gupta\" --name \"Shivani Gupta\" --custom-message \"Hello pagal\" --channel \"{target_channel or self.channel_id}\"`\n"
            f"     - Job outreach pitch + CV (e.g. 'Send linkedin msg to Rahul at Razorpay as PM'):\n"
            f"       `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py linkedin-msg --query \"Rahul Razorpay PM\" --name \"Rahul\" --company \"Razorpay\" --role \"PM\" --channel \"{target_channel or self.channel_id}\"`\n"
            f"  2) **Send Immediate Email (Custom OR Cold Pitch+CV — bypasses 2,200-lead queue!):**\n"
            f"     - Cold pitch + CV right now: `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py email --to \"hr@company.com\" --name \"HR\" --company \"Company\" --role \"APM\"`\n"
            f"     - Custom email right now: `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py email --to \"x@y.com\" --subject \"...\" --body \"...\" --no-cv`\n"
            f"  3) **Apply Immediately to Any Job / Form URL (LinkedIn, Greenhouse, Lever, Ashby, Google Form):**\n"
            f"     `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py apply --url \"<job_url>\" --role \"Product Analyst\" --channel \"{target_channel or self.channel_id}\"`\n"
            f"  4) **Run Any Urgent Command on Local Mac:**\n"
            f"     `{py_exec} {WORKSPACE_DIR}/scripts/priority_exec.py mac-sh --cmd \"<command>\" --channel \"{target_channel or self.channel_id}\"`\n"
            f"  5) **Any Cloud DB / Sheet / Worker Edit:** Execute inline immediately via `execute_shell_command` on `{db_loc}` / `{WORKSPACE_DIR}` and verify!"
        )
        system_context = (
            "You are Vishal Gupta's Priority #0 Autonomous CLI Agent running 24/7 in Discord #prompt.\n"
            f"Execution Host: {host_desc}\n"
            "STRICT PRIORITY #0 & HONESTY RULES:\n"
            "1) IMMEDIATE PRIORITY EXECUTION: Every user command in #prompt has TOP PRIORITY over all background workers. Execute it IMMEDIATELY right now using `execute_shell_command` (and `scripts/priority_exec.py` for LinkedIn/email/apply/Mac tasks) and report the REAL verified result!\n"
            "2) CONTEXTUAl FOLLOW-UPS: Pay close attention to recent conversation history! For example, if the user says 'Send hello pagal' and then 'To shivani gupta', combine them and immediately send the exact custom LinkedIn message `Hello pagal` to `Shivani Gupta` using `priority_exec.py linkedin-msg --custom-message \"Hello pagal\"`!\n"
            "3) NEVER hallucinate or claim something was sent unless `execute_shell_command` returned verified confirmation.\n"
            "4) NEVER mention past employer names under any circumstances.\n"
            "5) Keep responses concise, factual, and formatted in clean Markdown."
        )

        contents: List[Dict[str, Any]] = list(self.turn_history[-12:])
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

        models_to_try = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
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
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                try:
                    resp = urllib.request.urlopen(req, timeout=50)
                    res_json = json.loads(resp.read().decode())
                except Exception as api_err:
                    if active_model_idx + 1 < len(models_to_try):
                        active_model_idx += 1
                        continue
                    return f"❌ AI Tool-Agent Error: `{api_err}`"

                cand_content = res_json["candidates"][0]["content"]
                parts = cand_content.get("parts", [])
                fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not fn_calls or is_final_summary:
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    final_reply = "\n".join(text_parts).strip() or "✅ Done."
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
        """
        Executes the prompt through the Antigravity (AGY) CLI engine if installed,
        or runs the 24/7 Cloud VM tool-calling CLI agent when running on Linux VM.
        """
        if not shutil.which(AGY_BIN) and not os.path.exists(AGY_BIN):
            print(f"[DiscordCLI] Running 24/7 Cloud CLI Tool-Agent for prompt: {prompt}")
            return self.ask_gemini_agent(prompt, target_channel=target_channel)

        print(f"[DiscordCLI:AGY] Dispatching turn to Antigravity CLI: {prompt}")

        stop_typing = threading.Event()
        def keep_typing():
            while not stop_typing.is_set():
                try:
                    self.client.trigger_typing(target_channel)
                except Exception:
                    pass
                time.sleep(4)

        t_thread = threading.Thread(target=keep_typing, daemon=True)
        t_thread.start()

        agy_session_dir = os.path.expanduser("~/.discord_agy_workspace")
        os.makedirs(agy_session_dir, exist_ok=True)
        init_marker = os.path.join(agy_session_dir, ".agy_initialized")
        has_prior_session = os.path.exists(init_marker)

        effective_prompt = prompt
        if not has_prior_session:
            effective_prompt = (
                f"[Context: You are Vishal's Discord #prompt CLI agent. Workspace: {WORKSPACE_DIR}. "
                f"Local Mac runs Ridealong (:9230) + `scripts/master_autonomous_cycle.py` (LinkedIn 1-task loop: 10 msgs @ 1m gap -> 10m connections or post-body feed hunt -> 10m LinkedIn Jobs -> repeat). "
                f"OCI Cloud VM `opc@130.210.108.55` (`-i ~/Downloads/agent-vm-key.key`) runs 24/7 `linkedin-portal-worker` & `linkedin-email-worker`. "
                f"Always inspect real processes/SQLite (`~/.linkedin_agent.db`) before claiming anything started or sent. Never leak past employer names.]\n\n"
                f"User message: {prompt}"
            )

        def _invoke_agy(use_continue: bool, p_text: str):
            cmd = [AGY_BIN]
            if use_continue:
                cmd.append("-c")
            cmd.extend([
                "-p", p_text,
                "--add-dir", WORKSPACE_DIR,
                "--dangerously-skip-permissions"
            ])
            return subprocess.run(cmd, cwd=agy_session_dir, capture_output=True, text=True, timeout=180)

        try:
            res = _invoke_agy(has_prior_session, effective_prompt)
            stderr_lower = (res.stderr or "").lower()
            # Self-heal if continuation fails (e.g., first run or corrupted thought signature)
            if res.returncode != 0 and has_prior_session and any(
                err_k in stderr_lower for err_k in ["no previous", "no conversation", "thought signature", "invalid_argument", "not found"]
            ):
                print("[DiscordCLI:AGY] Continuation session reset needed; starting fresh clean AGY conversation...")
                res = _invoke_agy(False, effective_prompt)

            stop_typing.set()
            stdout = (res.stdout or "").strip()
            stderr = (res.stderr or "").strip()

            if res.returncode == 0 or stdout:
                try:
                    Path(init_marker).write_text("ok\n")
                except Exception:
                    pass

            if stdout:
                return stdout
            elif stderr:
                if "RESOURCE_EXHAUSTED" in stderr or "quota reached" in stderr:
                    return self.ask_gemini_agent(prompt)
                return f"⚠️ **CLI Notice:**\n```text\n{stderr[:1500]}\n```"
            else:
                return "✅ Action completed by Antigravity CLI."
        except subprocess.TimeoutExpired:
            stop_typing.set()
            return "⏱️ Antigravity CLI execution timed out after 180 seconds."
        except Exception as e:
            stop_typing.set()
            return f"❌ Antigravity CLI Error: `{e}`"

    def process_command(self, content: str, msg_id: str, author_id: str, channel_id: str = None) -> None:
        """Parses and acts on instructions from Discord."""
        text = content.strip()
        lower = text.lower()
        target_channel = channel_id or self.channel_id

        print(f"\n[DiscordListener:Msg:{target_channel}] Received from user: {text}")
        self.client.trigger_typing(target_channel)
        self.client.add_reaction(target_channel, msg_id, "⚙️")

        # 0. Instant Fast Path: Master Leads Sheet / Excel / CSV / Dashboard Link Request
        sheet_triggers = [
            "sheet", "excel", "xlsx", "csv", "leads link", "mail list",
            "master list", "download leads", "data link", "dashboard link", "dashboard"
        ]
        if any(trig in lower for trig in sheet_triggers) and len(text.split()) <= 14:
            print("[DiscordCLI] Serving instant Cloud VM Master Sheet & Download links")
            self.send_chunked(target_channel, self.get_sheet_links_message())
            self.client.add_reaction(target_channel, msg_id, "✅")
            return

        # 0b. Deterministic Priority #0 Fast-Path for Job / Post URLs & "fill it" follow-ups!
        import re
        urls_in_msg = [u.rstrip(".,;>)") for u in re.findall(r"https?://[^\s<>]+", text)]
        job_url_domains = (
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

        # 2. Fast path: Real-time System Status Check (Instant response without consuming CLI quota)
        status_triggers = [
            "status", "wht systems", "what systems", "systems running", "system status",
            "running systems", "which systems", "what is running", "current status",
            "working systems", "systems are working", "systems are currently working"
        ]
        if any(trig in lower for trig in status_triggers) and len(text.split()) < 10:
            print(f"[DiscordCLI] Serving fast-path local status check")
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
