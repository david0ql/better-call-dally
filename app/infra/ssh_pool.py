from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import paramiko

from app.core.config import MAX_WORKERS, SSH_HEALTHCHECK_INTERVAL, SSH_TIMEOUT
from app.infra.ssh import build_error_stats, collect_stats, resolve_key_path
from app.servers.models import Server


class _Entry:
    def __init__(self) -> None:
        self.client: paramiko.SSHClient | None = None
        self.lock = threading.Lock()
        self.last_error: str | None = None


class SSHClientPool:
    _instance: "SSHClientPool | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._servers: dict[str, Server] = {}
        self._lock = threading.Lock()
        self._monitor_started = False

    @classmethod
    def get(cls) -> "SSHClientPool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = SSHClientPool()
            return cls._instance

    def warm_connections(self, servers: list[Server]) -> None:
        if not servers:
            return
        self.register_servers(servers)

        def _warm() -> None:
            workers = min(MAX_WORKERS, len(servers))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                list(executor.map(self.ensure_connected, servers))

        thread = threading.Thread(target=_warm, daemon=True)
        thread.start()
        self._start_monitor()

    def register_servers(self, servers: list[Server]) -> None:
        with self._lock:
            for server in servers:
                self._servers[server.id] = server

    def _start_monitor(self) -> None:
        with self._lock:
            if self._monitor_started:
                return
            self._monitor_started = True
        thread = threading.Thread(target=self._monitor_loop, daemon=True)
        thread.start()

    def _monitor_loop(self) -> None:
        while True:
            with self._lock:
                servers = list(self._servers.values())
            for server in servers:
                try:
                    self.ensure_connected(server)
                except Exception:
                    continue
            time.sleep(SSH_HEALTHCHECK_INTERVAL)

    def ensure_connected(self, server: Server) -> paramiko.SSHClient:
        entry = self._get_entry(server.id)
        with entry.lock:
            return self._ensure_connected_locked(server, entry)

    def collect(self, server: Server, *, detail: str = "full"):
        entry = self._get_entry(server.id)
        with entry.lock:
            try:
                client = self._ensure_connected_locked(server, entry)
                return collect_stats(client, server, detail=detail)
            except Exception as exc:
                entry.last_error = str(exc)
                if entry.client:
                    entry.client.close()
                    entry.client = None
                return build_error_stats(server, str(exc))

    def _get_entry(self, server_id: str) -> _Entry:
        with self._lock:
            entry = self._entries.get(server_id)
            if entry is None:
                entry = _Entry()
                self._entries[server_id] = entry
            return entry

    def _ensure_connected_locked(self, server: Server, entry: _Entry) -> paramiko.SSHClient:
        if entry.client and self._is_active(entry.client):
            return entry.client
        if entry.client:
            entry.client.close()
            entry.client = None
        client = self._connect(server)
        entry.client = client
        entry.last_error = None
        return client

    def _connect(self, server: Server) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        key_path = resolve_key_path(server)
        client.connect(
            hostname=server.host,
            port=server.port,
            username=server.user,
            password=server.password,
            key_filename=str(key_path) if key_path else None,
            allow_agent=False,
            look_for_keys=False,
            timeout=SSH_TIMEOUT,
        )
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        return client

    @staticmethod
    def _is_active(client: paramiko.SSHClient) -> bool:
        transport = client.get_transport()
        return transport is not None and transport.is_active()
