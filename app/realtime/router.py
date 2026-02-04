from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.realtime.hub import hub

router = APIRouter(prefix="/ws", tags=["realtime"])


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            await hub.handle_message(websocket, message)
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)
        raise
