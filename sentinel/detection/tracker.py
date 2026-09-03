"""
sentinel/detection/tracker.py
================================

ByteTrack-backed multi-object tracker built on top of Ultralytics'
built-in tracking integration.

Design notes:
- Tracking is delegated entirely to Ultralytics' `model.track(...)`
  call using the bundled `bytetrack.yaml` tracker configuration, rather
  than reimplementing association logic. `persist=True` keeps track
  state alive across successive calls against the same model instance.
- Detections lacking a resolvable persistent track ID (which can happen
  transiently while ByteTrack is still confirming a new track) are
  assigned track_id = -1 rather than being dropped, so downstream
  consumers can decide whether to treat unconfirmed tracks specially.
- Output classes and confidence are filtered using the same allowlist
  and threshold as Detector, so that tracking and one-shot detection
  remain semantically consistent across the detection subsystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from ultralytics import YOLO

from sentinel.detection.detector import ALLOWED_CLASSES, MIN_CONFIDENCE

logger = logging.getLogger(__name__)


@dataclass
class TrackedObject:
    """
    A single tracked object within a frame, carrying both detection
    metadata and tracking/provenance metadata.

    Attributes
    ----------
    track_id : int
        Persistent tracking identifier assigned by ByteTrack, or -1 if
        no persistent ID could be resolved for this detection.
    class_name : str
        Human-readable COCO class name (restricted to ALLOWED_CLASSES).
    confidence : float
        Model confidence score in [0.0, 1.0].
    bbox : tuple[int, int, int, int]
        Bounding box as (x1, y1, x2, y2) in pixel coordinates.
    pts_ms : float
        Decoder-reported presentation timestamp (ms) of the source
        frame, as supplied by the caller.
    camera_id : str
        Identifier of the camera the frame originated from, as
        supplied by the caller.
    """

    track_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    pts_ms: float
    camera_id: str


class Tracker:
    """
    Wraps an Ultralytics YOLOv8 model configured for persistent
    ByteTrack tracking.

    Parameters
    ----------
    model_path : str
        Path to a YOLOv8 weights file (.pt), or a model name resolvable
        by Ultralytics (e.g. "yolov8n.pt"). A separate model instance
        from Detector is used deliberately, since Ultralytics attaches
        tracker state directly to the model's predictor.
    """

    def __init__(self, model_path: str = "yolov8n.pt") -> None:
        self.model_path = model_path
        logger.info("Loading YOLOv8 tracking model from %s", model_path)
        self.model = YOLO(model_path)

    def track(
        self, frame: np.ndarray, pts_ms: float, camera_id: str
    ) -> List[TrackedObject]:
        """
        Runs persistent ByteTrack tracking on a single frame and
        returns tracked objects restricted to ALLOWED_CLASSES with
        confidence >= MIN_CONFIDENCE.

        Any exception during tracking is caught and logged; an empty
        list is returned in that case so that callers can continue
        operating without interruption.
        """
        try:
            results = self.model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                stream=False,
                verbose=False,
            )
        except Exception:
            logger.error(
                "Tracker inference failed for camera=%s pts_ms=%.2f",
                camera_id,
                pts_ms,
                exc_info=True,
            )
            return []

        tracked_objects: List[TrackedObject] = []

        try:
            if not results:
                return tracked_objects

            result = results[0]
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                return tracked_objects

            names = result.names

            box_ids = boxes.id
            has_ids = box_ids is not None

            for i, box in enumerate(boxes):
                cls_id = int(box.cls.item())
                class_name = names.get(cls_id, str(cls_id))
                confidence = float(box.conf.item())

                if class_name not in ALLOWED_CLASSES:
                    continue
                if confidence < MIN_CONFIDENCE:
                    continue

                if has_ids:
                    try:
                        track_id = int(box_ids[i].item())
                    except (IndexError, ValueError, AttributeError):
                        track_id = -1
                else:
                    track_id = -1

                xyxy = box.xyxy[0].tolist()
                x1, y1, x2, y2 = (int(round(v)) for v in xyxy)

                tracked_objects.append(
                    TrackedObject(
                        track_id=track_id,
                        class_name=class_name,
                        confidence=confidence,
                        bbox=(x1, y1, x2, y2),
                        pts_ms=pts_ms,
                        camera_id=camera_id,
                    )
                )
        except Exception:
            logger.error(
                "Tracker post-processing failed while parsing tracking results "
                "for camera=%s pts_ms=%.2f",
                camera_id,
                pts_ms,
                exc_info=True,
            )
            return []

        return tracked_objects

    def reset(self) -> None:
        """
        Purges internal ByteTrack association state.

        Ultralytics maintains tracker state on the model's `predictor`
        attribute across calls when `persist=True`. Dropping that
        predictor forces a fresh predictor (and fresh tracker state) to
        be constructed on the next `track()` call, which is the
        supported way to fully sever track continuity, e.g. after a
        detected stream discontinuity or scene cut.
        """
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None:
            trackers = getattr(predictor, "trackers", None)
            if trackers:
                for t in trackers:
                    try:
                        t.reset()
                    except Exception:
                        logger.debug(
                            "Suppressed exception while resetting an "
                            "individual ByteTrack tracker instance.",
                            exc_info=True,
                        )
            try:
                self.model.predictor = None
            except Exception:
                logger.debug(
                    "Suppressed exception while clearing model.predictor "
                    "during tracker reset.",
                    exc_info=True,
                )
        logger.info("Tracker state reset for model=%s", self.model_path)
