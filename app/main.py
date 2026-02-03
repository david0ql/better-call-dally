from __future__ import annotations

from fastapi import FastAPI

from app.core.config import ensure_data_dir
from app.core.keys import ensure_watcher_keypair
from app.infra.ssh_pool import SSHClientPool
from app.servers.router import router as servers_router
from app.servers.service import ServerService
from app.stats.router import router as stats_router

app = FastAPI(title="Better Call Dally", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    ensure_data_dir()
    ensure_watcher_keypair()
    servers = ServerService().list_servers()
    SSHClientPool.get().warm_connections(servers)


app.include_router(servers_router)
app.include_router(stats_router)
