"""
Config loading for nano-mail-agent.

Everything here is read from environment variables (loaded from a local
.env file via python-dotenv). There are NO hardcoded secret fallbacks —
if a required value is missing, we raise a clear error instead of
silently defaulting to something that might point at the wrong place.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root, wherever the script is run from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

# Scopes needed for this phase. Read-only on purpose (see README) —
# we intentionally are NOT requesting write/modify scopes yet.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Path to the OAuth client secrets file downloaded from Google Cloud Console.
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials.json")

# Path where the user's OAuth token (access + refresh token) is cached
# after the first successful login. This file is gitignored.
TOKEN_PATH = os.getenv("TOKEN_PATH", "token.json")

# --- Phase 2: AI summarization ---

# No hardcoded fallback on purpose -- if this is missing, summarizer.py
# should fail loudly and early rather than silently misbehaving.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Model used for per-email summarization. Kept as a named constant (not
# scattered as a string literal) so it's a one-line change if we need to
# swap models later.
SUMMARIZER_MODEL = os.getenv("SUMMARIZER_MODEL", "claude-sonnet-4-6")

# --- Phase 3: agent core (tool use) ---

# Model used for the agentic tool-use loop. Separate constant from
# SUMMARIZER_MODEL (even though it defaults to the same model) so the
# two phases can be tuned/swapped independently later.
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")

# Hard ceiling on tool calls per email, so a confused/looping agent
# can't quietly burn tokens on one message.
AGENT_MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "5"))

# --- Phase 4: persistence (SQLite) ---

DB_PATH = os.getenv("DB_PATH", "nano_mail_agent.db")


def db_path() -> Path:
    return _resolve(DB_PATH)


def credentials_path() -> Path:
    return _resolve(CREDENTIALS_PATH)


def token_path() -> Path:
    return _resolve(TOKEN_PATH)


def _resolve(path_str: str) -> Path:
    """Resolve a config path relative to the project root if it isn't absolute."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    return _PROJECT_ROOT / p
