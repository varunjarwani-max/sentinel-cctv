"""
sentinel/ingestion/stream_reader.py
=====================================

Resilient RTSP stream reader for the Sentinel edge analytics platform.

Design constraints:
- Frame timing is derived EXCLUSIVELY from the decoder's reported
  presentation timestamp (cv2.CAP_PROP_POS_MSEC). Wall-clock arrival
  time is never used as a substitute, since RTSP jitter and decoder
  buffering make wall-clock timing unreliable for forensic/analytics
  purposes.
- Stream discontinuities (camera reboot, looped test file, RTSP
  renegotiation) are detected by observing PTS regression and are
  logged as warnings without interrupting the generator.
- Transient read failures back off exponentially to avoid hammering
  a camera or RTSP relay that is temporarily unreachable.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Generator, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Force TCP transport for RTSP capture. This must be set in the process
# environment before any cv2.VideoCapture backed by FFMPEG is instantiated,
# since FFMPEG's RTSP demuxer reads this option at capture-open time.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"


class StreamReader:
    """
    Wraps a single RTSP camera stream and yields decoded frames paired
    with their decoder-reported presentation timestamp (PTS) in
    milliseconds.

    Parameters
    ----------
    camera_id : str
        Unique identifier for the camera this reader is bound to. Used
        exclusively for logging/diagnostics.
    url : str
        RTSP URL to open.
    """

    INITIAL_BACKOFF_SECONDS: float = 2.0
    BACKOFF_MULTIPLIER: float = 2.0
    MAX_BACKOFF_SECONDS: float = 30.0

    def __init__(self, camera_id: str, url: str) -> None:
        self.camera_id = camera_id
        self.url = url
        self.cap: Optional[cv2.VideoCapture] = None
        self._closed = False
        self._open()

    def _open(self) -> None:
        """
        (Re)opens the underlying VideoCapture against the configured
        RTSP URL, forcing the FFMPEG backend so that
        OPENCV_FFMPEG_CAPTURE_OPTIONS is honored.
        """
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                logger.debug(
                    "Suppressed exception while releasing stale capture "
                    "for %s prior to reopen.",
                    self.camera_id,
                    exc_info=True,
                )
        logger.info("Opening RTSP capture for %s -> %s", self.camera_id, self.url)
        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

    def frames(self) -> Generator[Tuple[np.ndarray, float], None, None]:
        """
        Generator yielding (frame, pts_ms) tuples indefinitely until
        close() is called or the generator is explicitly stopped by the
        caller.

        Timing semantics:
        - pts_ms is read exclusively via
          self.cap.get(cv2.CAP_PROP_POS_MSEC) immediately after a
          successful read(). This is the decoder's own timestamp for the
          frame just decoded, and is the only timing source permitted
          for this generator.

        Failure semantics:
        - A failed read() (either cap.read() returning False, or a
          decoder-level exception) is treated as transient. The reader
          sleeps for an exponentially increasing backoff period, up to
          MAX_BACKOFF_SECONDS, and attempts to reopen the capture before
          retrying.
        - On successful read() after a backoff period, the backoff
          interval resets to INITIAL_BACKOFF_SECONDS.
        """
        backoff = self.INITIAL_BACKOFF_SECONDS
        last_pts: float = 0.0

        while not self._closed:
            if self.cap is None or not self.cap.isOpened():
                logger.warning(
                    "Capture for %s is not open. Attempting reopen in %.1fs.",
                    self.camera_id,
                    backoff,
                )
                time.sleep(backoff)
                try:
                    self._open()
                except Exception:
                    logger.debug(
                        "Reopen attempt failed for %s.",
                        self.camera_id,
                        exc_info=True,
                    )
                backoff = min(backoff * self.BACKOFF_MULTIPLIER, self.MAX_BACKOFF_SECONDS)
                continue

            try:
                ok, frame = self.cap.read()
            except cv2.error:
                logger.debug(
                    "Decoder anomaly (cv2.error) reading frame from %s.",
                    self.camera_id,
                    exc_info=True,
                )
                ok, frame = False, None
            except Exception:
                logger.debug(
                    "Unexpected exception reading frame from %s.",
                    self.camera_id,
                    exc_info=True,
                )
                ok, frame = False, None

            if not ok or frame is None:
                logger.warning(
                    "Read failure on %s. Backing off %.1fs before retry.",
                    self.camera_id,
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * self.BACKOFF_MULTIPLIER, self.MAX_BACKOFF_SECONDS)
                try:
                    self._open()
                except Exception:
                    logger.debug(
                        "Reopen attempt after read failure raised for %s.",
                        self.camera_id,
                        exc_info=True,
                    )
                continue

            # Successful read: reset backoff.
            backoff = self.INITIAL_BACKOFF_SECONDS

            current_pts = float(self.cap.get(cv2.CAP_PROP_POS_MSEC))

            if last_pts > 0 and current_pts < last_pts:
                logger.warning(
                    f"Discontinuity detected on {self.camera_id}: {last_pts}ms -> {current_pts}ms"
                )

            last_pts = current_pts

            yield frame, current_pts

    def close(self) -> None:
        """
        Releases the underlying capture resource and marks this reader
        closed so that any in-flight frames() generator terminates on
        its next loop iteration.
        """
        self._closed = True
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                logger.debug(
                    "Suppressed exception while releasing capture for %s during close().",
                    self.camera_id,
                    exc_info=True,
                )
            finally:
                self.cap = None
        logger.info("Closed capture for %s.", self.camera_id)
