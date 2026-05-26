"""Browser WebSocket endpoint at /ws.

One connection per browser tab. The server pushes coalesced Kalshi events
in 500ms batches; the browser applies them to TanStack cache.

No auth — same model as the REST API: localhost binding is the
authentication. nginx proxies wss://lutz.bot/ws → 127.0.0.1:8000/ws with
Upgrade headers.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.websocket("/ws")
async def browser_ws(websocket: WebSocket) -> None:
    """Accept a browser client. Server-push only — we ignore inbound msgs."""
    manager = websocket.app.state.broadcast
    await manager.connect(websocket)
    try:
        # Keep the socket open by awaiting receive_text(). The browser
        # doesn't send anything meaningful; this loop just survives until
        # disconnect.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:  # noqa: BLE001
        log.warning("browser_ws_error", error=str(e))
        manager.disconnect(websocket)
