from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.servers.models import ServerCreate, ServerListResponse, ServerPublic
from app.servers.service import ServerService

router = APIRouter(prefix="/servers", tags=["servers"])
service = ServerService()


@router.get("", response_model=ServerListResponse)
def list_servers() -> ServerListResponse:
    servers = service.list_servers()
    public = [ServerPublic(**server.model_dump(exclude={"password"})) for server in servers]
    return ServerListResponse(servers=public)


@router.post("", response_model=ServerPublic)
def add_server(
    host: str = Form(...),
    name: str | None = Form(None),
    port: int = Form(22),
    user: str = Form("root"),
    password: str | None = Form(None),
    tags: str | None = Form(None),
    key_file: UploadFile | None = File(None),
) -> ServerPublic:
    tags_list = [item.strip() for item in tags.split(",")] if tags else []
    payload = ServerCreate(
        name=name,
        host=host,
        port=port,
        user=user,
        password=password,
        tags=[tag for tag in tags_list if tag],
        enabled=True,
    )
    try:
        server = service.add_server_form(payload, key_file)
        return ServerPublic(**server.model_dump(exclude={"password"}))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
