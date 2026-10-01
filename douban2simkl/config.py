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

# Simkl client ID for PIN authentication
# Users can set SIMKL_CLIENT_ID in their .env or will be prompted interactively
DEFAULT_SIMKL_CLIENT_ID = ""

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


def save_simkl_client_id(client_id: str) -> None:
    """Save the Simkl client ID to .env for persistence."""
    global SIMKL_CLIENT_ID
    SIMKL_CLIENT_ID = client_id
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    set_key(str(ENV_FILE), "SIMKL_CLIENT_ID", client_id)
