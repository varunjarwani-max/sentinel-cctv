"""
Sentinel Correlation Package
===============================

Exposes the persistence and watchlist correlation primitives used by the
Sentinel alerting backbone:

- Database: asyncpg connection-pool wrapper providing detection logging,
  alert logging, vehicle track upsert, and read-side query helpers.
- WatchlistMatcher / WatchlistMatch: normalizes and matches recognized
  plate text against the active watchlist.
"""

from sentinel.correlation.db import Database
from sentinel.correlation.matcher import WatchlistMatcher, WatchlistMatch

__all__ = ["Database", "WatchlistMatcher", "WatchlistMatch"]
