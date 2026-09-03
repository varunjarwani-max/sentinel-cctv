"""
main.py
=======

Sentinel — Definitive Application Entry Point
================================================

This is the single, permanent production entry point for the Sentinel
platform. It supports three modes of operation, selected via CLI flags:

  1. Combined mode (default): runs the FastAPI/websocket API server and
     all multi-camera ingestion workers concurrently in one process.
  2. --server-only: runs only the FastAPI/websocket API server.
  3. --infer-only: runs only the multi-camera ingestion/inference
     workers, without the API server.

Before starting any workload, the process runs startup pre-flight
checks (database connectivity, primary camera stream reachability) and
exits with status 1 if a critical dependency is unavailable, rather
than starting into a broken state.

Usage:
    python main.py
    python main.py --cameras test1 test2
    python main.py --server-only --host 0.0.0.0 --port 8000
    python main.py --infer-only --cameras test1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from dataclasses import asdict
from typing import List, Optional

import asyncpg
import uvicorn
from dotenv import load_dotenv

from sentinel.ingestion.camera_manager import Camera, CameraManager
from sentinel.ingestion.stream_reader import StreamReader
from sentinel.ingestion.pipeline_runner import run_all_cameras
from sentinel.correlation.db import Database
from sentinel.correlation.matcher import WatchlistMatcher
from sentinel.alerts import websocket_server
from sentinel.alerts.alert_engine import AlertEngine, AlertPayload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.main")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parses CLI arguments for the Sentinel entry point.
    """
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Sentinel: real-time CCTV edge analytics platform "
        "(Gujarat Police Hackathon 2026).",
    )
    parser.add_argument(
        "--cameras",
        nargs="+",
        default=None,
        metavar="CAMERA_ID",
        help="Target camera IDs to run ingestion against "
        "(default: all cameras available from CameraManager).",
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Run only the FastAPI/websocket API server, without ingestion workers.",
    )
    parser.add_argument(
        "--infer-only",
        action="store_true",
        help="Run only the ingestion/inference workers, without the API server.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Server bind address (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server bind port (default: 8000).",
    )

    args = parser.parse_args(argv)

    if args.server_only and args.infer_only:
        parser.error("--server-only and --infer-only are mutually exclusive.")

    return args


async def check_database(dsn: str) -> bool:
    """
    Confirms PostgreSQL connectivity by executing `SELECT 1;` against
    the configured DSN using a short-lived, dedicated connection.
    """
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=5.0)
        try:
            result = await conn.fetchval("SELECT 1;")
            return result == 1
        finally:
            await conn.close()
    except Exception:
        logger.error("Database preflight check failed.", exc_info=True)
        return False


async def check_stream(camera: Camera, timeout_seconds: float = 8.0) -> bool:
    """
    Confirms RTSP stream connectivity for the given camera by
    attempting to read a single frame within timeout_seconds.
    """
    reader = StreamReader(camera_id=camera.id, url=camera.rtsp_url)
    frame_generator = reader.frames()

    try:
        _, pts_ms = await asyncio.wait_for(
            asyncio.to_thread(next, frame_generator), timeout=timeout_seconds
        )
        logger.info(
            "Stream preflight succeeded for camera=%s (first pts_ms=%.2f).",
            camera.id,
            pts_ms,
        )
        return True
    except Exception:
        logger.error(
            "Stream preflight check failed for camera=%s.", camera.id, exc_info=True
        )
        return False
    finally:
        reader.close()


def print_startup_banner(
    cameras: List[Camera],
    db_ok: bool,
    stream_ok: bool,
    host: str,
    port: int,
    server_enabled: bool,
    infer_enabled: bool,
) -> None:
    """
    Prints a formatted startup banner summarizing preflight results,
    active configuration, and available routes.
    """
    lines = [
        "=" * 79,
        "  SENTINEL -- Real-Time CCTV Edge Analytics Platform",
        "  Gujarat Police Hackathon 2026",
        "=" * 79,
        f"  Database connectivity : {'OK' if db_ok else 'FAILED'}",
        f"  Primary stream check  : {'OK' if stream_ok else ('SKIPPED' if not infer_enabled else 'FAILED')}",
        f"  API server            : "
        + (f"ENABLED (http://{host}:{port})" if server_enabled else "DISABLED"),
        f"  Ingestion workers     : {'ENABLED' if infer_enabled else 'DISABLED'}",
        f"  Active cameras ({len(cameras)}):",
    ]
    for camera in cameras:
        lines.append(f"    - {camera.id:<12} {camera.name:<28} {camera.rtsp_url}")

    if server_enabled:
        lines.append("  Routes:")
        lines.append(f"    GET    http://{host}:{port}/cameras")
        lines.append(f"    GET    http://{host}:{port}/alerts")
        lines.append(f"    GET    http://{host}:{port}/vehicle/{{plate_text}}/history")
        lines.append(f"    POST   http://{host}:{port}/ingest")
        lines.append(f"    WS     ws://{host}:{port}/ws")

    lines.append("=" * 79)
    print("\n".join(lines))
    sys.stdout.flush()


