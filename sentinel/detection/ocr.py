"""
sentinel/detection/ocr.py
============================

EasyOCR-backed Indian license plate recognition.

Design notes:
- preprocess() applies a grayscale + bilateral-filter + Otsu-threshold
  pipeline, which is a standard, well-behaved preprocessing chain for
  license plate OCR: bilateral filtering suppresses noise while
  preserving plate character edges, and Otsu thresholding produces a
  clean binary image robust to lighting variation across CCTV feeds.
- is_likely_plate() validates candidate OCR text against the standard
  Indian vehicle registration format, plus the newer Bharat (BH) series
  format, to suppress false-positive OCR reads from non-plate text in
  the crop (bumper stickers, reflections, etc.).
- read_plate() enforces a minimum per-candidate OCR confidence of 0.5
  and returns the highest-confidence valid candidate, or None if no
  candidate clears both the confidence and format bars.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

import cv2
import easyocr
import numpy as np

logger = logging.getLogger(__name__)

# Standard Indian vehicle registration format, e.g. "GJ01AB1234".
_STANDARD_PLATE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,2}[0-9]{4}$")

# Bharat (BH) series format introduced 2021, e.g. "22BH1234AB".
_BH_SERIES_PLATE_RE = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

# Minimum EasyOCR confidence for a candidate to be considered at all.
_MIN_OCR_CONFIDENCE = 0.5


class PlateOCR:
    """
    Wraps an EasyOCR reader configured for English-language license
    plate text, with preprocessing and Indian plate format validation.

    Parameters
    ----------
    gpu : bool
        Whether to run EasyOCR inference on GPU. Defaults to False for
        edge-device / CPU-only deployment.
    """

    def __init__(self, gpu: bool = False) -> None:
        logger.info("Initializing EasyOCR reader (gpu=%s)...", gpu)
        self.reader = easyocr.Reader(["en"], gpu=gpu)

    def preprocess(self, crop: np.ndarray) -> np.ndarray:
        """
        Applies a grayscale -> bilateral filter -> Otsu threshold
        pipeline to a vehicle/plate crop, producing a binary image
        optimized for OCR.

        Returns the original crop unchanged (as a safe no-op) if the
        crop is empty or otherwise malformed.
        """
        if crop is None or crop.size == 0:
            logger.debug("Received empty crop in PlateOCR.preprocess(); skipping.")
            return crop

        if crop.ndim == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop

        filtered = cv2.bilateralFilter(gray, 11, 17, 17)

        _, thresholded = cv2.threshold(
            filtered, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        return thresholded

    def is_likely_plate(self, text: str) -> bool:
        """
        Returns True if the given (already normalized, uppercase,
        alphanumeric-only) text matches either the standard Indian
        registration format or the Bharat (BH) series format.
        """
        if not text:
            return False
        return bool(
            _STANDARD_PLATE_RE.match(text) or _BH_SERIES_PLATE_RE.match(text)
        )

    def _normalize(self, raw_text: str) -> str:
        """
        Strips all non-alphanumeric characters and uppercases the
        result, so that OCR artifacts like spaces, hyphens, or
        misread punctuation don't break format validation.
        """
        return re.sub(r"[^A-Za-z0-9]", "", raw_text).upper()

    def read_plate(self, crop: np.ndarray) -> Optional[str]:
        """
        Runs OCR against a preprocessed version of `crop` and returns
        the highest-confidence normalized plate string that:
          1. Has OCR confidence > 0.5, and
          2. Matches an Indian plate format via is_likely_plate().

        Returns None if no candidate clears both bars, or if the crop
        is empty/invalid.
        """
        if crop is None or crop.size == 0:
            return None

        preprocessed = self.preprocess(crop)
        if preprocessed is None or preprocessed.size == 0:
            return None

        try:
            raw_results = self.reader.readtext(preprocessed)
        except Exception:
            logger.error("EasyOCR inference failed on plate crop.", exc_info=True)
            return None

        candidates: List[Tuple[str, float]] = []

        for entry in raw_results:
            try:
                _, text, confidence = entry
            except (ValueError, TypeError):
                logger.debug("Skipping malformed EasyOCR result entry: %r", entry)
                continue

            confidence = float(confidence)
            if confidence <= _MIN_OCR_CONFIDENCE:
                continue

            normalized = self._normalize(str(text))
            if not self.is_likely_plate(normalized):
                continue

            candidates.append((normalized, confidence))

        if not candidates:
            return None

        best_text, best_confidence = max(candidates, key=lambda c: c[1])
        logger.debug(
            "Selected plate candidate '%s' with confidence %.3f", best_text, best_confidence
        )
        return best_text
