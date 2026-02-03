from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("BCD_DATA_DIR", ROOT_DIR / "data")).resolve()
KEYS_DIR = Path(os.environ.get("BCD_KEYS_DIR", DATA_DIR / "keys")).resolve()
SERVERS_FILE = Path(os.environ.get("BCD_SERVERS_FILE", DATA_DIR / "servers.json")).resolve()
MAX_WORKERS = int(os.environ.get("BCD_MAX_WORKERS", "8"))
SSH_TIMEOUT = float(os.environ.get("BCD_SSH_TIMEOUT", "30"))
SSH_COMMAND_TIMEOUT = float(os.environ.get("BCD_SSH_COMMAND_TIMEOUT", "30"))
SSH_HEALTHCHECK_INTERVAL = float(os.environ.get("BCD_SSH_HEALTHCHECK_INTERVAL", "10"))


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
