from __future__ import annotations

from pydantic import BaseModel


class MemoryInfo(BaseModel):
    total_bytes: int | None = None
    used_bytes: int | None = None
    total_human: str
    used_human: str


class UptimeInfo(BaseModel):
    seconds: float | None = None
    human: str


class DiskInfo(BaseModel):
    mount: str = "/"
    total_bytes: int | None = None
    used_bytes: int | None = None
    total_human: str
    used_human: str


class Pm2Process(BaseModel):
    id: int | None = None
    name: str | None = None
    namespace: str | None = None
    version: str | None = None
    mode: str | None = None
    pid: int | None = None
    uptime: int | None = None
    restarts: int | None = None
    status: str | None = None
    cpu: float | None = None
    memory_bytes: int | None = None
    user: str | None = None
    watching: bool | None = None


class Pm2Info(BaseModel):
    total_memory_bytes: int | None = None
    processes: int | None = None
    details: list[Pm2Process] | None = None
    error: str | None = None


class SupervisorProcess(BaseModel):
    name: str | None = None
    state: str | None = None
    pid: int | None = None
    uptime: str | None = None
    message: str | None = None
    raw: str | None = None


class SupervisorInfo(BaseModel):
    total: int | None = None
    running: int | None = None
    details: list[SupervisorProcess] | None = None


class HostStats(BaseModel):
    server_id: str
    server_name: str | None = None
    host: str
    user: str
    port: int
    tags: list[str]
    error: str | None = None
    memory: MemoryInfo
    uptime: UptimeInfo
    disk: DiskInfo
    pm2: Pm2Info
    supervisor: SupervisorInfo


class StatsResponse(BaseModel):
    servers: list[HostStats]
