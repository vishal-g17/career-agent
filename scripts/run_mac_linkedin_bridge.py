#!/usr/bin/env python3
"""
Local Mac Priority #0 Task Consumer (1.2-Second HTTPS Bridge from Cloud VM #prompt).
Polls Cloud VM's `/api/priority-task/claim` over HTTPS every 1.2s (0.08s latency, zero SSH overhead).
Executes ANY #prompt Mac task (`linkedin_msg`, `linkedin_apply`, `mac_sh`) IMMEDIATELY on Priority #0
and reports the verified result back synchronously to Cloud VM so #prompt replies with real proof!
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.priority_exec import execute_mac_task_locally
from src.engine.discord_client import DiscordClient
from src import config

SSH_KEY = os.path.expanduser("~/Downloads/agent-vm-key.key")
VM_HOST = "opc@130.210.108.55"


def get_bridge_key() -> str:
    tok = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not tok and (PROJECT_ROOT / ".env").exists():
        for ln in (PROJECT_ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("DISCORD_BOT_TOKEN="):
                tok = ln.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return hashlib.sha256((tok or "career-agent-priority").encode("utf-8")).hexdigest()[:24]


def discover_cloud_base_url() -> str:
    try:
        proc = subprocess.run(
            [
                "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                VM_HOST, "cat /home/opc/.cloudflared_dashboard.log 2>/dev/null || true",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", proc.stdout or "")
        if matches:
            return matches[-1].rstrip("/")
    except Exception:
        pass
    return os.environ.get("DASHBOARD_PUBLIC_URL", "https://villa-foods-mesa-ing.trycloudflare.com").rstrip("/")


def main():
    key = get_bridge_key()
    base_url = discover_cloud_base_url()
    discord = DiscordClient()
    default_ch = getattr(config, "DISCORD_PROMPT_CHANNEL_ID", "1570342549435187290")
    consec_errs = 0

    print(f"[MacPriorityBridge] Active — polling {base_url}/api/priority-task/claim every 1.2s...")

    while True:
        try:
            req = urllib.request.Request(
                f"{base_url}/api/priority-task/claim?key={key}",
                headers={"User-Agent": "CareerAgentMacPriority/1.0"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            consec_errs = 0

            task = data.get("task")
            if task:
                tid = task["id"]
                ttype = task["task_type"]
                payload = task["payload"]
                ch_id = task.get("channel_id") or default_ch
                t0 = time.time()
                print(f"[MacPriorityBridge] ⚡ Executing Priority #0 task #{tid} ({ttype}): {payload}")
                try:
                    result_msg = execute_mac_task_locally(ttype, payload)
                    status = "failed" if result_msg.startswith("❌") or result_msg.startswith("⚠️") else "done"
                except Exception as ex:
                    result_msg = f"❌ Priority Execution Error (`{ttype}`): `{ex}`"
                    status = "failed"

                elapsed = time.time() - t0
                body = json.dumps({"id": tid, "status": status, "result_msg": result_msg}).encode("utf-8")
                post_req = urllib.request.Request(
                    f"{base_url}/api/priority-task/complete?key={key}",
                    data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "CareerAgentMacPriority/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(post_req, timeout=8) as _:
                    pass

                # If synchronous waiter on Cloud VM already timed out (>42s), post late receipt directly to Discord
                if elapsed >= 41.0:
                    try:
                        discord.send_message(ch_id, result_msg)
                    except Exception:
                        pass
                print(f"[MacPriorityBridge] ✅ Finished task #{tid} in {elapsed:.1f}s: {result_msg[:120]}")
        except Exception as err:
            consec_errs += 1
            if consec_errs >= 5:
                new_url = discover_cloud_base_url()
                if new_url != base_url:
                    print(f"[MacPriorityBridge] Refreshed Cloud Tunnel URL -> {new_url}")
                    base_url = new_url
                consec_errs = 0
            time.sleep(2.0)

        time.sleep(1.2)


if __name__ == "__main__":
    main()
