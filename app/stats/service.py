from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.config import MAX_WORKERS
from app.infra.ssh_pool import SSHClientPool
from app.servers.service import ServerService
from app.stats.models import HostStats, StatsResponse


class StatsService:
    def __init__(self, server_service: ServerService | None = None) -> None:
        self._server_service = server_service or ServerService()
        self._pool = SSHClientPool.get()

    def collect(self, *, include_disabled: bool = False) -> StatsResponse:
        servers = self._server_service.list_servers()
        if not include_disabled:
            servers = [server for server in servers if server.enabled]
        if not servers:
            return StatsResponse(servers=[])

        workers = min(MAX_WORKERS, len(servers))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(self._pool.collect, servers))
        return StatsResponse(servers=results)