async def async_main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    run_server = not args.infer_only
    run_infer = not args.server_only

    dsn = os.environ.get("DB_URL")
    if not dsn:
        logger.error("DB_URL environment variable is not set. Aborting startup.")
        return 1

    logger.info("Running startup pre-flight checks...")
    db_ok = await check_database(dsn)
    if not db_ok:
        logger.error("Database preflight check failed. Aborting startup.")
        return 1

    camera_grid_url = os.environ.get("CAMERA_GRID_URL") or None
    camera_password = os.environ.get("CAMERA_PASSWORD") or None
    camera_manager = CameraManager(api_url=camera_grid_url, password=camera_password)

    all_cameras = camera_manager.get_cameras()
    if args.cameras:
        wanted = set(args.cameras)
        cameras = [c for c in all_cameras if c.id in wanted]
        missing = wanted - {c.id for c in cameras}
        if missing:
            logger.warning(
                "Requested camera ids not found in resolved fleet: %s",
                sorted(missing),
            )
    else:
        cameras = all_cameras

    if not cameras:
        logger.error("No cameras resolved for this run. Aborting startup.")
        return 1

    stream_ok = True
    if run_infer:
        primary_camera = cameras[0]
        stream_ok = await check_stream(primary_camera)
        if not stream_ok:
            logger.error(
                "Primary camera stream check failed for camera=%s. Aborting startup.",
                primary_camera.id,
            )
            return 1

    print_startup_banner(
        cameras=cameras,
        db_ok=db_ok,
        stream_ok=stream_ok,
        host=args.host,
        port=args.port,
        server_enabled=run_server,
        infer_enabled=run_infer,
    )

    db = Database(dsn=dsn)
    await db.connect()

    stop_event = asyncio.Event()
    tasks: List[asyncio.Task] = []
    server: Optional[uvicorn.Server] = None

    if run_server:
        matcher = WatchlistMatcher(db=db)
        alert_engine = AlertEngine(db=db, matcher=matcher)

        # The websocket_server module's own FastAPI lifespan handler
        # normally constructs these itself; since we already hold a
        # live Database instance here (shared with the ingestion
        # workers below), we wire it directly onto app.state and
        # disable the built-in lifespan to avoid a redundant pool.
        websocket_server.app.state.db = db
        websocket_server.app.state.matcher = matcher
        websocket_server.app.state.alert_engine = alert_engine

        config = uvicorn.Config(
            app=websocket_server.app,
            host=args.host,
            port=args.port,
            log_level="info",
            lifespan="off",
        )
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve(), name="uvicorn-server"))

    async def broadcast_alert(alert: AlertPayload) -> None:
        await websocket_server.manager.broadcast(asdict(alert))

    if run_infer:
        tasks.append(
            asyncio.create_task(
                run_all_cameras(
                    camera_ids=[c.id for c in cameras],
                    db=db,
                    ws_broadcast_fn=broadcast_alert,
                    stop_event=stop_event,
                    camera_manager=camera_manager,
                ),
                name="ingestion-runner",
            )
        )

    loop = asyncio.get_running_loop()
    shutdown_initiated = asyncio.Event()

    def handle_signal(sig_name: str) -> None:
        if shutdown_initiated.is_set():
            return
        shutdown_initiated.set()
        logger.warning("Received %s. Initiating graceful shutdown...", sig_name)
        stop_event.set()
        if server is not None:
            server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal, sig.name)
        except NotImplementedError:
            # add_signal_handler is unavailable on some platforms
            # (notably Windows); fall back to the standard signal
            # module handler registration in that case.
            signal.signal(
                sig,
                lambda signum, frame: handle_signal(signal.Signals(signum).name),
            )

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        stop_event.set()
        if server is not None:
            server.should_exit = True
        await db.close()
        logger.info("Sentinel shutdown complete.")

    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
