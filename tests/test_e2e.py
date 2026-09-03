"""
tests/test_e2e.py
====================

Self-contained end-to-end integration test for the Sentinel ingestion,
detection, correlation, and alerting pipeline.

Prerequisites:
  - A reachable PostgreSQL instance, configured via the DB_URL
    environment variable (see .env.example).
  - A local RTSP test stream at rtsp://localhost:8554/stream/test1,
    provided by scripts/fake_rtsp_setup.sh.

This suite:
  1. Ensures the correlation schema exists on the target database
     (schema.sql is idempotent: IF NOT EXISTS / ON CONFLICT DO NOTHING
     throughout).
  2. Ensures a cameras row exists for the test camera so foreign keys
     resolve correctly.
  3. Inserts a mock STOLEN watchlist entry for plate GJ01TEST99.
  4. Runs run_camera() against the local test stream for 30 seconds.
  5. Asserts that detections were logged, a vehicle_tracks row was
     upserted, and at least one alert was recorded.
  6. Cleans up all records created during the run and prints a summary
     to stdout.

Usage:
    ./scripts/fake_rtsp_setup.sh
    python -m tests.test_e2e

Also runnable under pytest via the `test_end_to_end` function.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

import asyncpg
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sentinel.ingestion.camera_manager import Camera
from sentinel.ingestion.pipeline_runner import run_camera
from sentinel.detection.detector import Detector
from sentinel.detection.tracker import Tracker
from sentinel.detection.ocr import PlateOCR
from sentinel.detection.pipeline import DetectionPipeline
from sentinel.correlation.db import Database
from sentinel.correlation.matcher import WatchlistMatcher
from sentinel.alerts.alert_engine import AlertEngine, AlertPayload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.tests.e2e")

TEST_CAMERA_ID = "test1"
TEST_RTSP_URL = "rtsp://localhost:8554/stream/test1"
TEST_PLATE = "GJ01TEST99"
TEST_RUN_DURATION_SECONDS = 30.0

SCHEMA_PATH = _REPO_ROOT / "sentinel" / "correlation" / "schema.sql"


async def ensure_schema(dsn: str) -> None:
    """
    Ensures the correlation schema exists by executing schema.sql
    against the target database. Safe to run repeatedly since the DDL
    uses IF NOT EXISTS / ON CONFLICT DO NOTHING throughout.
    """
    conn = await asyncpg.connect(dsn=dsn, timeout=10.0)
    try:
        sql = SCHEMA_PATH.read_text()
        await conn.execute(sql)
    finally:
        await conn.close()


async def ensure_test_camera(dsn: str) -> None:
    """
    Ensures a cameras row exists for TEST_CAMERA_ID so that the
    detections/alerts foreign keys used during this test resolve
    correctly.
    """
    conn = await asyncpg.connect(dsn=dsn, timeout=10.0)
    try:
        await conn.execute(
            """
            INSERT INTO cameras (id, name, department, lat, lng, hls_url, rtsp_url, active)
            VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
            ON CONFLICT (id) DO NOTHING
            """,
            TEST_CAMERA_ID,
            "E2E Test Camera",
            "QA",
            23.0225,
            72.5714,
            "https://cctv.corp8.cloud/test1/index.m3u8",
            TEST_RTSP_URL,
        )
    finally:
        await conn.close()


async def insert_test_watchlist_entry(dsn: str) -> str:
    """
    Inserts a mock STOLEN vehicle watchlist entry for TEST_PLATE and
    returns its generated UUID (as a string).
    """
    conn = await asyncpg.connect(dsn=dsn, timeout=10.0)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO watchlist (entity_type, plate_text, flag_type, description, added_by)
            VALUES ('VEHICLE', $1, 'STOLEN', 'E2E test fixture entry.', 'e2e-test-suite')
            RETURNING id
            """,
            TEST_PLATE,
        )
        return str(row["id"])
    finally:
        await conn.close()


async def cleanup_test_records(dsn: str, watchlist_id: str) -> None:
    """
    Removes all rows created during this test run: alerts and
    detections tied to the test camera/plate, the vehicle_tracks row
    for the test plate, and the watchlist fixture row itself.
    """
    conn = await asyncpg.connect(dsn=dsn, timeout=10.0)
    try:
        await conn.execute(
            "DELETE FROM alerts WHERE plate_text = $1 OR watchlist_id = $2",
            TEST_PLATE,
            watchlist_id,
        )
        await conn.execute(
            "DELETE FROM detections WHERE camera_id = $1 AND plate_text = $2",
            TEST_CAMERA_ID,
            TEST_PLATE,
        )
        await conn.execute(
            "DELETE FROM vehicle_tracks WHERE camera_id = $1 AND plate_text = $2",
            TEST_CAMERA_ID,
            TEST_PLATE,
        )
        await conn.execute("DELETE FROM watchlist WHERE id = $1", watchlist_id)
    finally:
        await conn.close()


