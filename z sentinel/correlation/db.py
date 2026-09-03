"""
sentinel/correlation/db.py
=============================

Async PostgreSQL persistence layer for the Sentinel correlation backbone,
built on asyncpg connection pooling.

Design notes:
- Database.connect() must be awaited once (typically during application
  startup, e.g. a FastAPI lifespan handler) before any other method is
  called; the constructor itself remains synchronous since asyncpg pool
  creation is inherently async.
- All queries are fully parameterized ($1, $2, ...) to eliminate SQL
  injection risk, including on the hot-path plate lookup and ingest
  routes that receive untrusted network input.
- Row-returning methods convert asyncpg.Record objects to plain dicts
  so callers (FastAPI JSON responses, websocket broadcast payloads)
  never need to import asyncpg types themselves.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg

from sentinel.detection.pipeline import PipelineResult

logger = logging.getLogger(__name__)


class Database:
    """
    Async PostgreSQL access layer backed by an asyncpg connection pool.

    Parameters
    ----------
    dsn : str
        PostgreSQL connection string, e.g.
        "postgresql://user:pass@host:5432/sentinel".
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """
        Establishes the underlying asyncpg connection pool. Must be
        awaited before any other Database method is used.
        """
        logger.info("Creating asyncpg connection pool (min_size=5, max_size=20)...")
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn, min_size=5, max_size=20
        )
        logger.info("Database connection pool established.")

    async def close(self) -> None:
        """
        Closes the underlying connection pool. Safe to call even if
        connect() was never called.
        """
        if self.pool is not None:
            await self.pool.close()
            logger.info("Database connection pool closed.")
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError(
                "Database.connect() must be awaited before using this method."
            )
        return self.pool

    # -------------------------------------------------------------------
    # Write paths
    # -------------------------------------------------------------------

    async def insert_detection(self, res: PipelineResult) -> str:
        """
        Persists a single PipelineResult as a row in the detections
        table and returns the newly generated detection id (UUID) as a
        string.
        """
        pool = self._require_pool()
        x1, y1, x2, y2 = res.bbox

        row = await pool.fetchrow(
            """
            INSERT INTO detections (
                camera_id, track_id, class_name, confidence,
                bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                plate_text, pts_ms
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
            """,
            res.camera_id,
            res.track_id,
            res.class_name,
            res.confidence,
            x1,
            y1,
            x2,
            y2,
            res.plate_text,
            res.pts_ms,
        )
        detection_id = str(row["id"])
        logger.debug(
            "Inserted detection id=%s camera=%s track_id=%s class=%s",
            detection_id,
            res.camera_id,
            res.track_id,
            res.class_name,
        )
        return detection_id

    async def insert_alert(
        self,
        detection_id: str,
        watchlist_id: str,
        camera_id: str,
        track_id: int,
        plate_text: str,
        flag_type: str,
        confidence: float,
    ) -> str:
        """
        Persists a single alert row, linking back to its originating
        detection and matched watchlist entry, and returns the newly
        generated alert id (UUID) as a string.
        """
        pool = self._require_pool()

        row = await pool.fetchrow(
            """
            INSERT INTO alerts (
                detection_id, watchlist_id, camera_id, track_id,
                plate_text, flag_type, confidence
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            detection_id,
            watchlist_id,
            camera_id,
            track_id,
            plate_text,
            flag_type,
            confidence,
        )
        alert_id = str(row["id"])
        logger.info(
            "Inserted alert id=%s camera=%s plate=%s flag_type=%s",
            alert_id,
            camera_id,
            plate_text,
            flag_type,
        )
        return alert_id

    async def upsert_vehicle_track(
        self,
        track_id: int,
        camera_id: str,
        plate_text: str,
        timestamp: datetime,
    ) -> None:
        """
        Inserts or updates the vehicle_tracks row identified by the
        (track_id, camera_id, plate_text) unique constraint: on
        conflict, extends last_seen_at and increments frame_count
        rather than creating a duplicate row.
        """
        pool = self._require_pool()

        await pool.execute(
            """
            INSERT INTO vehicle_tracks (
                track_id, camera_id, plate_text, first_seen_at, last_seen_at, frame_count
            )
            VALUES ($1, $2, $3, $4, $4, 1)
            ON CONFLICT (track_id, camera_id, plate_text)
            DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                frame_count = vehicle_tracks.frame_count + 1
            """,
            track_id,
            camera_id,
            plate_text,
            timestamp,
        )
        logger.debug(
            "Upserted vehicle_track track_id=%s camera=%s plate=%s at %s",
            track_id,
            camera_id,
            plate_text,
            timestamp.isoformat(),
        )

    # -------------------------------------------------------------------
    # Read paths
    # -------------------------------------------------------------------

    async def get_alerts_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Returns the `limit` most recent alerts, most recent first, each
        enriched with the originating camera's name for display
        purposes.
        """
        pool = self._require_pool()

        rows = await pool.fetch(
            """
            SELECT
                a.id AS alert_id,
                a.detection_id,
                a.watchlist_id,
                a.camera_id,
                c.name AS camera_name,
                a.track_id,
                a.plate_text,
                a.flag_type,
                a.confidence,
                a.alerted_at,
                a.acknowledged
            FROM alerts a
            LEFT JOIN cameras c ON c.id = a.camera_id
            ORDER BY a.alerted_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(row) for row in rows]

    async def get_vehicle_history(self, plate_text: str) -> List[Dict[str, Any]]:
        """
        Returns all vehicle_tracks rows for the given plate text,
        ordered by most recently seen first.
        """
        pool = self._require_pool()

        rows = await pool.fetch(
            """
            SELECT
                track_id,
                camera_id,
                plate_text,
                first_seen_at,
                last_seen_at,
                frame_count
            FROM vehicle_tracks
            WHERE plate_text = $1
            ORDER BY last_seen_at DESC
            """,
            plate_text,
        )
        return [dict(row) for row in rows]

    async def get_cameras(self) -> List[Dict[str, Any]]:
        """
        Returns all active cameras.
        """
        pool = self._require_pool()

        rows = await pool.fetch(
            """
            SELECT id, name, department, lat, lng, hls_url, rtsp_url, active, created_at
            FROM cameras
            WHERE active = TRUE
            ORDER BY name
            """
        )
        return [dict(row) for row in rows]

    async def get_camera(self, camera_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns a single camera row by id, or None if not found.
        """
        pool = self._require_pool()

        row = await pool.fetchrow(
            """
            SELECT id, name, department, lat, lng, hls_url, rtsp_url, active, created_at
            FROM cameras
            WHERE id = $1
            """,
            camera_id,
        )
        return dict(row) if row is not None else None
