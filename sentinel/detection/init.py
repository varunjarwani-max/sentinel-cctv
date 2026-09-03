"""
Sentinel Detection Package
============================

Exposes the core detection, tracking, OCR, and pipeline orchestration
primitives used by the Sentinel edge analytics platform:

- Detector / Detection: YOLOv8-backed object detection restricted to a
  policing-relevant subset of COCO classes.
- Tracker / TrackedObject: ByteTrack-backed multi-object tracking with
  persistent identity across frames.
- PlateOCR: EasyOCR-backed Indian license plate recognition with
  preprocessing and regex validation.
- DetectionPipeline / PipelineResult: end-to-end per-frame orchestration
  tying tracking and plate OCR together into a single result stream.
"""

from sentinel.detection.detector import Detector, Detection
from sentinel.detection.tracker import Tracker, TrackedObject
from sentinel.detection.ocr import PlateOCR
from sentinel.detection.pipeline import DetectionPipeline, PipelineResult

__all__ = [
    "Detector",
    "Detection",
    "Tracker",
    "TrackedObject",
    "PlateOCR",
    "DetectionPipeline",
    "PipelineResult",
]