async def fetch_counts(dsn: str) -> Dict[str, int]:
    """
    Fetches post-run counts of detections, vehicle_tracks, and alerts
    associated with the test camera/plate.
    """
    conn = await asyncpg.connect(dsn=dsn, timeout=10.0)
    try:
        detection_count = await conn.fetchval(
            "SELECT COUNT(*) FROM detections WHERE camera_id = $1", TEST_CAMERA_ID
        )
        track_count = await conn.fetchval(
            "SELECT COUNT(*) FROM vehicle_tracks WHERE camera_id = $1 AND plate_text = $2",
            TEST_CAMERA_ID,
            TEST_PLATE,
        )
        alert_count = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE plate_text = $1", TEST_PLATE
        )
        return {
            "detections": int(detection_count),
            "vehicle_tracks": int(track_count),
            "alerts": int(alert_count),
        }
    finally:
        await conn.close()


async def run_e2e_test() -> bool:
    """
    Executes the full end-to-end integration test and returns True if
    all assertions pass, False otherwise. Test fixtures are always
    cleaned up, even on failure.
    """
    load_dotenv()
    dsn = os.environ.get("DB_URL")
    if not dsn:
        logger.error(
            "DB_URL environment variable must be set to run the e2e test suite."
        )
        return False

    logger.info("Ensuring schema is present on target database...")
    await ensure_schema(dsn)
    await ensure_test_camera(dsn)

    logger.info("Inserting mock watchlist fixture for plate=%s...", TEST_PLATE)
    watchlist_id = await insert_test_watchlist_entry(dsn)

    db = Database(dsn=dsn)
    await db.connect()

    passed = False
    broadcast_alerts: List[AlertPayload] = []

    async def capture_broadcast(alert: AlertPayload) -> None:
        broadcast_alerts.append(alert)
        logger.info(
            "Captured broadcasted alert id=%s plate=%s flag_type=%s",
            alert.alert_id,
            alert.plate_text,
            alert.flag_type,
        )

    try:
        camera = Camera(
            id=TEST_CAMERA_ID,
            name="E2E Test Camera",
            rtsp_url=TEST_RTSP_URL,
            hls_url="https://cctv.corp8.cloud/test1/index.m3u8",
            lat=23.0225,
            lng=72.5714,
            department="QA",
        )

        detector = Detector(model_path="yolov8n.pt", device="cpu")
        tracker = Tracker(model_path="yolov8n.pt")
        ocr = PlateOCR(gpu=False)
        pipeline = DetectionPipeline(
            camera_id=camera.id, detector=detector, tracker=tracker, ocr=ocr
        )
        matcher = WatchlistMatcher(db=db)
        alert_engine = AlertEngine(db=db, matcher=matcher)

        stop_event = asyncio.Event()

        async def stop_after_duration() -> None:
            await asyncio.sleep(TEST_RUN_DURATION_SECONDS)
            stop_event.set()

        logger.info(
            "Running ingestion worker against %s for %.0f seconds...",
            TEST_RTSP_URL,
            TEST_RUN_DURATION_SECONDS,
        )

        await asyncio.gather(
            run_camera(
                camera=camera,
                pipeline=pipeline,
                alert_engine=alert_engine,
                ws_server_broadcast_fn=capture_broadcast,
                frame_skip=3,
                stop_event=stop_event,
            ),
            stop_after_duration(),
        )

        counts = await fetch_counts(dsn)
        logger.info("Post-run counts: %s", counts)

        detections_logged = counts["detections"] > 0
        tracks_upserted = counts["vehicle_tracks"] > 0
        alert_recorded = counts["alerts"] > 0
        alert_broadcast_received = len(broadcast_alerts) > 0

        logger.info("Assertion: detections logged        -> %s", detections_logged)
        logger.info("Assertion: vehicle_tracks upserted   -> %s", tracks_upserted)
        logger.info("Assertion: alert recorded            -> %s", alert_recorded)
        logger.info("Assertion: alert broadcast received  -> %s", alert_broadcast_received)

        passed = detections_logged and tracks_upserted and alert_recorded

    except Exception:
        logger.error("E2E test run raised an unexpected exception.", exc_info=True)
        passed = False
    finally:
        await db.close()
        logger.info("Cleaning up test fixtures...")
        await cleanup_test_records(dsn, watchlist_id)

    return passed


def main() -> int:
    result = asyncio.run(run_e2e_test())
    print("=" * 79)
    print(f"SENTINEL E2E TEST RESULT: {'PASS' if result else 'FAIL'}")
    print("=" * 79)
    return 0 if result else 1


def test_end_to_end() -> None:
    """
    pytest entrypoint. Requires a reachable local RTSP test stream
    (see scripts/fake_rtsp_setup.sh) and a Postgres instance reachable
    via DB_URL.
    """
    assert asyncio.run(run_e2e_test()) is True


if __name__ == "__main__":
    sys.exit(main())
