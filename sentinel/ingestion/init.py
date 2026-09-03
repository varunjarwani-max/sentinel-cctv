"""
Sentinel Ingestion Package
===========================

Exposes the core ingestion primitives used by the Sentinel edge analytics
platform:

- StreamReader: resilient RTSP frame generator with PTS-based timing,
  discontinuity detection, and exponential backoff on decode failure.
- CameraManager: camera fleet resolver, supporting both a remote camera
  grid API and a hardcoded local fallback fleet for offline/demo mode.
"""

from sentinel.ingestion.stream_reader import StreamReader
from sentinel.ingestion.camera_manager import CameraManager, Camera

__all__ = ["StreamReader", "CameraManager", "Camera"]
