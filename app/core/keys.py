from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import ROOT_DIR


def ensure_watcher_keypair() -> None:
    key_dir = ROOT_DIR / "keys"
    private_key = key_dir / "watcher_ed25519"
    public_key = key_dir / "watcher_ed25519.pub"

    if public_key.exists() and private_key.exists():
        return

    key_dir.mkdir(parents=True, exist_ok=True)

    if private_key.exists() and not public_key.exists():
        cmd = [
            "ssh-keygen",
            "-y",
            "-f",
            str(private_key),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise RuntimeError(message or "Failed to derive watcher public key")
        public_key.write_text(result.stdout.strip() + "\n", encoding="utf-8")
        return

    cmd = [
        "ssh-keygen",
        "-t",
        "ed25519",
        "-f",
        str(private_key),
        "-N",
        "",
        "-C",
        "better-call-dally-watcher",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(message or "Failed to generate watcher keypair")
