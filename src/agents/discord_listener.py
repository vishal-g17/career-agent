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
        self.processed_msg_ids: Set[str] = set()
        self.turn_history: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def send_chunked(self, target_channel: str, text: str) -> None:
        """Sends messages safely within Discord's 2000-char limit."""
        if not text:
            return
        chunk_size = 1900
        for i in range(0, len(text), chunk_size):
            self.client.send_message(target_channel, text[i:i + chunk_size])
            time.sleep(0.3)

    def execute_bash_direct(self, cmd: str) -> str:
        """Fast-path execution for direct shell commands (!sh <cmd>)."""
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90, cwd=WORKSPACE_DIR)
            stdout = res.stdout.strip()
            stderr = res.stderr.strip()
            out = stdout if stdout else (stderr if stderr else "Exit code 0 (no output)")
            return out[:1800]
        except subprocess.TimeoutExpired:
            return "Command timed out after 90 seconds."
        except Exception as e:
            return f"Bash Error: {e}"

    def get_system_status_text(self) -> str:
        """Generates real-time system status directly from local processes and DB without LLM quota."""
        import datetime
        now_str = datetime.datetime.now().strftime('%d %b %Y, %I:%M %p IST')
        lines = [f"📊 **Live Systems Status Report** ({now_str})\n"]

        ps_res = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
        is_mac = sys.platform == "darwin"
        lines.append(f"**Active Host (`{'Mac + Ridealong' if is_mac else 'Oracle Cloud VM'}`):**")
        local_procs = {
            'LinkedIn Master Cycle (1-Task Loop)': 'master_autonomous_cycle.py',
            'Ridealong Browser Daemon': 'ridealong',
            'Discord CLI Listener': 'discord_listener.py',
        } if is_mac else {
            'Portal ATS Hunter': 'run_portal_worker.py',
            'Cold Email Worker': 'run_email_worker.py',
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
            lines.append("\n**Outreach Leads:**")
            lines.append(f"• Sent: {email_counts.get('sent', 0)} | Bounced: {email_counts.get('bounced', 0)} | Replies: {email_counts.get('replied', 0)}")

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
        host_desc = (
            f"Local Mac (`{WORKSPACE_DIR}`), Python `{py_exec}`, DB `{db_loc}`."
            if is_mac else
            f"24/7 Oracle Cloud VM (`130.210.108.55`, workspace `{WORKSPACE_DIR}`, Python `{py_exec}`, SQLite DB `{db_loc}`). "
            "Systemd services here: `linkedin-email-worker.service` (24/7 Cold Email rotating `vishal.gupta.ug22@nsut.ac.in` & `vishal90301@gmail.com` via `src.services.cold_mailer.send_cold_email_smtp`), "
            "`linkedin-portal-worker.service` (24/7 Naukri/Indeed/Greenhouse/Lever auto-apply), and `linkedin-discord-listener.service` (this #prompt CLI daemon). "
            "Note on LinkedIn: LinkedIn's edge firewall returns HTTP 403 on Oracle Cloud datacenter IPs (`130.210.108.55`), so LinkedIn browser clicks execute over Vishal's residential IP while controlled from HERE via `src.services.linkedin_control` (`/home/opc/.linkedin_control.json`). "
            f"To stop, pause, start, or check LinkedIn automation from this VM, run `{py_exec} -m src.services.linkedin_control stop` or `start` or `status`!"
        )
        system_context = (
            "You are Vishal Gupta's Autonomous CLI Agent running 24/7 in Discord #prompt with zero laptop dependency.\n"
            f"Execution Host: {host_desc}\n"
            "STRICT CLI EXECUTION & HONESTY RULES:\n"
            f"1) Always use `execute_shell_command` (using `{py_exec}` for Python scripts/one-liners and `sqlite3 {db_loc}` for DB checks) whenever the user asks to send an email, check data/logs/processes, edit config, or start/stop/restart workers.\n"
            "2) NEVER hallucinate or roleplay fake logs! NEVER claim an email was sent, a job was applied to, or a daemon started unless you actually ran `execute_shell_command` and verified the real stdout/stderr.\n"
            "3) NEVER mention past employer names under any circumstances.\n"
            "4) Keep responses concise, factual, and formatted in clean Markdown."
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

        models_to_try = ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
        active_model_idx = 0

        try:
            for _ in range(8):
                model_name = models_to_try[active_model_idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "systemInstruction": {"parts": [{"text": system_context}]},
                    "contents": contents,
                    "tools": tools_spec,
                    "generationConfig": {"maxOutputTokens": 1200, "temperature": 0.2}
                }
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
                if not fn_calls:
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
            return "⚠️ Completed 8 tool steps; run `status` or `!sh ps aux` to inspect state."
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

    def process_command(self, content: str, msg_id: str, author_id: str) -> None:
        """Parses and acts on instructions from Discord using the native Antigravity CLI."""
        text = content.strip()
        lower = text.lower()
        target_channel = self.channel_id

        print(f"\n[DiscordListener:Msg] Received from user: {text}")
        self.client.trigger_typing(target_channel)
        self.client.add_reaction(target_channel, msg_id, "⚙️")

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
        """Polls Discord prompt channel for new incoming user commands."""
        print(f"[DiscordListener] Active on prompt channel {self.channel_id}. Polling every {interval_seconds}s...")
        print(f"[DiscordListener] Engine: Native Antigravity CLI (`{AGY_BIN}`) with continuation `-c`")
        
        # Populate initial message IDs so old messages are not re-executed
        try:
            initial_msgs = self.client.get_channel_messages(self.channel_id, limit=25)
            for m in initial_msgs:
                self.processed_msg_ids.add(m["id"])
            print(f"[DiscordListener] Initialized with {len(self.processed_msg_ids)} existing messages.")
        except Exception as e:
            print(f"[DiscordListener] Notice fetching initial messages: {e}")

        while True:
            try:
                messages = self.client.get_channel_messages(self.channel_id, limit=10)
                for msg in reversed(messages):
                    msg_id = msg.get("id")
                    if msg_id in self.processed_msg_ids:
                        continue
                    self.processed_msg_ids.add(msg_id)

                    author = msg.get("author", {})
                    # Skip own bot messages
                    if author.get("bot"):
                        continue

                    content = msg.get("content", "").strip()
                    if content:
                        # Process in thread so polling doesn't block
                        t = threading.Thread(target=self.process_command, args=(content, msg_id, author.get("id")))
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
