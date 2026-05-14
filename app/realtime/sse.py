from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.servers.service import ServerService
from app.stats.service import StatsService

router = APIRouter(prefix="/sse", tags=["realtime"])
STATS_INTERVAL_S = 20.0


async def event_stream() -> AsyncGenerator[str, None]:
    stats_service = StatsService()
    server_service = ServerService()

    while True:
        try:
            servers = server_service.list_servers()
            if not servers:
                await asyncio.sleep(STATS_INTERVAL_S)
                continue

            results = await asyncio.to_thread(
                stats_service.collect_all,
                servers=servers,
                include_disabled=False,
            )

            payload = {
                "type": "stats:batch",
                "servers": [_build_server_payload(s) for s in results],
            }
            yield f"data: {json.dumps(payload)}\n\n"
        except Exception as exc:
            error_payload = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(error_payload)}\n\n"

        await asyncio.sleep(STATS_INTERVAL_S)


def _build_server_payload(stats) -> dict:
    disks_list = []
    for d in (stats.disks.disks or []):
        disks_list.append({
            "device": d.device,
            "mount": d.mount,
            "total_bytes": d.total_bytes,
            "used_bytes": d.used_bytes,
        })
    return {
        "server_id": stats.server_id,
        "server_name": stats.server_name,
        "host": stats.host,
        "error": stats.error,
        "cpu": {
            "cores": stats.cpu.cores,
            "usage_percent": stats.cpu.usage_percent,
        },
        "memory": {
            "total_bytes": stats.memory.total_bytes,
            "used_bytes": stats.memory.used_bytes,
        },
        "disks": {"disks": disks_list},
        "uptime": {
            "seconds": stats.uptime.seconds,
            "human": stats.uptime.human,
        },
        "pm2": {
            "processes": stats.pm2.processes,
            "total_memory_bytes": stats.pm2.total_memory_bytes,
        },
        "supervisor": {
            "total": stats.supervisor.total,
            "running": stats.supervisor.running,
        },
    }


@router.get("")
async def sse_endpoint(request: Request) -> StreamingResponse:
    async def generate() -> AsyncGenerator[str, None]:
        yield "event: connected\ndata: {\"status\":\"connected\"}\n\n"
        async for chunk in event_stream():
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )