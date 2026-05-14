from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = Path(os.getenv("BCD_DATA_DIR", str(ROOT_DIR / "data"))).resolve()
KEYS_DIR = Path(os.getenv("BCD_KEYS_DIR", str(DATA_DIR / "keys"))).resolve()
SERVERS_FILE = Path(os.getenv("BCD_SERVERS_FILE", str(DATA_DIR / "servers.json"))).resolve()
MAX_WORKERS = int(os.getenv("BCD_MAX_WORKERS", "8"))
SSH_TIMEOUT = float(os.getenv("BCD_SSH_TIMEOUT", "30"))
SSH_COMMAND_TIMEOUT = float(os.getenv("BCD_SSH_COMMAND_TIMEOUT", "30"))
SSH_HEALTHCHECK_INTERVAL = float(os.getenv("BCD_SSH_HEALTHCHECK_INTERVAL", "10"))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("BCD_CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
EMAIL_API_URL = os.getenv("BCD_EMAIL_API_URL", "")
EMAIL_ACCESS_TOKEN = os.getenv("BCD_EMAIL_ACCESS_TOKEN", "")
EMAIL_TO = os.getenv("BCD_EMAIL_TO", "")
EMAIL_ENABLED = os.getenv("BCD_EMAIL_ENABLED", "false").lower() == "true"
REPORT_HOUR = int(os.getenv("BCD_REPORT_HOUR", "15"))
REPORT_MINUTE = int(os.getenv("BCD_REPORT_MINUTE", "0"))


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    KEYS_DIR.mkdir(parents=True, exist_ok=True)