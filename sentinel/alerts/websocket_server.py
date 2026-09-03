"""
sentinel/alerts/websocket_server.py
======================================

Production FastAPI application exposing Sentinel's read APIs, ingest
endpoint, and real-time alert broadcast websocket.

Endpoints:
- GET  /cameras                     -> list of active cameras
- GET  /alerts                      -> most recent alerts
- GET  /vehicle/{plate_text}/history -> vehicle track history for a plate
- POST /ingest                      -> accepts a PipelineResult-shaped
                                        JSON payload, runs it through the
                                        AlertEngine, and broadcasts any
                                        generated alert to websocket
                                        clients
- WS   /ws                          -> real-time alert broadcast channel

Application state (Database, WatchlistMatcher, AlertEngine, and the
websocket ConnectionManager) is created during the FastAPI lifespan
startup hook and torn down on shutdown, so a single process-wide
connection pool is shared across all requests.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from sentinel.correlation.db import Database
from sentinel.correlation.matcher import WatchlistMatcher
from sentinel.alerts.alert_engine import AlertEngine
from sentinel.detection.pipeline import PipelineResult

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.alerts.websocket_server")


class IngestPayload(BaseModel):
    """
    JSON request body accepted by POST /ingest, structurally mirroring
    sentinel.detection.pipeline.PipelineResult.
    """

    track_id: int
    class_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: Tuple[int, int, int, int]
    pts_ms: float
    camera_id: str
    plate_text: Optional[str] = None

    def to_pipeline_result(self) -> PipelineResult:
        return PipelineResult(
            track_id=self.track_id,
            class_name=self.class_name,
            confidence=self.confidence,
            bbox=self.bbox,
            pts_ms=self.pts_ms,
            camera_id=self.camera_id,
            plate_text=self.plate_text,
        )


class ConnectionManager:
    """
    Tracks connected websocket clients and broadcasts JSON messages to
    all of them, removing any connection that fails to receive a
    message (indicating it has disconnected).

    Access to the underlying client set is serialized via an
    asyncio.Lock so that concurrent connect/disconnect/broadcast calls
    from different request handlers never race on the shared set.
    """

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(
            "WebSocket client connected. Active clients: %d", len(self._connections)
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(
            "WebSocket client disconnected. Active clients: %d", len(self._connections)
        )

    async def broadcast(self, message: Dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._connections)

        stale: List[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                logger.debug(
                    "Failed to send to a websocket client; marking stale.",
                    exc_info=True,
                )
                stale.append(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    self._connections.discard(ws)
            logger.info(
                "Pruned %d stale websocket client(s). Active clients: %d",
                len(stale),
                len(self._connections),
            )


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    dsn = os.environ.get("DB_URL")
    if not dsn:
        raise RuntimeError(
            "DB_URL environment variable must be set to start the "
            "Sentinel alerts websocket server."
        )

    db = Database(dsn=dsn)
    await db.connect()

    matcher = WatchlistMatcher(db=db)
    alert_engine = AlertEngine(db=db, matcher=matcher)

    app.state.db = db
    app.state.matcher = matcher
    app.state.alert_engine = alert_engine

    logger.info("Sentinel alerts websocket server started.")
    try:
        yield
    finally:
        await db.close()
        logger.info("Sentinel alerts websocket server shut down.")


app = FastAPI(title="Sentinel Alerts API", lifespan=lifespan)


@app.get("/cameras")
async def list_cameras() -> List[Dict[str, Any]]:
    """
    Returns all active cameras registered with the platform.
    """
    db: Database = app.state.db
    try:
        cameras = await db.get_cameras()
    except Exception:
        logger.error("Failed to fetch cameras.", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch cameras.")
    return cameras


@app.get("/alerts")
async def list_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Returns the most recent alerts, most recent first.
    """
    if limit <= 0 or limit > 500:
        raise HTTPException(
            status_code=400, detail="limit must be between 1 and 500."
        )

    db: Database = app.state.db
    try:
        alerts = await db.get_alerts_recent(limit=limit)
    except Exception:
        logger.error("Failed to fetch recent alerts.", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch alerts.")
    return alerts


@app.get("/vehicle/{plate_text}/history")
async def vehicle_history(plate_text: str) -> List[Dict[str, Any]]:
    """
    Returns the track history for a given (already normalized) plate
    text.
    """
    db: Database = app.state.db
    normalized = "".join(ch for ch in plate_text.upper() if ch.isalnum())
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid plate_text.")

    try:
        history = await db.get_vehicle_history(plate_text=normalized)
    except Exception:
        logger.error(
            "Failed to fetch vehicle history for plate=%s", normalized, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to fetch vehicle history.")
    return history


@app.post("/ingest")
async def ingest(payload: IngestPayload) -> Dict[str, Any]:
    """
    Accepts a single PipelineResult-shaped JSON payload (typically
    emitted by an edge inference process running DetectionPipeline),
    runs it through the AlertEngine, and broadcasts any generated
    alert to all connected websocket clients.
    """
    alert_engine: AlertEngine = app.state.alert_engine
    result = payload.to_pipeline_result()

    try:
        alert = await alert_engine.process(result)
    except Exception:
        logger.error(
            "AlertEngine.process() failed for camera=%s track_id=%s",
            result.camera_id,
            result.track_id,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to process detection.")

    if alert is not None:
        await manager.broadcast(asdict(alert))
        return {"status": "alert_generated", "alert": asdict(alert)}

    return {"status": "no_match"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Real-time alert broadcast channel. Clients connect and receive a
    JSON message for every alert generated via POST /ingest. Incoming
    client messages are not required for operation but are consumed
    (and discarded) so that client-initiated pings/keepalives don't
    accumulate as unread frames.
    """
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug(
            "WebSocket connection terminated unexpectedly.", exc_info=True
        )
    finally:
        await manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("INFERENCE_HOST", "0.0.0.0")
    port = int(os.environ.get("INFERENCE_PORT", "8000"))
    uvicorn.run(
        "sentinel.alerts.websocket_server:app",
        host=host,
        port=port,
        reload=False,
    )
