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

    def ask_gemini_agent(self, prompt: str) -> str:
        """Tool-enabled, memory-aware Gemini agent fallback when agy binary is not installed (e.g. on Linux VM)."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key and os.path.exists(str(PROJECT_ROOT / ".env")):
            for line in open(str(PROJECT_ROOT / ".env")):
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
        if not api_key:
            return "❌ Agent Error: No `GEMINI_API_KEY` configured in `.env`."

        is_mac = sys.platform == "darwin"
        host_desc = (
            "Local Mac (`/Users/admin/linkedin-multiagent`) connected to Ridealong (`127.0.0.1:9230`) + SSH access to OCI VM `opc@130.210.108.55` (`-i ~/Downloads/agent-vm-key.key`)."
            if is_mac else
            "Oracle Cloud VM (`130.210.108.55`, `/home/opc/career-agent`). Note: LinkedIn blocks OCI datacenter IPs with HTTP 403, so LinkedIn runs on Vishal's Mac Ridealong daemon while Portal ATS + Cold Email run 24/7 here on OCI."
        )
        system_context = (
            "You are Vishal Gupta's CLI-grade Career Automation Agent in Discord #prompt.\n"
            f"Current execution host: {host_desc}\n"
            "STRICT HONESTY & EXECUTION RULES:\n"
            "1) You have a real shell tool `execute_shell_command`. Use it to inspect processes (`ps aux`), check SQLite DB (`~/.linkedin_agent.db`), read logs, or start/stop scripts.\n"
            "2) NEVER hallucinate or roleplay fake execution logs! NEVER claim a message was sent, a job was applied to, or a worker started unless you actually ran `execute_shell_command` and verified the output.\n"
            "3) NEVER mention past employer names under any circumstances.\n"
            "4) Keep answers concise, factual, and CLI-accurate."
        )

        # Build real multi-turn chat history from recent Discord messages in #prompt
        contents: List[Dict[str, Any]] = []
        try:
            recent_msgs = self.client.get_channel_messages(self.channel_id, limit=8)
            for m in reversed(recent_msgs):
                c_txt = (m.get("content") or "").strip()
                if not c_txt or c_txt == prompt.strip():
                    continue
                is_bot = bool((m.get("author") or {}).get("bot"))
                role = "model" if is_bot else "user"
                if contents and contents[-1]["role"] == role:
                    contents[-1]["parts"][0]["text"] += "\n" + c_txt[:1000]
                else:
                    contents.append({"role": role, "parts": [{"text": c_txt[:1000]}]})
        except Exception:
            pass
        if contents and contents[0]["role"] == "model":
            contents = contents[1:]
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        tools_spec = [{
            "functionDeclarations": [{
                "name": "execute_shell_command",
                "description": "Execute a bash command in the career-agent workspace and return real stdout/stderr.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {"type": "STRING", "description": "Shell command to run"}
                    },
                    "required": ["command"]
                }
            }]
        }]

        model_name = "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

        for _ in range(4):
            payload = {
                "systemInstruction": {"parts": [{"text": system_context}]},
                "contents": contents,
                "tools": tools_spec,
                "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.2}
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                resp = urllib.request.urlopen(req, timeout=45)
                res_json = json.loads(resp.read().decode())
                cand_content = res_json["candidates"][0]["content"]
                parts = cand_content.get("parts", [])
                fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not fn_calls:
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    return "\n".join(text_parts).strip() or "✅ Done."

                contents.append(cand_content)
                fn_responses = []
                for fc in fn_calls:
                    cmd_str = (fc.get("args") or {}).get("command", "")
                    print(f"[DiscordCLI:GeminiTool] Running: {cmd_str}")
                    tool_out = self.execute_bash_direct(cmd_str)
                    fn_responses.append({
                        "functionResponse": {
                            "name": fc["name"],
                            "response": {"output": tool_out}
                        }
                    })
                contents.append({"role": "user", "parts": fn_responses})
            except Exception as e:
                return f"❌ AI Tool-Agent Error: `{e}`"

        return "⚠️ Completed tool steps; check `!sh ps aux` or `status` for latest state."

    def run_antigravity_cli_turn(self, prompt: str, target_channel: str, msg_id: str) -> str:
        """
        Executes the prompt through the Antigravity (AGY) CLI engine in an isolated persistent
        session directory (~/.discord_agy_workspace) so it never collides with open TUI sessions,
        or falls back to the tool-enabled Gemini agent when agy is unavailable.
        """
        if not shutil.which(AGY_BIN) and not os.path.exists(AGY_BIN):
            print(f"[DiscordCLI:AGY] AGY binary not found at '{AGY_BIN}'. Routing to tool-enabled Gemini agent...")
            return self.ask_gemini_agent(prompt)

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

        # 3. Emergency Safety Pause
        if any(w in lower for w in ["emergency stop", "kill all workers", "pause everything"]):
            subprocess.run("pkill -f 'master_autonomous_cycle'", shell=True)
            subprocess.run("pkill -f 'run_email_worker'", shell=True)
            subprocess.run("pkill -f 'run_portal_worker'", shell=True)
            self.client.send_message(
                target_channel,
                "🛑 **EMERGENCY STOP TRIGGERED:** All background automation processes stopped."
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
