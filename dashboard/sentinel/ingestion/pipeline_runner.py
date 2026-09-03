"""
sentinel/ingestion/pipeline_runner.py
========================================

Async multi-camera ingestion runner tying together stream decoding,
the detection pipeline, and the alert engine for one or more cameras
concurrently.

Design notes:
- StreamReader.frames() is a synchronous generator backed by a
  blocking OpenCV VideoCapture. Each next() call is dispatched via
  asyncio.to_thread so that a slow or stalled decode on one camera
  never blocks the event loop serving other cameras or the FastAPI
  server running alongside it.
- StreamReader already performs its own internal reconnect/backoff on
  read failure (see sentinel/ingestion/stream_reader.py). run_camera
  adds an outer reconnect loop as a second line of defense: if the
  frame generator itself terminates or raises unexpectedly, the reader
  is fully torn down and reopened from scratch after an exponential
  backoff, rather than letting the worker task die silently.
- Object detection/tracking is comparatively expensive, so only every
  Nth frame (frame_skip) is run through the DetectionPipeline; all
  other frames are decoded (to keep PTS/discontinuity tracking
  accurate and to drain the decoder buffer) but skipped for inference.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

from sentinel.ingestion.stream_reader import StreamReader
from sentinel.ingestion.camera_manager import Camera, CameraManager
from sentinel.detection.detector import Detector
from sentinel.detection.tracker import Tracker
from sentinel.detection.ocr import PlateOCR
from sentinel.detection.pipeline import DetectionPipeline, PipelineResult
from sentinel.correlation.db import Database
from sentinel.correlation.matcher import WatchlistMatcher
from sentinel.alerts.alert_engine import AlertEngine, AlertPayload

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[AlertPayload], Awaitable[None]]

RECONNECT_INITIAL_BACKOFF_SECONDS = 2.0
RECONNECT_MAX_BACKOFF_SECONDS = 30.0
RECONNECT_BACKOFF_MULTIPLIER = 2.0


async def run_camera(
    camera: Camera,
    pipeline: DetectionPipeline,
    alert_engine: AlertEngine,
    ws_server_broadcast_fn: BroadcastFn,
    frame_skip: int = 3,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Runs the ingestion + detection + alerting loop for a single camera
    until stop_event is set.

    Parameters
    ----------
    camera : Camera
        The camera to ingest from.
    pipeline : DetectionPipeline
        Pre-constructed detection pipeline bound to this camera's id.
    alert_engine : AlertEngine
        Shared alert engine used to persist detections/tracks and run
        watchlist correlation.
    ws_server_broadcast_fn : Callable[[AlertPayload], Awaitable[None]]
        Async callable invoked with any AlertPayload produced by
        alert_engine.process(), typically wired to the websocket
        connection manager's broadcast() method.
    frame_skip : int
        Only every Nth decoded frame is run through the detection
        pipeline; all frames are still decoded to keep PTS tracking
        and discontinuity detection accurate.
    stop_event : asyncio.Event | None
        Cooperative shutdown signal. When set, the worker tears down
        its StreamReader and returns. If not supplied, a fresh Event
        is created internally (the worker will then only stop on an
        unrecoverable error).
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    if frame_skip < 1:
        frame_skip = 1

    logger.info(
        "Starting ingestion worker for camera=%s (%s)", camera.id, camera.rtsp_url
    )

    backoff = RECONNECT_INITIAL_BACKOFF_SECONDS

    while not stop_event.is_set():
        reader = StreamReader(camera_id=camera.id, url=camera.rtsp_url)
        frame_generator = reader.frames()
        last_pts: float = 0.0
        frame_index = 0
        worker_healthy = True

        try:
            while not stop_event.is_set():
                try:
                    frame, pts_ms = await asyncio.to_thread(next, frame_generator)
                except StopIteration:
                    logger.warning(
                        "Frame generator for camera=%s terminated unexpectedly.",
                        camera.id,
                    )
                    worker_healthy = False
                    break
                except Exception:
                    logger.error(
                        "Unrecoverable error while decoding frame for camera=%s.",
                        camera.id,
                        exc_info=True,
                    )
                    worker_healthy = False
                    break

                if last_pts > 0 and pts_ms < last_pts:
                    logger.warning(
                        "Pipeline-level discontinuity for camera=%s "
                        "(%.2fms -> %.2fms). Resetting pipeline state.",
                        camera.id,
                        last_pts,
                        pts_ms,
                    )
                    pipeline.handle_discontinuity()
                last_pts = pts_ms

                frame_index += 1
                if frame_index % frame_skip != 0:
                    continue

                try:
                    results: List[PipelineResult] = await asyncio.to_thread(
                        pipeline.process_frame, frame, pts_ms
                    )
                except Exception:
                    logger.error(
                        "Detection pipeline failed for camera=%s pts_ms=%.2f",
                        camera.id,
                        pts_ms,
                        exc_info=True,
                    )
                    continue

                for result in results:
                    try:
                        alert = await alert_engine.process(result)
                    except Exception:
                        logger.error(
                            "AlertEngine.process failed for camera=%s track_id=%s",
                            camera.id,
                            result.track_id,
                            exc_info=True,
                        )
                        continue

                    if alert is not None:
                        try:
                            await ws_server_broadcast_fn(alert)
                        except Exception:
                            logger.error(
                                "Failed to broadcast alert id=%s for camera=%s",
                                alert.alert_id,
                                camera.id,
                                exc_info=True,
                            )

                # Successful frame processed end-to-end: reset backoff.
                backoff = RECONNECT_INITIAL_BACKOFF_SECONDS

        except Exception:
            logger.error(
                "Unexpected error in ingestion loop for camera=%s.",
                camera.id,
                exc_info=True,
            )
            worker_healthy = False
        finally:
            reader.close()

        if stop_event.is_set():
            break

        if worker_healthy:
            # Inner loop exited cleanly (e.g. stop_event flipped mid-check);
            # nothing further to reconnect for.
            break

        logger.warning(
            "Ingestion worker for camera=%s disconnected. Reconnecting in %.1fs.",
            camera.id,
            backoff,
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * RECONNECT_BACKOFF_MULTIPLIER, RECONNECT_MAX_BACKOFF_SECONDS)

    logger.info("Ingestion worker for camera=%s stopped.", camera.id)


async def run_all_cameras(
    camera_ids: Optional[List[str]],
    db: Database,
    ws_broadcast_fn: BroadcastFn,
    stop_event: asyncio.Event,
    camera_manager: Optional[CameraManager] = None,
    frame_skip: int = 3,
) -> None:
    """
    Resolves the target camera fleet and spawns one run_camera worker
    per camera, running them concurrently until stop_event is set.

    Parameters
    ----------
    camera_ids : list[str] | None
        If provided, restricts ingestion to cameras whose id appears in
        this list. If None, all cameras resolved by camera_manager are
        used.
    db : Database
        Connected Database instance shared by all per-camera
        AlertEngine instances.
    ws_broadcast_fn : Callable[[AlertPayload], Awaitable[None]]
        Async callable used to broadcast generated alerts, shared
        across all camera workers.
    stop_event : asyncio.Event
        Cooperative shutdown signal shared by all camera workers.
    camera_manager : CameraManager | None
        Pre-constructed CameraManager to resolve the fleet from. If not
        supplied, a new default instance is constructed (falling back
        to the local demo fleet unless CAMERA_GRID_URL is configured in
        the environment).
    frame_skip : int
        Passed through to each run_camera worker.
    """
    manager = camera_manager or CameraManager()
    all_cameras = manager.get_cameras()

    if camera_ids:
        wanted = set(camera_ids)
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
        logger.error("No cameras available to run ingestion workers against.")
        return

    logger.info(
        "Starting ingestion workers for %d camera(s): %s",
        len(cameras),
        [c.id for c in cameras],
    )

    # Detector and PlateOCR wrap comparatively heavyweight models; a
    # single shared instance of each is reused across all camera
    # workers to bound memory/GPU usage. Tracker maintains per-stream
    # ByteTrack association state and therefore MUST be distinct per
    # camera, as must each camera's DetectionPipeline (which is bound
    # to a specific camera_id).
    detector = Detector(model_path="yolov8n.pt", device="cpu")
    ocr = PlateOCR(gpu=False)

    matcher = WatchlistMatcher(db=db)
    alert_engine = AlertEngine(db=db, matcher=matcher)

    tasks: List[asyncio.Task] = []
    for camera in cameras:
        tracker = Tracker(model_path="yolov8n.pt")
        pipeline = DetectionPipeline(
            camera_id=camera.id,
            detector=detector,
            tracker=tracker,
            ocr=ocr,
        )
        task = asyncio.create_task(
            run_camera(
                camera=camera,
                pipeline=pipeline,
                alert_engine=alert_engine,
                ws_server_broadcast_fn=ws_broadcast_fn,
                frame_skip=frame_skip,
                stop_event=stop_event,
            ),
            name=f"ingestion-{camera.id}",
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for camera, result in zip(cameras, results):
        if isinstance(result, Exception):
            logger.error(
                "Ingestion worker for camera=%s terminated with an exception.",
                camera.id,
                exc_info=result,
            )

    logger.info("All ingestion workers have stopped.")
