from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.secure_storage import decrypt, encrypt, is_encrypted


class ServerCreate(BaseModel):
    name: str | None = None
    host: str
    port: int = 22
    user: str = "root"
    password: str | None = None
    key_path: str | None = None
    pm2_user: str | None = None
    pm2_home: str | None = None
    disks_monitored: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class Server(BaseModel):
    id: str
    name: str | None = None
    host: str
    port: int
    user: str
    password: str | None = None
    key_path: str | None = None
    pm2_user: str | None = None
    pm2_home: str | None = None
    disks_monitored: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool

    def get_password(self) -> str | None:
        if not self.password:
            return None
        if is_encrypted(self.password):
            return decrypt(self.password)
        return self.password

    @staticmethod
    def from_create(payload: ServerCreate) -> "Server":
        encrypted_password = None
        if payload.password:
            encrypted_password = encrypt(payload.password)
        return Server(
            id=str(uuid4()),
            name=payload.name,
            host=payload.host,
            port=payload.port,
            user=payload.user,
            password=encrypted_password,
            key_path=payload.key_path,
            pm2_user=payload.pm2_user,
            pm2_home=payload.pm2_home,
            disks_monitored=payload.disks_monitored,
            tags=payload.tags,
            enabled=payload.enabled,
        )


class ServerPublic(BaseModel):
    id: str
    name: str | None = None
    host: str
    port: int
    user: str
    key_path: str | None = None
    pm2_user: str | None = None
    pm2_home: str | None = None
    disks_monitored: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool


class ServerListResponse(BaseModel):
    servers: list[ServerPublic]
