import os
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
DEFAULT_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "30.0"))
MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
USE_MOCK_CLIENT = os.getenv("USE_MOCK_CLIENT", "false").lower() in ("true", "1", "yes")
