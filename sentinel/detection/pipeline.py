"""
sentinel/detection/pipeline.py
================================

End-to-end per-frame detection pipeline tying together object
tracking and license plate OCR for a single camera stream.

Design notes:
- process_frame() runs tracking once per frame (which internally
  performs detection via YOLO), then for any tracked object whose
  class is a vehicle class, crops the object's bounding box and
  attempts plate OCR on that crop.
- Non-vehicle tracked objects (e.g. "person") are still emitted as
  PipelineResult entries, with plate_text=None, since downstream
  consumers (dashboards, alerting) need full scene occupancy, not just
  vehicles.
- handle_discontinuity() is the pipeline-level hook invoked by callers
  when the underlying stream reports a PTS discontinuity (e.g. camera
  reboot, looped test file), ensuring stale track associations don't
  bleed across a discontinuity boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from sentinel.detection.detector import Detector
from sentinel.detection.tracker import Tracker, TrackedObject
from sentinel.detection.ocr import PlateOCR

logger = logging.getLogger(__name__)

# Vehicle classes eligible for license plate OCR attempts.
VEHICLE_CLASSES: frozenset[str] = frozenset({"car", "bus", "truck", "motorcycle"})


@dataclass
class PipelineResult:
    """
    A single per-object, per-frame pipeline output combining tracking
    and (where applicable) plate OCR results.

    Attributes
    ----------
    track_id : int
        Persistent tracking identifier, or -1 if unresolved.
    class_name : str
        Detected/tracked object class name.
    confidence : float
        Detection/tracking confidence score.
    bbox : tuple[int, int, int, int]
        Bounding box as (x1, y1, x2, y2) in pixel coordinates.
    pts_ms : float
        Decoder-reported presentation timestamp (ms) of the source
        frame.
    camera_id : str
        Identifier of the camera the frame originated from.
    plate_text : str | None
        Normalized license plate text, if this object is a vehicle and
        a valid plate was read from its crop; otherwise None.
    """

    track_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    pts_ms: float
    camera_id: str
    plate_text: Optional[str]


class DetectionPipeline:
    """
    Orchestrates tracking and plate OCR for a single camera's frame
    stream.

    Parameters
    ----------
    camera_id : str
        Identifier of the camera this pipeline instance serves.
    detector : Detector
        Used here for its safe crop() utility when extracting vehicle
        regions ahead of OCR.
    tracker : Tracker
        Performs persistent multi-object tracking per frame.
    ocr : PlateOCR
        Performs license plate OCR on vehicle crops.
    """

    def __init__(
        self,
        camera_id: str,
        detector: Detector,
        tracker: Tracker,
        ocr: PlateOCR,
    ) -> None:
        self.camera_id = camera_id
        self.detector = detector
        self.tracker = tracker
        self.ocr = ocr
        self._frame_count = 0

    def process_frame(self, frame: np.ndarray, pts_ms: float) -> List[PipelineResult]:
        """
        Runs the full per-frame pipeline:
          1. Track objects in the frame (detection + persistent ID
             assignment via ByteTrack).
          2. For tracked objects belonging to VEHICLE_CLASSES, crop the
             bounding box region and attempt plate OCR.
          3. Assemble and return one PipelineResult per tracked object.

        Any exception raised during processing of an individual tracked
        object is caught and logged so that a single bad crop or OCR
        failure cannot abort processing of the remaining objects in the
        frame.
        """
        self._frame_count += 1

        tracked_objects: List[TrackedObject] = self.tracker.track(
            frame=frame, pts_ms=pts_ms, camera_id=self.camera_id
        )

        results: List[PipelineResult] = []

        for obj in tracked_objects:
            plate_text: Optional[str] = None

            if obj.class_name in VEHICLE_CLASSES:
                try:
                    crop = self.detector.crop(frame, obj.bbox)
                    if crop.size > 0:
                        plate_text = self.ocr.read_plate(crop)
                except Exception:
                    logger.error(
                        "Plate OCR failed for track_id=%s camera=%s bbox=%s",
                        obj.track_id,
                        self.camera_id,
                        obj.bbox,
                        exc_info=True,
                    )
                    plate_text = None

            results.append(
                PipelineResult(
                    track_id=obj.track_id,
                    class_name=obj.class_name,
                    confidence=obj.confidence,
                    bbox=obj.bbox,
                    pts_ms=obj.pts_ms,
                    camera_id=obj.camera_id,
                    plate_text=plate_text,
                )
            )

        return results

    def handle_discontinuity(self) -> None:
        """
        Clears all tracker-internal history and resets the pipeline's
        internal frame counter. Called by stream consumers when a PTS
        discontinuity has been detected (e.g. camera reboot, looped
        test file), so that stale track identities from before the
        discontinuity are never associated with objects after it.
        """
        logger.warning(
            "Handling discontinuity for camera=%s: resetting tracker state.",
            self.camera_id,
        )
        self.tracker.reset()
        self._frame_count = 0
