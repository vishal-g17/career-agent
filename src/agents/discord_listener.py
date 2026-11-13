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
        procs = {
            'LinkedIn Cycle': 'master_autonomous_cycle.py',
            'Portal ATS Worker': 'run_portal_worker.py',
            'Cold Email Worker': 'run_email_worker.py',
            'Discord Remote CLI': 'discord_listener.py',
            'Ridealong Browser': 'ridealong'
        }
        lines.append("**Processes:**")
        for label, pat in procs.items():
            running = any(pat in l for l in ps_res.splitlines())
            status_icon = "🟢 RUNNING" if running else "🔴 STOPPED"
            lines.append(f"• {label}: {status_icon}")

        try:
            conn = sqlite3.connect('/Users/admin/.linkedin_agent.db')
            c = conn.cursor()
            c.execute('SELECT status, count(*) FROM outreach_leads GROUP BY status')
            email_counts = dict(c.fetchall())
            lines.append("\n**Outreach Leads:**")
            lines.append(f"• Sent: {email_counts.get('sent', 0)} | Bounced: {email_counts.get('bounced', 0)} | Replies: {email_counts.get('replied', 0)}")

            c.execute('SELECT count(*) FROM linkedin_connections_outreach')
            li_sent = c.fetchone()[0]
            lines.append(f"\n**LinkedIn Outreach:**\n• Total 1st-Degree Sent: {li_sent}")

            c.execute('SELECT status, count(*) FROM jobs GROUP BY status')
            job_counts = dict(c.fetchall())
            lines.append(f"\n**Jobs Pipeline:**\n• Applied: {job_counts.get('applied', 0)} | Redirects Pending: {job_counts.get('external_redirect', 0)} | New: {job_counts.get('new', 0)}")
            conn.close()
        except Exception as e:
            lines.append(f"\nDB Error: {e}")

        return "\n".join(lines)

    def run_antigravity_cli_turn(self, prompt: str, target_channel: str, msg_id: str) -> str:
        """
        Executes the prompt through the Antigravity (AGY) CLI engine.
        Acts exact same to the CLI with full agentic capabilities:
        running bash, editing files, inspecting DB, checking processes, and continuing conversation.
        """
        print(f"[DiscordCLI:AGY] Dispatching turn to Antigravity CLI: {prompt}")

        # Continuous typing indicator while Antigravity thinks and acts
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

        cmd = [
            AGY_BIN,
            "-c",
            "-p",
            prompt,
            "--add-dir", WORKSPACE_DIR,
            "--dangerously-skip-permissions"
        ]

        try:
            res = subprocess.run(cmd, cwd=WORKSPACE_DIR, capture_output=True, text=True, timeout=180)
            stop_typing.set()
            stdout = res.stdout.strip()
            stderr = res.stderr.strip()

            if stdout:
                return stdout
            elif stderr:
                if "RESOURCE_EXHAUSTED" in stderr or "quota reached" in stderr:
                    fallback_status = self.get_system_status_text()
                    return (
                        f"⚠️ **Antigravity CLI Quota Reached** (Individual limit resets in ~25h).\n\n"
                        f"💡 *Direct terminal commands (`!sh <cmd>`) and status checks work 100% offline with zero quota!*\n\n"
                        f"{fallback_status}"
                    )
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
