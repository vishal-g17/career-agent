#!/usr/bin/env python3
"""
Lightweight 24/7 Career Autopilot Dashboard HTTP Server (~14 MB RAM).
Replaces Node Folio server on low-memory Cloud VMs, serving / and /p/dashboard
directly from SQLite (~/.linkedin_agent.db) with 60-second HTML caching.
"""

import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.dashboard_builder import build_dashboard_html

PORT = int(os.environ.get("FOLIO_PORT", "8082"))
ADDR = os.environ.get("FOLIO_ADDR", "127.0.0.1")

_CACHE = {"html": b"", "ts": 0.0}
CACHE_TTL_SECONDS = 45.0


def get_cached_html() -> bytes:
    now = time.time()
    if not _CACHE["html"] or (now - _CACHE["ts"]) > CACHE_TTL_SECONDS:
        html_str = build_dashboard_html()
        _CACHE["html"] = html_str.encode("utf-8")
        _CACHE["ts"] = now
    return _CACHE["html"]


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if path in ("/", "/p/dashboard", "/dashboard"):
            try:
                body = get_cached_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                msg = f"Dashboard error: {exc}".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg)
            return

        # Redirect any unknown path to /p/dashboard
        self.send_response(302)
        self.send_header("Location", "/p/dashboard")
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def main():
    print(f"[DashboardServer] Serving Career Autopilot Dashboard on http://{ADDR}:{PORT}/p/dashboard")
    server = HTTPServer((ADDR, PORT), DashboardHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
