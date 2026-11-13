import sqlite3
import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime

from .. import config

DB_PATH = os.environ.get("DATABASE_PATH", str(config.DATABASE_PATH))

def get_connection() -> sqlite3.Connection:
    db_path = os.environ.get("DATABASE_PATH", DB_PATH)
    needs_init = not os.path.exists(db_path) or os.path.getsize(db_path) == 0
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")  # Write-Ahead Logging for high concurrency
    if needs_init:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
    return conn

def init_db(schema_path: Optional[str] = None):
    if schema_path is None:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with get_connection() as conn:
        conn.executescript(schema_sql)

def save_post(post_id: str, topic: str, hook_id: str, content: str, human_score: float, status: str = "draft") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO posts (id, topic, hook_id, content, human_score, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (post_id, topic, hook_id, content, human_score, status)
        )

def get_post(post_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None

def is_job_seen(dedup_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (dedup_id,)).fetchone()
        return row is not None

def save_job(dedup_id: str, job_id: str, title: str, company: str, location: str, posted_at: str, url: str, match_score: int, scorecard: Dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (id, job_id, title, company, location, posted_at, url, match_score, scorecard)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (dedup_id, job_id, title, company, location, posted_at, url, match_score, json.dumps(scorecard))
        )

def log_event(component: str, level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO system_logs (component, level, message, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (component, level, message, json.dumps(metadata or {}))
        )
