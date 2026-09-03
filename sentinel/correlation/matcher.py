"""
sentinel/correlation/matcher.py
==================================

Watchlist correlation: matches recognized license plate text against
the active vehicle watchlist.

Design notes:
- Plate normalization strips all non-alphanumeric characters and
  uppercases the result, mirroring the normalization already performed
  in sentinel.detection.ocr.PlateOCR, so that formatting differences
  between OCR output and watchlist data entry never cause a false
  negative match.
- The lookup query is fully parameterized and relies on the
  idx_watchlist_plate partial index (entity_type = 'VEHICLE' AND
  active = TRUE) defined in schema.sql for O(log n) lookup performance
  on the real-time ingest path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from sentinel.correlation.db import Database
from sentinel.detection.pipeline import PipelineResult

logger = logging.getLogger(__name__)


@dataclass
class WatchlistMatch:
    """
    Represents a single watchlist hit resulting from a plate match.

    Attributes
    ----------
    watchlist_id : str
        UUID (as string) of the matched watchlist row.
    flag_type : str
        One of STOLEN, WANTED, SUSPECT, BLACKLISTED.
    entity_type : str
        Entity type of the matched watchlist row (expected 'VEHICLE').
    description : str
        Free-text description of why the entity is flagged.
    matched_on : str
        The normalized plate text that produced the match.
    """

    watchlist_id: str
    flag_type: str
    entity_type: str
    description: str
    matched_on: str


def _normalize_plate(raw_text: str) -> str:
    """
    Strips all non-alphanumeric characters and uppercases the result.
    """
    return re.sub(r"[^A-Za-z0-9]", "", raw_text).upper()


class WatchlistMatcher:
    """
    Matches PipelineResult plate reads against the active vehicle
    watchlist.

    Parameters
    ----------
    db : Database
        Connected Database instance used to perform the watchlist
        lookup query.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def match(self, result: PipelineResult) -> Optional[WatchlistMatch]:
        """
        Normalizes result.plate_text and runs an exact-match lookup
        against active, vehicle-type watchlist rows.

        Returns None if the result carries no plate text, if
        normalization yields an empty string, or if no active
        watchlist row matches.
        """
        if not result.plate_text:
            return None

        normalized = _normalize_plate(result.plate_text)
        if not normalized:
            return None

        pool = self.db._require_pool()

        row = await pool.fetchrow(
            """
            SELECT id, flag_type, entity_type, description
            FROM watchlist
            WHERE entity_type = 'VEHICLE'
              AND active = TRUE
              AND plate_text = $1
            LIMIT 1
            """,
            normalized,
        )

        if row is None:
            return None

        match = WatchlistMatch(
            watchlist_id=str(row["id"]),
            flag_type=row["flag_type"],
            entity_type=row["entity_type"],
            description=row["description"] or "",
            matched_on=normalized,
        )
        logger.info(
            "Watchlist match: plate=%s flag_type=%s watchlist_id=%s",
            normalized,
            match.flag_type,
            match.watchlist_id,
        )
        return match
