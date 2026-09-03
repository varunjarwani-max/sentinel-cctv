"""
sentinel/detection/detector.py
================================

YOLOv8-backed object detector restricted to a fixed set of
policing-relevant COCO classes.

Design notes:
- The class allowlist and minimum confidence threshold are exposed as
  module-level constants so that the Tracker module can reuse them and
  keep detection semantics consistent across the detection subsystem.
- detect() is defensive: any exception raised during inference (model
  load races, malformed frames, CUDA OOM, etc.) is caught, logged, and
  results in an empty detection list rather than propagating and
  crashing the ingestion loop that calls it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# Fixed subset of COCO classes relevant to CCTV policing analytics.
ALLOWED_CLASSES: frozenset[str] = frozenset(
    {"person", "car", "motorcycle", "bus", "truck"}
)

# Minimum confidence required for a detection to be retained.
MIN_CONFIDENCE: float = 0.45


@dataclass
class Detection:
    """
    A single object detection within a frame.

    Attributes
    ----------
    class_name : str
        Human-readable COCO class name (restricted to ALLOWED_CLASSES).
    confidence : float
        Model confidence score in [0.0, 1.0].
    bbox : tuple[int, int, int, int]
        Bounding box as (x1, y1, x2, y2) in pixel coordinates.
    """

    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]


class Detector:
    """
    Thin wrapper around an Ultralytics YOLOv8 model that restricts
    output to a fixed set of policing-relevant classes and a minimum
    confidence threshold.

    Parameters
    ----------
    model_path : str
        Path to a YOLOv8 weights file (.pt), or a model name resolvable
        by Ultralytics (e.g. "yolov8n.pt").
    device : str
        Inference device string passed through to Ultralytics
        (e.g. "cpu", "cuda:0").
    """

    def __init__(self, model_path: str = "yolov8n.pt", device: str = "cpu") -> None:
        self.model_path = model_path
        self.device = device
        logger.info("Loading YOLOv8 model from %s on device=%s", model_path, device)
        self.model = YOLO(model_path)
        self.model.to(device)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """
        Runs object detection on a single frame and returns detections
        restricted to ALLOWED_CLASSES with confidence >= MIN_CONFIDENCE.

        Any exception during inference is caught and logged; an empty
        list is returned in that case so that callers (ingestion loops,
        pipelines) can continue operating without interruption.
        """
        try:
            results = self.model.predict(
                source=frame,
                device=self.device,
                verbose=False,
            )
        except Exception:
            logger.error("Detector inference failed.", exc_info=True)
            return []

        detections: List[Detection] = []

        try:
            if not results:
                return detections

            result = results[0]
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                return detections

            names = result.names

            for box in boxes:
                cls_id = int(box.cls.item())
                class_name = names.get(cls_id, str(cls_id))
                confidence = float(box.conf.item())

                if class_name not in ALLOWED_CLASSES:
                    continue
                if confidence < MIN_CONFIDENCE:
                    continue

                xyxy = box.xyxy[0].tolist()
                x1, y1, x2, y2 = (int(round(v)) for v in xyxy)

                detections.append(
                    Detection(
                        class_name=class_name,
                        confidence=confidence,
                        bbox=(x1, y1, x2, y2),
                    )
                )
        except Exception:
            logger.error(
                "Detector post-processing failed while parsing inference results.",
                exc_info=True,
            )
            return []

        return detections

    def crop(
        self, frame: np.ndarray, bbox: Tuple[int, int, int, int]
    ) -> np.ndarray:
        """
        Safely crops a region from frame according to bbox, clamping
        coordinates to the frame's valid pixel range so that
        out-of-bounds or malformed boxes never raise an indexing error.

        Parameters
        ----------
        frame : np.ndarray
            Source frame, shape (h, w, c) or (h, w).
        bbox : tuple[int, int, int, int]
            (x1, y1, x2, y2) bounding box in pixel coordinates.

        Returns
        -------
        np.ndarray
            The cropped region. May be an empty array if the clamped
            box has zero area.
        """
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox

        x1c = max(0, min(x1, w))
        x2c = max(0, min(x2, w))
        y1c = max(0, min(y1, h))
        y2c = max(0, min(y2, h))

        if x2c <= x1c or y2c <= y1c:
            logger.debug(
                "Clamped bbox %s against frame size (%d, %d) yielded zero area.",
                bbox,
                w,
                h,
            )
            return frame[0:0, 0:0]

        return frame[y1c:y2c, x1c:x2c]
