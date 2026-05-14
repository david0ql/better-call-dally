from __future__ import annotations

import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import ensure_data_dir
from app.core.keys import ensure_watcher_keypair
from app.core.migrations import migrate_servers
from app.infra.ssh_pool import SSHClientPool
from app.realtime.hub import hub
from app.realtime.router import router as realtime_router
from app.realtime.sse import router as sse_router
from app.reports.disk_report import get_report_service
from app.servers.router import router as servers_router
from app.servers.service import ServerService
from app.stats.router import router as stats_router

app = FastAPI(title="Better Call Dally", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    ensure_data_dir()
    migrate_servers()
    ensure_watcher_keypair()
    servers = ServerService().list_servers()
    SSHClientPool.get().warm_connections(servers)
    hub.start()
    report_service = get_report_service()
    report_service.start()
    asyncio.create_task(report_service.send_disk_report())


app.include_router(servers_router)
app.include_router(stats_router)
app.include_router(realtime_router)
app.include_router(sse_router)
