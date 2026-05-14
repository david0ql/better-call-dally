from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

KEY_SIZE = 32
NONCE_SIZE = 12
SALT_SIZE = 16
ITERATIONS = 480000


def _get_key_path() -> Path:
    from app.core.config import DATA_DIR
    return DATA_DIR / ".master.key"


def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=ITERATIONS,
        backend=default_backend(),
    )
    return kdf.derive(password)


def _get_encryption_key() -> bytes:
    key_path = _get_key_path()
    if key_path.exists():
        return key_path.read_bytes()

    password = os.urandom(KEY_SIZE)
    salt = os.urandom(SALT_SIZE)
    key = _derive_key(password, salt)

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)

    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    return key


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    key = _get_encryption_key()
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return (nonce + ciphertext).hex()


def decrypt(encrypted: str) -> str:
    if not encrypted:
        return ""
    key = _get_encryption_key()
    data = bytes.fromhex(encrypted)
    nonce = data[:NONCE_SIZE]
    ciphertext = data[NONCE_SIZE:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


def is_encrypted(value: str | None) -> bool:
    if not value:
        return False
    try:
        data = bytes.fromhex(value)
        return len(data) > NONCE_SIZE
    except Exception:
        return False