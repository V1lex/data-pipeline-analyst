import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

TLC_JAR_PATH = Path(os.environ["TLC_JAR_PATH"]).resolve()
MODELS_DIR = PROJECT_ROOT / "models"
REACHABLE_CONFIGS_DIR = MODELS_DIR / "reachable"

MODEL_PATH = MODELS_DIR / "model.tla"
SAFETY_CONFIG_PATH = MODELS_DIR / "safety.cfg"

MODEL_NAME = "model"
