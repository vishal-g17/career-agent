import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any

IST = timezone(timedelta(hours=5, minutes=30))
LOCAL_CONTROL_PATH = os.path.expanduser("~/.linkedin_control.json")
REMOTE_CONTROL_PATH = "/home/opc/.linkedin_control.json"
SSH_KEY_PATH = os.path.expanduser("~/Downloads/agent-vm-key.key")
VM_HOST = "opc@130.210.108.55"

_LAST_CLOUD_SYNC_TS = 0.0
_CACHED_STATE: Dict[str, Any] = {}


def _now_ist_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p IST")


def read_local_control() -> Dict[str, Any]:
    if os.path.exists(LOCAL_CONTROL_PATH):
        try:
            with open(LOCAL_CONTROL_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "linkedin_enabled": True,
        "reason": "active",
        "updated_by": "system",
        "updated_at": _now_ist_str(),
        "stage": "idle",
        "last_mac_heartbeat": "",
    }


def write_local_control(state: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(LOCAL_CONTROL_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass
    return state


def set_linkedin_enabled(enabled: bool, reason: str, updated_by: str = "cli") -> Dict[str, Any]:
    """Sets LinkedIn automation enabled/disabled state locally AND syncs to Cloud VM if on Mac."""
    state = read_local_control()
    state["linkedin_enabled"] = bool(enabled)
    state["reason"] = reason
    state["updated_by"] = updated_by
    state["updated_at"] = _now_ist_str()
    state["command_seq"] = int(state.get("command_seq", 0)) + 1
    write_local_control(state)

    if sys.platform == "darwin" and os.path.exists(SSH_KEY_PATH):
        try:
            payload = json.dumps(state)
            subprocess.run(
                [
                    "ssh", "-i", SSH_KEY_PATH,
                    "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=4",
                    VM_HOST,
                    f"cat > {REMOTE_CONTROL_PATH}",
                ],
                input=payload, text=True, capture_output=True, timeout=7,
            )
        except Exception:
            pass
    return state


def sync_control_with_cloud(stage_info: str = None, force: bool = False) -> Dict[str, Any]:
    """
    Called by Mac LinkedIn workers: pulls latest command_seq from Cloud VM (so Discord #prompt
    stop/start commands on the VM take effect within seconds on Mac) and pushes Mac heartbeat back.
    """
    global _LAST_CLOUD_SYNC_TS, _CACHED_STATE
    now = time.time()
    local_state = read_local_control()

    if sys.platform != "darwin" or not os.path.exists(SSH_KEY_PATH):
        return local_state

    if not force and _CACHED_STATE and (now - _LAST_CLOUD_SYNC_TS) < 12.0:
        return local_state

    _LAST_CLOUD_SYNC_TS = now
    try:
        remote_cmd = (
            f"cat {REMOTE_CONTROL_PATH} 2>/dev/null || echo '{{}}'"
        )
        proc = subprocess.run(
            [
                "ssh", "-i", SSH_KEY_PATH,
                "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=4",
                VM_HOST,
                remote_cmd,
            ],
            capture_output=True, text=True, timeout=6,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            remote_state = json.loads(proc.stdout.strip())
            r_seq = int(remote_state.get("command_seq", 0))
            l_seq = int(local_state.get("command_seq", 0))
            if r_seq > l_seq:
                # Cloud VM (#prompt) issued a newer start/stop command! Adopt it locally.
                local_state["linkedin_enabled"] = bool(remote_state.get("linkedin_enabled", True))
                local_state["reason"] = remote_state.get("reason", "")
                local_state["updated_by"] = remote_state.get("updated_by", "discord_cloud")
                local_state["updated_at"] = remote_state.get("updated_at", _now_ist_str())
                local_state["command_seq"] = r_seq

        if stage_info:
            local_state["stage"] = stage_info
        local_state["last_mac_heartbeat"] = _now_ist_str()
        write_local_control(local_state)
        _CACHED_STATE = dict(local_state)

        # Push updated heartbeat back to Cloud VM if stage_info provided
        if stage_info:
            subprocess.run(
                [
                    "ssh", "-i", SSH_KEY_PATH,
                    "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=4",
                    VM_HOST,
                    f"cat > {REMOTE_CONTROL_PATH}",
                ],
                input=json.dumps(local_state), text=True, capture_output=True, timeout=6,
            )
    except Exception:
        pass

    return local_state


def is_linkedin_logged_out(page) -> bool:
    """Detects if LinkedIn session is logged out, hit authwall, or requires login/checkpoint."""
    if page is None:
        return False
    try:
        url_l = (page.url or "").lower()
        if any(tok in url_l for tok in ["/login", "/uas/login", "authwall", "/checkpoint/", "session_redirect"]):
            return True
        if "linkedin.com" in url_l:
            login_input = page.locator('input[name="session_key"], input#username, form.login__form').first
            if login_input.count() > 0 and login_input.is_visible():
                return True
    except Exception:
        pass
    return False


if __name__ == "__main__":
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if action in ("stop", "pause", "off"):
        st = set_linkedin_enabled(False, reason="manual_stop", updated_by="cli")
        print(f"🛑 LinkedIn Automation STOPPED ({st['updated_at']}). Will remain paused until explicitly started.")
    elif action in ("start", "resume", "on"):
        st = set_linkedin_enabled(True, reason="manual_start", updated_by="cli")
        print(f"🟢 LinkedIn Automation STARTED ({st['updated_at']}).")
    else:
        st = sync_control_with_cloud(force=True) if sys.platform == "darwin" else read_local_control()
        print(json.dumps(st, indent=2))
