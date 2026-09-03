"""
sentinel/ingestion/camera_manager.py
======================================

Camera fleet resolution for the Sentinel edge analytics platform.

Two modes of operation:

1. Fallback mode (no api_url configured): three hardcoded demo cameras
   pointed at a local MediaMTX RTSP relay, seeded with Ahmedabad-area
   coordinates for map/dashboard demo purposes.

2. Remote mode (api_url configured): pulls a cameras.json manifest from
   the configured camera grid API using bearer-token auth, and derives
   RTSP/HLS URLs from each camera's id.

The manager maintains a thread-safe in-memory cache of Camera objects
and exposes both synchronous and asynchronous refresh entrypoints so it
can be used from blocking scripts (main.py) as well as the async FastAPI
service layer.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REMOTE_RTSP_HOST = "103.250.160.189"
REMOTE_RTSP_PORT = 8554
HLS_HOST = "https://cctv.corp8.cloud"


@dataclass
class Camera:
    """
    Represents a single CCTV camera resolved either from the local
    fallback fleet or from the remote camera grid API.
    """

    id: str
    name: str
    rtsp_url: str
    hls_url: str
    lat: float
    lng: float
    department: str


def _fallback_cameras() -> List[Camera]:
    """
    Builds the hardcoded three-camera fallback fleet used when no
    camera grid API is configured (offline demo / hackathon mode).

    RTSP endpoints target a local MediaMTX relay (see
    scripts/fake_rtsp_setup.sh). Coordinates are seeded around
    Ahmedabad, Gujarat.
    """
    seed = [
        {
            "id": "test1",
            "name": "Ahmedabad Camera 1",
            "lat": 23.0225,
            "lng": 72.5714,
            "department": "Ahmedabad Police",
        },
        {
            "id": "test2",
            "name": "Ahmedabad Camera 2",
            "lat": 23.0339,
            "lng": 72.5850,
            "department": "Ahmedabad Police",
        },
        {
            "id": "test3",
            "name": "Ahmedabad Camera 3",
            "lat": 23.0125,
            "lng": 72.5580,
            "department": "Ahmedabad Police",
        },
    ]

    cameras: List[Camera] = []
    for entry in seed:
        cam_id = entry["id"]
        cameras.append(
            Camera(
                id=cam_id,
                name=entry["name"],
                rtsp_url=f"rtsp://localhost:8554/stream/{cam_id}",
                hls_url=f"{HLS_HOST}/{cam_id}/index.m3u8",
                lat=entry["lat"],
                lng=entry["lng"],
                department=entry["department"],
            )
        )
    return cameras


def _camera_from_remote_entry(entry: dict) -> Camera:
    """
    Maps a single raw JSON entry from the remote cameras.json manifest
    into a Camera instance, deriving RTSP and HLS URLs from the
    camera's id per the platform's fixed URL scheme.
    """
    cam_id = str(entry["id"])
    return Camera(
        id=cam_id,
        name=entry.get("name", cam_id),
        rtsp_url=f"rtsp://{REMOTE_RTSP_HOST}:{REMOTE_RTSP_PORT}/stream/{cam_id}",
        hls_url=f"{HLS_HOST}/{cam_id}/index.m3u8",
        lat=float(entry["lat"]),
        lng=float(entry["lng"]),
        department=entry.get("department", "Unknown"),
    )


class CameraManager:
    """
    Resolves and caches the active camera fleet.

    Parameters
    ----------
    api_url : str | None
        Base URL of the remote camera grid API. When falsy (None or
        empty string), the manager operates in fallback mode using
        three hardcoded local demo cameras.
    password : str | None
        Bearer token used to authenticate against the camera grid API's
        cameras.json endpoint. Only used when api_url is set.
    """

    def __init__(self, api_url: Optional[str] = None, password: Optional[str] = None) -> None:
        self.api_url = api_url or None
        self.password = password or None
        self._lock = threading.RLock()
        self._cache: Dict[str, Camera] = {}
        self.refresh()

    def _fetch_remote_cameras(self) -> List[Camera]:
        """
        Fetches cameras.json from the configured camera grid API using
        an Authorization: Bearer <password> header, and parses each
        entry into a Camera instance.
        """
        assert self.api_url is not None
        url = self.api_url.rstrip("/") + "/cameras.json"
        headers = {}
        if self.password:
            headers["Authorization"] = f"Bearer {self.password}"

        logger.info("Fetching camera manifest from %s", url)
        response = httpx.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        payload = response.json()

        cameras: List[Camera] = []
        for entry in payload:
            try:
                cameras.append(_camera_from_remote_entry(entry))
            except (KeyError, ValueError, TypeError):
                logger.warning(
                    "Skipping malformed camera entry from remote manifest: %r",
                    entry,
                    exc_info=True,
                )
        return cameras

    def refresh(self) -> None:
        """
        Synchronously refreshes the internal camera cache. Falls back
        to the hardcoded fleet if api_url is not configured, or if the
        remote fetch fails for any reason.
        """
        if not self.api_url:
            logger.info("CAMERA_GRID_URL not configured. Using fallback camera fleet.")
            cameras = _fallback_cameras()
        else:
            try:
                cameras = self._fetch_remote_cameras()
                if not cameras:
                    logger.warning(
                        "Remote camera manifest returned zero usable entries. "
                        "Falling back to local fleet."
                    )
                    cameras = _fallback_cameras()
            except Exception:
                logger.error(
                    "Failed to fetch remote camera manifest from %s. "
                    "Falling back to local fleet.",
                    self.api_url,
                    exc_info=True,
                )
                cameras = _fallback_cameras()

        with self._lock:
            self._cache = {camera.id: camera for camera in cameras}

        logger.info("Camera cache refreshed with %d camera(s).", len(self._cache))

    async def refresh_async(self) -> None:
        """
        Asynchronous variant of refresh(), suitable for use from the
        FastAPI async service layer. Delegates to the synchronous
        implementation via a worker thread so as not to block the
        event loop on network I/O.
        """
        await asyncio.to_thread(self.refresh)

    def get_cameras(self) -> List[Camera]:
        """
        Returns a snapshot list of all currently cached cameras.
        Thread-safe.
        """
        with self._lock:
            return list(self._cache.values())

    def get_camera(self, camera_id: str) -> Optional[Camera]:
        """
        Returns a single cached camera by id, or None if not present.
        Thread-safe.
        """
        with self._lock:
            return self._cache.get(camera_id)

    def get_primary_camera(self) -> Optional[Camera]:
        """
        Returns the first camera in the cache in a deterministic order,
        or None if the cache is empty. Used by smoke-test / demo
        entrypoints that need a single representative camera.
        """
        with self._lock:
            if not self._cache:
                return None
            first_id = sorted(self._cache.keys())[0]
            return self._cache[first_id]
