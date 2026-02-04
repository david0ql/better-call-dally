from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import os

from fastapi import WebSocket

from app.servers.service import ServerService
from app.stats.service import StatsService

MIN_INTERVAL_S = 3.0
MAX_INTERVAL_S = 60.0
DEFAULT_INTERVAL_S = 10.0
PM2_DETAIL_LIMIT = int(os.environ.get("BCD_PM2_DETAIL_LIMIT", "8"))
SUP_DETAIL_LIMIT = int(os.environ.get("BCD_SUP_DETAIL_LIMIT", "5"))


@dataclass
class Subscription:
    interval_s: float
    detail: str


@dataclass
class CacheEntry:
    payload: dict
    fetched_at: float
    detail: str


class RealtimeHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: set[WebSocket] = set()
        self._server_subs: dict[str, dict[WebSocket, Subscription]] = {}
        self._cache: dict[str, CacheEntry] = {}
        self._in_flight: set[str] = set()
        self._task: asyncio.Task | None = None
        self._stats_service = StatsService()
        self._server_service = ServerService()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
            for subs in self._server_subs.values():
                subs.pop(websocket, None)
            self._server_subs = {k: v for k, v in self._server_subs.items() if v}

    async def handle_message(self, websocket: WebSocket, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        msg_type = payload.get("type")
        if msg_type == "list:subscribe":
            include_disabled = bool(payload.get("include_disabled", False))
            await self._send_list(websocket, include_disabled=include_disabled)
            return
        if msg_type == "server:subscribe":
            server_id = payload.get("server_id")
            if not server_id:
                return
            interval = self._normalize_interval(payload.get("interval_ms"))
            detail = self._normalize_detail(payload.get("detail"))
            await self._subscribe_server(websocket, server_id, interval, detail)
            return
        if msg_type == "server:unsubscribe":
            server_id = payload.get("server_id")
            if not server_id:
                return
            await self._unsubscribe_server(websocket, server_id)
            return

    async def _send_list(self, websocket: WebSocket, *, include_disabled: bool) -> None:
        servers = self._server_service.list_servers()
        if not include_disabled:
            servers = [server for server in servers if server.enabled]
        payload = {
            "type": "list:update",
            "servers": [
                {
                    "server_id": server.id,
                    "server_name": server.name or server.host,
                    "host": server.host,
                    "enabled": server.enabled,
                    "tags": server.tags,
                }
                for server in servers
            ],
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await websocket.send_json(payload)

    async def _subscribe_server(
        self,
        websocket: WebSocket,
        server_id: str,
        interval_s: float,
        detail: str,
    ) -> None:
        async with self._lock:
            subs = self._server_subs.setdefault(server_id, {})
            subs[websocket] = Subscription(interval_s=interval_s, detail=detail)
            cache = self._cache.get(server_id)
        if cache is not None and cache.detail == detail:
            await self._safe_send(websocket, cache.payload)

    async def _unsubscribe_server(self, websocket: WebSocket, server_id: str) -> None:
        async with self._lock:
            subs = self._server_subs.get(server_id)
            if subs is None:
                return
            subs.pop(websocket, None)
            if not subs:
                self._server_subs.pop(server_id, None)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            await self._tick()

    async def _tick(self) -> None:
        now = time.monotonic()
        async with self._lock:
            subs_snapshot = {
                server_id: list(subs.values())
                for server_id, subs in self._server_subs.items()
                if subs
            }

        for server_id, subs in subs_snapshot.items():
            interval_s = min(sub.interval_s for sub in subs) if subs else DEFAULT_INTERVAL_S
            detail = "full" if any(sub.detail == "full" for sub in subs) else "summary"
            cache = self._cache.get(server_id)
            due = cache is None or (now - cache.fetched_at) >= interval_s or cache.detail != detail

            if not due:
                continue

            async with self._lock:
                if server_id in self._in_flight:
                    continue
                self._in_flight.add(server_id)

            asyncio.create_task(self._fetch_and_broadcast(server_id, detail))

    async def _fetch_and_broadcast(self, server_id: str, detail: str) -> None:
        try:
            stats = await asyncio.to_thread(
                self._stats_service.collect_one,
                server_id,
                include_disabled=True,
                detail=detail,
            )
            if stats is None:
                payload = {
                    "type": "server:error",
                    "server_id": server_id,
                    "error": "server not found",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            else:
                if detail == "summary":
                    server_payload = self._build_summary(stats)
                else:
                    server_payload = self._build_full(stats)
                payload = {
                    "type": "server:update",
                    "server": server_payload,
                    "detail": detail,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                async with self._lock:
                    self._cache[server_id] = CacheEntry(
                        payload=payload,
                        fetched_at=time.monotonic(),
                        detail=detail,
                    )
            await self._broadcast(server_id, payload)
        finally:
            async with self._lock:
                self._in_flight.discard(server_id)

    async def _broadcast(self, server_id: str, payload: dict) -> None:
        async with self._lock:
            targets = list(self._server_subs.get(server_id, {}).keys())
        if not targets:
            return
        failures: list[WebSocket] = []
        for websocket in targets:
            ok = await self._safe_send(websocket, payload)
            if not ok:
                failures.append(websocket)
        for websocket in failures:
            await self.disconnect(websocket)

    async def _safe_send(self, websocket: WebSocket, payload: dict) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_detail(detail: object) -> str:
        if isinstance(detail, str) and detail.lower() == "full":
            return "full"
        return "summary"

    @staticmethod
    def _normalize_interval(interval_ms: object) -> float:
        if isinstance(interval_ms, (int, float)):
            value = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, interval_ms / 1000.0))
            return value
        return DEFAULT_INTERVAL_S

    def _build_summary(self, stats) -> dict:
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
            "disk": {
                "total_bytes": stats.disk.total_bytes,
                "used_bytes": stats.disk.used_bytes,
            },
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

    def _build_full(self, stats) -> dict:
        pm2_details = stats.pm2.details or []
        sup_details = stats.supervisor.details or []
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
            "disk": {
                "total_bytes": stats.disk.total_bytes,
                "used_bytes": stats.disk.used_bytes,
            },
            "uptime": {
                "seconds": stats.uptime.seconds,
                "human": stats.uptime.human,
            },
            "pm2": {
                "processes": stats.pm2.processes,
                "total_memory_bytes": stats.pm2.total_memory_bytes,
                "details": [
                    {"name": item.name, "status": item.status}
                    for item in pm2_details[:PM2_DETAIL_LIMIT]
                    if item is not None
                ],
            },
            "supervisor": {
                "total": stats.supervisor.total,
                "running": stats.supervisor.running,
                "details": [
                    {
                        "name": item.name,
                        "state": item.state,
                        "uptime": item.uptime,
                    }
                    for item in sup_details[:SUP_DETAIL_LIMIT]
                    if item is not None
                ],
            },
        }


hub = RealtimeHub()
