"""Configuration management for douban2simkl."""

import os
from pathlib import Path
from dotenv import load_dotenv, set_key

# Load .env file from the current directory or workspace root
ENV_FILE = Path(".env")
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    load_dotenv()

# Default Simkl client ID for zero-friction PIN authentication
# Users can override this by setting SIMKL_CLIENT_ID in their .env
DEFAULT_SIMKL_CLIENT_ID = "c5b2c9d69e4a3b118029d20c58e72cbe9b76c8c50eef5f7ce3a22839b2512f46"

SIMKL_CLIENT_ID = os.getenv("SIMKL_CLIENT_ID", DEFAULT_SIMKL_CLIENT_ID)
SIMKL_CLIENT_SECRET = os.getenv("SIMKL_CLIENT_SECRET", "")
SIMKL_ACCESS_TOKEN = os.getenv("SIMKL_ACCESS_TOKEN", "")

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "")
DOUBAN_COOKIE = os.getenv("DOUBAN_COOKIE", "")


def save_simkl_token(token: str) -> None:
    """Save the authenticated Simkl access token to .env for persistence."""
    global SIMKL_ACCESS_TOKEN
    SIMKL_ACCESS_TOKEN = token
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    set_key(str(ENV_FILE), "SIMKL_ACCESS_TOKEN", token)
