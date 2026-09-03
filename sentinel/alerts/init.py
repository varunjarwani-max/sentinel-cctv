"""
Sentinel Alerts Package
==========================

Exposes the alert generation and real-time broadcast primitives used by
the Sentinel platform:

- AlertEngine / AlertPayload: consumes PipelineResult objects, persists
  detections, upserts vehicle track history, correlates against the
  watchlist, and constructs alert payloads for matched entities.
"""

from sentinel.alerts.alert_engine import AlertEngine, AlertPayload

__all__ = ["AlertEngine", "AlertPayload"]
