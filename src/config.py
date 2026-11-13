import os
from pathlib import Path

def load_env(env_path: str = None) -> None:
    if env_path is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
    else:
        env_path = Path(env_path)

    if not env_path.is_file():
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val

load_env()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_POSTS_CHANNEL_ID = os.environ.get("DISCORD_POSTS_CHANNEL_ID", "1569380078368989244")
DISCORD_JOBS_CHANNEL_ID = os.environ.get("DISCORD_JOBS_CHANNEL_ID", "1569246471251959838")
DISCORD_AGENT_CHANNEL_ID = os.environ.get("DISCORD_AGENT_CHANNEL_ID", "1569246471251959838")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "1569246471251959838")
DISCORD_COMMAND_CHANNEL_ID = os.environ.get("DISCORD_COMMAND_CHANNEL_ID", "1570342549435187290")
DISCORD_PROMPT_CHANNEL_ID = os.environ.get("DISCORD_PROMPT_CHANNEL_ID", "1570342549435187290")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")
DATABASE_PATH = os.environ.get("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "agent.db"))

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "vishal90301@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
COLLEGE_EMAIL_ADDRESS = os.environ.get("COLLEGE_EMAIL_ADDRESS", "vishal.gupta.ug22@nsut.ac.in")
COLLEGE_EMAIL_APP_PASSWORD = os.environ.get("COLLEGE_EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
