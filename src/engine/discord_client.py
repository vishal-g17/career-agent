import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List
from .. import config

DISCORD_API_BASE = "https://discord.com/api/v10"

class DiscordClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or config.DISCORD_BOT_TOKEN
        if not self.token:
            raise ValueError("DISCORD_BOT_TOKEN is not configured.")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "LinkedInAgent (https://github.com, v1.0)"
        }

    def _request(self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{DISCORD_API_BASE}{endpoint}"
        payload_bytes = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=payload_bytes, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Discord API Error {e.code} on {method} {endpoint}: {err_body}")

    def get_me(self) -> Dict[str, Any]:
        return self._request("GET", "/users/@me")

    def get_guilds(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/users/@me/guilds")

    def send_message(self, channel_id: str, content: str = "", embeds: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds
        return self._request("POST", f"/channels/{channel_id}/messages", payload)

    def create_thread(self, channel_id: str, message_id: str, name: str, auto_archive_duration: int = 1440) -> Dict[str, Any]:
        payload = {
            "name": name[:100],
            "auto_archive_duration": auto_archive_duration
        }
        return self._request("POST", f"/channels/{channel_id}/messages/{message_id}/threads", payload)

    def send_thread_message(self, thread_id: str, content: str = "", embeds: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        return self.send_message(thread_id, content, embeds)

    def get_channel_messages(self, channel_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self._request("GET", f"/channels/{channel_id}/messages?limit={limit}")

    def trigger_typing(self, channel_id: str) -> Dict[str, Any]:
        """Shows the bot is typing in the channel."""
        try:
            return self._request("POST", f"/channels/{channel_id}/typing")
        except Exception:
            return {}

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Adds an emoji reaction to a message."""
        import urllib.parse
        try:
            encoded_emoji = urllib.parse.quote(emoji)
            self._request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me")
        except Exception as e:
            print(f"[DiscordClient] Add reaction notice: {e}")
