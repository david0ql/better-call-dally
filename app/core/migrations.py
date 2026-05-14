from __future__ import annotations

from app.core.secure_storage import decrypt, encrypt, is_encrypted
from app.servers.repository import ServerRepository


def migrate_servers() -> None:
    repo = ServerRepository()
    servers = repo.list()
    needs_save = False

    for server in servers:
        if server.password and not is_encrypted(server.password):
            try:
                decrypted = decrypt(server.password)
                server.password = encrypt(decrypted)
                needs_save = True
            except Exception:
                continue

    if needs_save:
        repo._save(servers)