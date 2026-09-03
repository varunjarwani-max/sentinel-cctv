"""
sentinel/alerts/alert_engine.py
==================================

Ties together detection logging, vehicle track history, and watchlist
correlation into a single per-result alert pipeline.

Processing order for each PipelineResult:
1. Log the raw detection to the detections table (always).
2. If the detection is a vehicle with a recognized plate, upsert its
   vehicle_tracks row so track continuity/history is maintained
   regardless of whether it matches the watchlist.
3. Run watchlist correlation on the plate text.
4. If matched, resolve the originating camera's GIS metadata, persist
   an alerts row, and return an AlertPayload ready for websocket
   broadcast. If unmatched, return None.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sentinel.correlation.db import Database
from sentinel.correlation.matcher import WatchlistMatcher
from sentinel.detection.pipeline import PipelineResult, VEHICLE_CLASSES

logger = logging.getLogger(__name__)


@dataclass
class AlertPayload:
    """
    Fully-resolved alert ready for API response / websocket broadcast.

    Attributes
    ----------
    alert_id : str
        UUID (as string) of the persisted alert row.
    camera_id : str
        Identifier of the camera that produced the triggering detection.
    camera_name : str
        Human-readable camera name, resolved from the cameras table.
    track_id : int
        Persistent tracking identifier of the flagged object.
    plate_text : str
        Normalized plate text that triggered the match.
    flag_type : str
        One of STOLEN, WANTED, SUSPECT, BLACKLISTED.
    confidence : float
        Detection/tracking confidence associated with the triggering
        result.
    alerted_at : str
        ISO-8601 UTC timestamp string of when the alert was generated.
    lat : float
        Latitude of the triggering camera.
    lng : float
        Longitude of the triggering camera.
    """

    alert_id: str
    camera_id: str
    camera_name: str
    track_id: int
    plate_text: str
    flag_type: str
    confidence: float
    alerted_at: str
    lat: float
    lng: float


class AlertEngine:
    """
    Orchestrates detection logging, vehicle track history, and
    watchlist-driven alert generation.

    Parameters
    ----------
    db : Database
        Connected Database instance used for all persistence
        operations.
    matcher : WatchlistMatcher
        Watchlist correlation engine used to check recognized plates
        against active flagged entries.
    """

    def __init__(self, db: Database, matcher: WatchlistMatcher) -> None:
        self.db = db
        self.matcher = matcher

    async def process(self, result: PipelineResult) -> Optional[AlertPayload]:
        """
        Processes a single PipelineResult end-to-end: logs the
        detection, updates vehicle track history where applicable,
        runs watchlist correlation, and returns an AlertPayload if a
        match was found.

        Returns None (after logging/track-upsert side effects still
        having occurred) if no watchlist match was found.
        """
        detection_id = await self.db.insert_detection(result)

        if result.plate_text and result.class_name in VEHICLE_CLASSES:
            try:
                await self.db.upsert_vehicle_track(
                    track_id=result.track_id,
                    camera_id=result.camera_id,
                    plate_text=result.plate_text,
                    timestamp=datetime.now(timezone.utc),
                )
            except Exception:
                logger.error(
                    "Failed to upsert vehicle_track for track_id=%s camera=%s plate=%s",
                    result.track_id,
                    result.camera_id,
                    result.plate_text,
                    exc_info=True,
                )

        try:
            match = await self.matcher.match(result)
        except Exception:
            logger.error(
                "Watchlist match failed for detection_id=%s camera=%s",
                detection_id,
                result.camera_id,
                exc_info=True,
            )
            return None

        if match is None:
            return None

        camera = await self.db.get_camera(result.camera_id)
        if camera is None:
            logger.warning(
                "Camera %s not found while assembling alert payload for "
                "detection_id=%s; using placeholder GIS coordinates.",
                result.camera_id,
                detection_id,
            )
            camera_name = result.camera_id
            lat = 0.0
            lng = 0.0
        else:
            camera_name = camera["name"]
            lat = float(camera["lat"])
            lng = float(camera["lng"])

        alerted_at = datetime.now(timezone.utc)

        alert_id = await self.db.insert_alert(
            detection_id=detection_id,
            watchlist_id=match.watchlist_id,
            camera_id=result.camera_id,
            track_id=result.track_id,
            plate_text=match.matched_on,
            flag_type=match.flag_type,
            confidence=result.confidence,
        )

        payload = AlertPayload(
            alert_id=alert_id,
            camera_id=result.camera_id,
            camera_name=camera_name,
            track_id=result.track_id,
            plate_text=match.matched_on,
            flag_type=match.flag_type,
            confidence=result.confidence,
            alerted_at=alerted_at.isoformat(),
            lat=lat,
            lng=lng,
        )

        logger.info(
            "Generated alert id=%s camera=%s plate=%s flag_type=%s",
            alert_id,
            result.camera_id,
            match.matched_on,
            match.flag_type,
        )
        return payload
