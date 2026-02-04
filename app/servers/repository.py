from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.config import SERVERS_FILE, ensure_data_dir
from app.servers.models import Server


class ServerRepository:
    _lock = threading.Lock()

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or SERVERS_FILE

    def list(self) -> list[Server]:
        with self._lock:
            return self._load()

    def add(self, server: Server) -> Server:
        with self._lock:
            servers = self._load()
            for existing in servers:
                if (
                    existing.host == server.host
                    and existing.port == server.port
                    and existing.user == server.user
                ):
                    raise ValueError("Server already exists for host/port/user")
            servers.append(server)
            self._save(servers)
        return server

    def get_by_id(self, server_id: str) -> Server | None:
        with self._lock:
            servers = self._load()
        for server in servers:
            if server.id == server_id:
                return server
        return None

    def _load(self) -> list[Server]:
        if not self._path.exists():
            return []
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [Server(**item) for item in data]

    def _save(self, servers: list[Server]) -> None:
        ensure_data_dir()
        payload = [server.model_dump() for server in servers]
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)
