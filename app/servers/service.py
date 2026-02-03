from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.core.config import KEYS_DIR, ROOT_DIR, ensure_data_dir
from app.infra.ssh import provision_root_access
from app.infra.ssh_pool import SSHClientPool
from app.servers.models import Server, ServerCreate
from app.servers.repository import ServerRepository

DEFAULT_KEY_PATH = str(ROOT_DIR / "keys" / "watcher_ed25519")


class ServerService:
    def __init__(self, repo: ServerRepository | None = None) -> None:
        self._repo = repo or ServerRepository()

    def list_servers(self) -> list[Server]:
        return self._repo.list()

    def add_server(self, payload: ServerCreate) -> Server:
        if payload.key_path is None:
            payload = payload.model_copy(update={"key_path": DEFAULT_KEY_PATH})
        server = Server.from_create(payload)
        return self._repo.add(server)

    def add_server_form(self, payload: ServerCreate, key_file: UploadFile | None) -> Server:
        server = Server.from_create(payload)
        stored_path: Path | None = None
        if key_file is not None:
            ensure_data_dir()
            filename = Path(key_file.filename or "key.pem").name
            stored_name = f"{server.id}_{filename}"
            stored_path = KEYS_DIR / stored_name
            with stored_path.open("wb") as handle:
                handle.write(key_file.file.read())
            try:
                server.key_path = str(stored_path.relative_to(ROOT_DIR))
            except ValueError:
                server.key_path = str(stored_path)
        elif server.password:
            server.key_path = None
        elif server.key_path is None:
            server.key_path = DEFAULT_KEY_PATH
        public_key_path = ROOT_DIR / "keys" / "watcher_ed25519.pub"
        try:
            provision_root_access(server, public_key_path)
        except Exception:
            if stored_path and stored_path.exists():
                stored_path.unlink(missing_ok=True)
            raise
        saved = self._repo.add(server)
        SSHClientPool.get().warm_connections([saved])
        return saved
